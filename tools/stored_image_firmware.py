"""Build a separate opt-in stored-image E8-HP target. No programming commands."""

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from tools import inference as common
from tools import memory_preflight as preflight
from tools import prepare_reference_image as image_tool
from tools import smoke_firmware as smoke
from tools.firmware_layouts import HP_ONLY_LARGE


ROOT = common.ROOT
WORKSPACE = common.WORK / "stored-image-firmware"
APPLICATION = ROOT / "firmware/stored_image"
PROFILE = HP_ONLY_LARGE
ARENA = 752640
TEMPORARY = 1572864
METHOD = 262144
COMPONENTS = (
    "ARM::CMSIS:CORE", "AlifSemiconductor::Device:Startup",
    "AlifSemiconductor::Device:SOC Peripherals:PINCONF", "AlifSemiconductor::Device:SOC Peripherals:GPIO",
    "AlifSemiconductor::CMSIS Driver:USART", "AlifSemiconductor::Device:SOC Peripherals:MHU",
    "AlifSemiconductor::BSP:Board Config", "AlifSemiconductor::BSP:DevKit Config&DevKit-e8",
    "AlifSemiconductor::Services:Secure Enclave:core&Source",
    "AlifSemiconductor::Services:Secure Enclave:Initialization Helper&Source",
)


def budgets():
    return dict(scratch_bytes=TEMPORARY, temporary_overhead_bytes=65536, arena_bytes=ARENA,
                sram_bytes=PROFILE.limits["sram_bytes"], mram_bytes=PROFILE.limits["mram_bytes"],
                firmware_mram_bytes=262144, other_mram_bytes=0, method_pool_bytes=METHOD,
                other_sram_bytes=0, other_dtcm_bytes=16384, heap_bytes=131072,
                stack_bytes=32768, dtcm_bytes=PROFILE.limits["dtcm_bytes"])


def candidate_memory(text):
    settings = {
        "APP_MRAM_HE_BASE": "0x80000000", "APP_MRAM_HE_SIZE": "0x00000000",
        "APP_MRAM_HP_BASE": "0x80001000", "APP_MRAM_HP_SIZE": "0x004FF000",
        "APP_MRAM_USER_BASE": "0x80500000", "APP_MRAM_USER_SIZE": "0x00080000",
        "APP_HP_STACK_SIZE": "0x00008000", "APP_HP_HEAP_SIZE": "0x00020000",
    }
    for name, value in settings.items():
        text, count = re.subn(rf"(#define\s+{name}\s+)0x[0-9a-fA-F]+", rf"\g<1>{value}", text)
        if count != 1:
            raise common.SetupError(f"Missing or ambiguous memory setting: {name}")
    if text.count("#define APP_CODE_MRAM_SIZE  (APP_MRAM_USER_BASE - APP_MRAM_HP_BASE)") != 1:
        raise common.SetupError("SDK code-region formula changed; review the layout before building.")
    return text


def check_runtime(runtime):
    meta = preflight.read_json(runtime / "manifest.json")
    if meta.get("executorch_revision") != common.REVISION or meta.get("cpu") != "cortex-m55" or meta.get("npu") != common.TARGET:
        raise common.SetupError("Stored-image target requires the pinned Cortex-M55/U85 runtime.")
    required = {f"lib{name}.a" for name in smoke.WHOLE_LIBRARIES + smoke.SUPPORT_LIBRARIES}
    if not required.issubset(meta.get("libraries", {})):
        raise common.SetupError("Runtime manifest lacks required archives.")
    for name, digest in meta["libraries"].items():
        if Path(name).name != name or common.sha256(runtime / "lib" / name) != digest:
            raise common.SetupError(f"Runtime archive changed: {name}")
    source = common.SOURCE / "backends/arm/third-party/ethos-u-core-driver"
    if common.run(["git", "rev-parse", "HEAD"], cwd=source, capture=True) != meta["driver_revision"]:
        raise common.SetupError("Runtime driver revision mismatch.")
    for name in ("ethosu_driver.h", "ethosu_types.h", "pmu_ethosu.h"):
        if common.sha256(runtime / "include" / name) != common.sha256(source / "include" / name):
            raise common.SetupError(f"Runtime driver header changed: {name}")
    return meta


def generate_assets(model, report, args):
    assets = WORKSPACE / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    common.emit_model_header(model / "mv2_ethosu85_256.pte", assets / "reference_model_pte.h")
    raw = (model / "reference/input.f32").read_bytes()
    image_tool.write_float_header([value for value, in struct.iter_unpack("<f", raw)], assets / "reference_input.h")
    golden = preflight.read_float_npy(model / "reference/host_logits.npy", (1, 1000))
    top = preflight.read_json(model / "reference/reference.json")["host_top5"]
    lines = ["#pragma once", "alignas(16) static const float reference_logits[] = {"]
    values = [value for value, in struct.iter_unpack("<f", golden)]
    for offset in range(0, len(values), 8):
        lines.append("    " + ", ".join(image_tool.float_literal(value) for value in values[offset:offset + 8]) + ",")
    lines += ["};", "static const unsigned reference_top5_indices[] = {" + ", ".join(str(item["index"]) for item in top) + "};",
              "static const char* const reference_top5_labels[] = {" + ", ".join(json.dumps(item["label"]) for item in top) + "};"]
    (assets / "reference_logits.h").write_text("\n".join(lines) + "\n")
    config = {
        "IMAGE_MRAM_BASE": "0x80001000u", "IMAGE_MRAM_END": "0x80500000u",
        "IMAGE_ARENA_BYTES": f"{ARENA}u", "IMAGE_TEMPORARY_BYTES": f"{TEMPORARY}u", "IMAGE_METHOD_BYTES": f"{METHOD}u",
        "IMAGE_MAX_LOGIT_ERROR": image_tool.float_literal(args.max_logit_error),
        "IMAGE_MIN_COSINE": repr(args.min_cosine), "IMAGE_DUMP_LOGITS": str(int(args.dump_logits)),
        "IMAGE_MODEL_SHA256": json.dumps(report["requirements"]["hashes"]["model"]),
        "IMAGE_INPUT_SHA256": json.dumps(report["requirements"]["hashes"]["input_f32"]),
    }
    (assets / "image_config.h").write_text("#pragma once\n" + "".join(f"#define {name} {value}\n" for name, value in config.items()))
    return assets


def configure(pack, runtime, assets):
    device = WORKSPACE / "RTE/Device" / smoke.DEVICE
    board = WORKSPACE / "RTE/BSP" / smoke.DEVICE
    device.mkdir(parents=True, exist_ok=True)
    board.mkdir(parents=True, exist_ok=True)
    baseline = smoke.VENDOR / "viewfinder/RTE/Device" / smoke.DEVICE
    for name in ("RTE_Device.h", "app_tz.h", "core_config.h"):
        shutil.copy2(baseline / name, device / name)
    (device / "app_mem_regions.h").write_text(candidate_memory((baseline / "app_mem_regions.h").read_text()))
    for name in ("board_defs.h", "gpios.h", "pins.h"):
        shutil.copy2(smoke.VENDOR / "board/devkit-e8/RTE/BSP" / smoke.DEVICE / name, board / name)
    linker = pack / "Device/core/rtss_hp/linker/linker_gnu_mram.ld.src"
    (device / linker.name).write_text(smoke.smoke_linker(linker.read_text()))
    shutil.copy2(smoke.VENDOR / "License.txt", WORKSPACE / "Alif-License.txt")
    relative = lambda path: os.path.relpath(path, WORKSPACE)
    links = ["-Wl,--start-group", "-Wl,--whole-archive"]
    links += [str(runtime / "lib" / f"lib{name}.a") for name in smoke.WHOLE_LIBRARIES]
    links += ["-Wl,--no-whole-archive"]
    links += [str(runtime / "lib" / f"lib{name}.a") for name in smoke.SUPPORT_LIBRARIES]
    links += ["-Wl,--end-group"]
    sources = [APPLICATION / "src/main.cpp", APPLICATION / "src/model_runner.cpp", ROOT / "firmware/smoke/src/platform.cpp"]
    project = {"project": {
        "groups": [{"group": "StoredImage", "files": [{"file": relative(path)} for path in sources]}],
        "components": [{"component": name} for name in COMPONENTS],
        "output": {"base-name": "stored-image", "type": ["elf", "bin", "map"]},
        "define": ["C10_USING_CUSTOM_GENERATED_MACROS", "ETHOSU85"],
        "add-path": [relative(path) for path in (APPLICATION / "include", ROOT / "firmware/smoke/include", assets,
                     runtime / "include", runtime / "include/executorch/runtime/core/portable_type/c10")],
        "linker": [{"script": relative(device / linker.name), "for-compiler": "GCC"}],
        "misc": [{"for-compiler": "GCC", "Link": links}],
    }}
    solution = {"solution": {
        "created-for": "CMSIS-Toolbox@2.15.0", "cdefault": None, "compiler": "GCC@13.3.1",
        "packs": [{"pack": "AlifSemiconductor::Ensemble@2.2.0"}, {"pack": "ARM::CMSIS@6.1.0"}],
        "target-types": [{"type": smoke.TARGET, "device": "Alif Semiconductor::AE822FA0E5597LS0:M55_HP",
                          "board": "Alif Semiconductor::DevKit-E8",
                          "target-set": [{"set": None, "images": [{"project-context": "stored-image.debug"}]}]}],
        "build-types": [{"type": "debug", "optimize": "balanced", "debug": "on"}],
        "projects": [{"project": "stored-image.cproject.yml"}],
    }}
    default = {"default": {"misc": [{"for-compiler": "GCC",
        "C": ["-std=c99", "-fshort-enums", "-fdata-sections", "-ffunction-sections"],
        "CPP": ["-std=c++17", "-fshort-enums", "-fno-exceptions", "-fno-rtti", "-fno-unwind-tables", "-fdata-sections", "-ffunction-sections", "-Wall", "-Wextra", "-Werror"],
        "Link": ["--specs=nosys.specs", "--entry=Reset_Handler",
                 f"-Wl,-Map={WORKSPACE / 'stored-image.map'},--cref,--print-memory-usage,--gc-sections,--no-warn-rwx-segments"],
        "Library": ["-lm", "-lc", "-lgcc"],
    }]}}
    for name, data in (("stored-image.cproject.yml", project), ("stored-image.csolution.yml", solution), ("cdefault.yml", default)):
        (WORKSPACE / name).write_text(json.dumps(data, indent=2) + "\n")
    return sources, linker


def verify_layout(symbols, binary, model_bytes):
    if len(binary) > PROFILE.limits["mram_bytes"] or len(binary) < 1984 or len(binary) % 16:
        raise common.SetupError("Invalid stored-image binary extent/alignment; tail or boot reservation at risk.")
    expected = {
        "reference_model_pte": (model_bytes, PROFILE.mram_base, PROFILE.mram_end, 16),
        "reference_input": (preflight.INPUT_BYTES, PROFILE.mram_base, PROFILE.mram_end, 16),
        "reference_logits": (preflight.OUTPUT_BYTES, PROFILE.mram_base, PROFILE.mram_end, 16),
        "g_image_arena": (ARENA, PROFILE.sram_base, PROFILE.sram_end, 32),
        "g_image_temporary": (TEMPORARY, PROFILE.sram_base, PROFILE.sram_end, 32),
        "g_image_input": (preflight.INPUT_BYTES, PROFILE.sram_base, PROFILE.sram_end, 32),
        "g_image_output": (preflight.OUTPUT_BYTES, PROFILE.sram_base, PROFILE.sram_end, 32),
        "g_image_method_pool": (METHOD, PROFILE.dtcm_base, PROFILE.dtcm_end, 32),
    }
    spans = []
    report = {}
    for name, (size, begin, end, alignment) in expected.items():
        symbol = symbols.get(name)
        if not symbol or symbol[1] != size or symbol[0] % alignment or not begin <= symbol[0] < symbol[0] + size <= end:
            raise common.SetupError(f"Invalid extent/placement for {name}")
        spans.append((symbol[0], symbol[0] + size, name))
        report[name] = {"address": hex(symbol[0]), "bytes": size}
        if begin == PROFILE.mram_base and symbol[0] + size > PROFILE.mram_base + len(binary):
            raise common.SetupError(f"{name} extends beyond the binary's load image")
    spans.sort()
    if any(right[0] < left[1] for left, right in zip(spans, spans[1:])):
        raise common.SetupError("Stored-image model/reference/pools overlap")
    table = symbols.get("__Vectors") or symbols.get("__VECTOR_TABLE")
    if not table or table[0] != PROFILE.mram_base:
        raise common.SetupError("Stored-image vector table does not start at 0x80001000")
    for name in ("IRQ366_Handler", "SysTick_Handler", "HardFault_Handler", "Reset_Handler", "_write", "et_pal_current_ticks"):
        if name not in symbols or symbols[name][2] != "T" or not PROFILE.mram_base <= symbols[name][0] < PROFILE.mram_base + len(binary):
            raise common.SetupError(f"Missing or misplaced strong platform symbol {name}")
    for index, name in ((1, "Reset_Handler"), (3, "HardFault_Handler"), (15, "SysTick_Handler"), (382, "IRQ366_Handler")):
        if struct.unpack_from("<I", binary, index * 4)[0] != (symbols[name][0] | 1):
            raise common.SetupError(f"Incorrect vector for {name}")
    heap_start, heap_end, stack_start, stack_end = [symbols.get(name, (0,))[0] for name in ("__end__", "__HeapLimit", "__StackLimit", "__StackTop")]
    if not PROFILE.dtcm_base <= heap_start < heap_end <= stack_start < stack_end <= PROFILE.dtcm_end:
        raise common.SetupError("Invalid heap/stack extent")
    if heap_end - heap_start != 131072 or stack_end - stack_start != 32768 or struct.unpack_from("<I", binary)[0] != stack_end:
        raise common.SetupError("Unexpected heap/stack sizes or initial MSP")
    if symbols["g_image_method_pool"][0] + METHOD > heap_start:
        raise common.SetupError("CPU method pool overlaps the heap")
    status = symbols.get("g_smoke_status")
    if not status or status[1] != 60 or not PROFILE.dtcm_base <= status[0] < status[0] + status[1] <= heap_start:
        raise common.SetupError("Missing or misplaced shared platform status record")
    report["platform_status"] = {"symbol": "g_smoke_status", "address": hex(status[0]), "bytes": status[1]}
    # Decode the actual startup zero table, so the SRAM pools cannot silently
    # move into an uninitialised/orphan linker section.
    zero_start = symbols.get("__zero_table_start__", (0,))[0] - PROFILE.mram_base
    zero_end = symbols.get("__zero_table_end__", (0,))[0] - PROFILE.mram_base
    if not 0 <= zero_start < zero_end <= len(binary) or (zero_end - zero_start) % 8:
        raise common.SetupError("Invalid startup zero table")
    zeros = [(address, address + words * 4) for address, words in struct.iter_unpack("<II", binary[zero_start:zero_end])]
    for name in ("g_image_arena", "g_image_temporary", "g_image_input", "g_image_output", "g_image_method_pool"):
        address, size, _ = symbols[name]
        if not any(begin <= address and address + size <= end for begin, end in zeros):
            raise common.SetupError(f"Startup does not zero {name}")
    copy_start = symbols.get("__copy_table_start__", (0,))[0] - PROFILE.mram_base
    copy_end = symbols.get("__copy_table_end__", (0,))[0] - PROFILE.mram_base
    if not 0 <= copy_start < copy_end <= len(binary) or (copy_end - copy_start) % 12:
        raise common.SetupError("Invalid startup copy table")
    copies = []
    for source, destination, words in struct.iter_unpack("<III", binary[copy_start:copy_end]):
        size = words * 4
        if not size:
            continue
        cpu_ram = (PROFILE.dtcm_base <= destination < destination + size <= heap_start or
                   0 <= destination < destination + size <= 0x40000)
        if not cpu_ram or not PROFILE.mram_base <= source < source + size <= PROFILE.mram_base + len(binary):
            raise common.SetupError("Startup copy source/destination is outside the linked image/CPU RAM")
        copies.append((destination, destination + size))
    if not any(begin <= status[0] and status[0] + status[1] <= end for begin, end in copies):
        raise common.SetupError("Startup does not initialise the platform status record")
    report.update(binary_bytes=len(binary), mram_headroom=PROFILE.mram_end - PROFILE.mram_base - len(binary),
                  non_asset_mram_bytes=len(binary) - model_bytes - preflight.INPUT_BYTES - preflight.OUTPUT_BYTES,
                  heap_bytes=heap_end - heap_start, stack_bytes=stack_end - stack_start,
                  heap_to_stack_gap_bytes=stack_start - heap_end, vectors_verified=True,
                  startup_zero_table_verified=True, startup_copy_table_verified=True)
    return report


def build(args):
    if args.layout != PROFILE.name:
        raise common.SetupError("Stored-image firmware requires explicit --layout hp-only-large")
    if not math.isfinite(args.max_logit_error) or args.max_logit_error < 0 or not math.isfinite(args.min_cosine) or not -1 <= args.min_cosine <= 1:
        raise common.SetupError("Invalid numerical-comparison limits")
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "manifest.json").unlink(missing_ok=True)
    common.require_source()
    common.check_space(1)
    model = common.WORK / "models/mv2"
    runtime = common.WORK / "runtime-cortex-m55"
    report = preflight.preflight(model, budgets(), Path(args.image).expanduser().resolve(), PROFILE.name)
    preflight.write_report(WORKSPACE / "preflight.json", report)
    if not report["fits"]:
        raise common.SetupError("Preflight blocked stored-image build: " + "; ".join(report["errors"]))
    meta = check_runtime(runtime)
    compiler = common.find_toolchain(args.toolchain_bin)
    if common.run([compiler / "arm-none-eabi-gcc", "-dumpfullversion"], capture=True) != meta["compiler"]:
        raise common.SetupError("Stored-image compiler does not match runtime")
    for name in ("ethosu_core_driver", "executorch_delegate_ethos_u"):
        smoke.verify_arm_abi(common.run([compiler / "arm-none-eabi-readelf", "-A", runtime / "lib" / f"lib{name}.a"], capture=True))
    assets = generate_assets(model, report, args)
    pack_root = common.find_cmsis().parents[2]
    sources, linker = configure(pack_root / "AlifSemiconductor/Ensemble/2.2.0", runtime, assets)
    cbuild = smoke.find_cbuild(args.cbuild)
    env = common.build_environment(args.jobs, compiler)
    env.update(PATH=str(cbuild.parent) + os.pathsep + env["PATH"],
               CMSIS_COMPILER_ROOT=str(cbuild.parent.parent / "etc"), CMSIS_PACK_ROOT=str(pack_root), GCC_TOOLCHAIN_13_3_1=str(compiler))
    command = [cbuild, "stored-image.csolution.yml", "--context", "stored-image.debug+" + smoke.TARGET, "--update-rte", "--jobs", args.jobs]
    if args.rebuild: command.append("--rebuild")
    common.run(command, cwd=WORKSPACE, env=env)
    candidates = list((WORKSPACE / "out").rglob("stored-image.elf"))
    if len(candidates) != 1: raise common.SetupError("Expected exactly one stored-image ELF")
    elf = candidates[0]
    binary = elf.with_suffix(".bin")
    smoke.verify_arm_abi(common.run([compiler / "arm-none-eabi-readelf", "-A", elf], capture=True))
    symbols = smoke.parse_symbols(common.run([compiler / "arm-none-eabi-nm", "-S", "-C", "--defined-only", elf], capture=True))
    layout = verify_layout(symbols, binary.read_bytes(), report["requirements"]["model_bytes"])
    # Make sure no input changed while the compiler was building its embedded copy.
    if preflight.preflight(model, budgets(), Path(args.image).expanduser().resolve(), PROFILE.name) != report:
        raise common.SetupError("Model/reference inputs changed during build")
    paths = sources + list(APPLICATION.glob("include/*.h")) + list((ROOT / "firmware/smoke/include").glob("*.h"))
    manifest = {
        "target": "stored-image.debug+" + smoke.TARGET, "layout_profile": PROFILE.report(),
        "executorch_revision": common.REVISION, "driver_revision": meta["driver_revision"],
        "elf": str(elf.relative_to(ROOT)), "elf_sha256": common.sha256(elf),
        "bin": str(binary.relative_to(ROOT)), "bin_sha256": common.sha256(binary),
        "model_sha256": report["requirements"]["hashes"]["model"], "reference_hashes": report["requirements"]["hashes"],
        "layout": layout, "comparison": {"max_abs_logit_error": args.max_logit_error, "min_cosine": args.min_cosine, "top1_must_match": True},
        "source_sha256": {str(path.relative_to(ROOT)): common.sha256(path) for path in paths},
        "generated_sha256": {str(path.relative_to(WORKSPACE)): common.sha256(path) for path in sorted(WORKSPACE.rglob("*"))
                             if path.is_file() and (path.is_relative_to(assets) or path.is_relative_to(WORKSPACE / "RTE"))},
        "project_sha256": {name: common.sha256(WORKSPACE / name) for name in
                           ("stored-image.csolution.yml", "stored-image.cproject.yml", "cdefault.yml", "preflight.json")},
        "generator_sha256": common.sha256(Path(__file__)), "layout_definitions_sha256": common.sha256(ROOT / "tools/firmware_layouts.py"),
        "tool_sha256": {name: common.sha256(ROOT / "tools" / name) for name in (
            "inference.py", "smoke_firmware.py", "memory_preflight.py", "inspect_reference_pte.py", "prepare_reference_image.py")},
        "sdk_linker_sha256": common.sha256(linker), "runtime_manifest_sha256": common.sha256(runtime / "manifest.json"),
        "hardware_verified": False, "programming_authorized": False,
        "programming_gate": "Confirm no HE/A32 firmware or other MRAM data needs preservation; generate/review a new matching boot package separately.",
    }
    preflight.write_report(WORKSPACE / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2))
    print("STORED-IMAGE BUILD/LAYOUT CHECKS PASSED. No board was programmed. Hardware verification remains required.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", choices=[PROFILE.name], required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--jobs", type=common.positive_jobs, default=1)
    parser.add_argument("--cbuild")
    parser.add_argument("--toolchain-bin")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--max-logit-error", type=float, default=1.0)
    parser.add_argument("--min-cosine", type=float, default=0.98)
    parser.add_argument("--dump-logits", action="store_true")
    args = parser.parse_args()
    try:
        common.prepare_work()
        with (common.WORK / "command.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            build(args)
    except (common.SetupError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
