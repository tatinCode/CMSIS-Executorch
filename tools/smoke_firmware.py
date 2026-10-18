"""Materialize and build an isolated E8-HP CMSIS smoke target. Never flashes."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from tools import inference as common


DEVICE = "AE822FA0E5597LS0_M55_HP"
TARGET = "DevKit-E8-HP"
ROOT = common.ROOT
WORKSPACE = common.WORK / "firmware"
APPLICATION = ROOT / "firmware" / "smoke"
VENDOR = ROOT / "firmware" / "vendor" / "alif_viewfinder"
WHOLE_LIBRARIES = ("executorch", "executorch_core", "executorch_delegate_ethos_u", "cortex_m_ops_lib")
SUPPORT_LIBRARIES = ("cortex_m_kernels", "portable_ops_lib", "portable_kernels", "quantized_ops_lib",
                     "quantized_kernels", "kernels_util_all_deps", "cmsis-nn", "flatccrt", "ethosu_core_driver")


def read_json(path):
    return json.loads(Path(path).read_text())


def check_artifacts(runtime, model):
    runtime_meta = read_json(runtime / "manifest.json")
    model_meta = read_json(model / "manifest.json")
    for meta in (runtime_meta, model_meta):
        if meta.get("executorch_revision") != common.REVISION:
            raise common.SetupError("Model/runtime revision mismatch; regenerate matching artifacts.")
    if runtime_meta.get("cpu") != "cortex-m55" or runtime_meta.get("npu") != common.TARGET:
        raise common.SetupError("Runtime is not configured for Cortex-M55 / U85-256.")
    if model_meta.get("model") != "smoke" or model_meta.get("target") != common.TARGET:
        raise common.SetupError("Only the U85-256 smoke model is accepted by this target.")
    if model_meta.get("memory_mode") != "Sram_Only":
        raise common.SetupError("The smoke target requires a Sram_Only model.")
    required = {f"lib{name}.a" for name in WHOLE_LIBRARIES + SUPPORT_LIBRARIES}
    if not required.issubset(runtime_meta.get("libraries", {})):
        raise common.SetupError("Runtime manifest lacks libraries required by the smoke target.")
    for name, digest in runtime_meta["libraries"].items():
        if Path(name).name != name or common.sha256(runtime / "lib" / name) != digest:
            raise common.SetupError(f"Runtime archive differs from its manifest: {name}")
    if common.sha256(model / "smoke_ethosu85_256.pte") != model_meta["pte_sha256"]:
        raise common.SetupError("Smoke model hash differs from its manifest.")
    if (model / "smoke_ethosu85_256.pte").stat().st_size != model_meta["pte_bytes"]:
        raise common.SetupError("Smoke model byte count differs from its manifest.")
    if common.sha256(model / "program_metadata.json") != model_meta["metadata_sha256"]:
        raise common.SetupError("Smoke metadata differs from its manifest.")
    metadata = read_json(model / "program_metadata.json")
    from tools.inspect_reference_pte import validate_methods
    validate_methods(metadata["methods"], "smoke")
    forward = next(method for method in metadata["methods"] if method["name"] == "forward")
    if len(forward["planned_buffer_bytes"]) > 4 or sum(
        (size + 31) & ~31 for size in forward["planned_buffer_bytes"]
    ) > 4096:
        raise common.SetupError("Smoke model exceeds the firmware's bounded planned arena.")
    return runtime_meta, model_meta


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise common.SetupError(f"SDK linker layout changed; refusing an unchecked patch: {old!r}")
    return text.replace(old, new, 1)


def smoke_linker(text):
    # Preserve the SDK's vectors, copy/zero tables, heap, stack and constructors.
    # Execute general runtime code from MRAM instead of overflowing 256 KiB ITCM.
    text = replace_once(text, "    *(.text*)", "    *(.text.handgpt_itcm*)")
    text = replace_once(text, "    *(.rodata*)", "    *(.text*)\n    *(.rodata*)")
    text = replace_once(text, "  .bss.at_sram0 (NOLOAD) : ALIGN(8)\n  {",
                        "  .bss.at_sram0 (NOLOAD) : ALIGN(32)\n  {\n    *(.bss.handgpt_npu)")
    return text


def configure_workspace(pack, runtime, model):
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "manifest.json").unlink(missing_ok=True)
    device = WORKSPACE / "RTE" / "Device" / DEVICE
    device.mkdir(parents=True, exist_ok=True)
    baseline = VENDOR / "viewfinder" / "RTE" / "Device" / DEVICE
    for name in ("RTE_Device.h", "app_tz.h", "core_config.h", "app_mem_regions.h"):
        source = baseline / name
        if not source.is_file():
            raise common.SetupError(f"Missing camera baseline configuration: {source}")
        shutil.copy2(source, device / name)
    memory = (device / "app_mem_regions.h").read_text()
    for name, value in (("APP_HP_STACK_SIZE", "0x00008000"), ("APP_HP_HEAP_SIZE", "0x00020000")):
        memory, count = re.subn(rf"(#define\s+{name}\s+)0x[0-9a-fA-F]+", rf"\g<1>{value}", memory)
        if count != 1:
            raise common.SetupError(f"Missing or ambiguous memory setting: {name}")
    (device / "app_mem_regions.h").write_text(memory)
    board = WORKSPACE / "RTE" / "BSP" / DEVICE
    board.mkdir(parents=True, exist_ok=True)
    for name in ("board_defs.h", "gpios.h", "pins.h"):
        shutil.copy2(VENDOR / "board" / "devkit-e8" / "RTE" / "BSP" / DEVICE / name, board / name)
    original_linker = pack / "Device" / "core" / "rtss_hp" / "linker" / "linker_gnu_mram.ld.src"
    (device / original_linker.name).write_text(smoke_linker(original_linker.read_text()))
    shutil.copy2(VENDOR / "License.txt", WORKSPACE / "Alif-License.txt")

    # Regenerate the header from the validated PTE rather than trusting a stale header.
    assets = WORKSPACE / "assets"
    assets.mkdir(exist_ok=True)
    common.emit_model_header(model / "smoke_ethosu85_256.pte", assets / "reference_model_pte.h")

    components = [
        "ARM::CMSIS:CORE", "AlifSemiconductor::Device:Startup",
        "AlifSemiconductor::Device:SOC Peripherals:PINCONF",
        "AlifSemiconductor::Device:SOC Peripherals:GPIO",
        "AlifSemiconductor::CMSIS Driver:USART",
        "AlifSemiconductor::Device:SOC Peripherals:MHU",
        "AlifSemiconductor::BSP:Board Config",
        "AlifSemiconductor::BSP:DevKit Config&DevKit-e8",
        "AlifSemiconductor::Services:Secure Enclave:core&Source",
        "AlifSemiconductor::Services:Secure Enclave:Initialization Helper&Source",
    ]
    links = ["-Wl,--start-group", "-Wl,--whole-archive"]
    links += [str(runtime / "lib" / f"lib{name}.a") for name in WHOLE_LIBRARIES]
    links += ["-Wl,--no-whole-archive"]
    links += [str(runtime / "lib" / f"lib{name}.a") for name in SUPPORT_LIBRARIES]
    links += ["-Wl,--end-group"]
    solution = {"solution": {
        "created-for": "CMSIS-Toolbox@2.15.0", "cdefault": None, "compiler": "GCC@13.3.1",
        "packs": [{"pack": "AlifSemiconductor::Ensemble@2.2.0"}, {"pack": "ARM::CMSIS@6.1.0"}],
        "target-types": [{"type": TARGET,
                          "device": "Alif Semiconductor::AE822FA0E5597LS0:M55_HP",
                          "board": "Alif Semiconductor::DevKit-E8",
                          "target-set": [{"set": None, "images": [{"project-context": "smoke.debug"}]}]}],
        "build-types": [{"type": "debug", "optimize": "balanced", "debug": "on"}],
        "projects": [{"project": "smoke.cproject.yml"}],
    }}
    project = {"project": {
        "groups": [{"group": "Smoke", "files": [{"file": os.path.relpath(APPLICATION / "src" / name, WORKSPACE)}
                    for name in ("main.cpp", "platform.cpp", "model_runner.cpp")]}],
        "components": [{"component": name} for name in components],
        "output": {"base-name": "smoke", "type": ["elf", "bin", "map"]},
        "define": ["C10_USING_CUSTOM_GENERATED_MACROS", "ETHOSU85"],
        "add-path": [os.path.relpath(path, WORKSPACE) for path in (
            APPLICATION / "include", assets, runtime / "include",
            runtime / "include" / "executorch/runtime/core/portable_type/c10")],
        "linker": [{"script": str(Path("RTE") / "Device" / DEVICE / original_linker.name), "for-compiler": "GCC"}],
        "misc": [{"for-compiler": "GCC", "Link": links}],
    }}
    default = {"default": {"misc": [{"for-compiler": "GCC",
        "C": ["-std=c99", "-fshort-enums", "-fdata-sections", "-ffunction-sections"],
        "CPP": ["-std=c++17", "-fshort-enums", "-fno-exceptions", "-fno-rtti", "-fno-unwind-tables", "-fdata-sections", "-ffunction-sections", "-Wall", "-Wextra", "-Werror"],
        "Link": ["--specs=nosys.specs", "--entry=Reset_Handler",
                 f"-Wl,-Map={WORKSPACE / 'smoke.map'},--cref,--print-memory-usage,--gc-sections,--no-warn-rwx-segments"],
        "Library": ["-lm", "-lc", "-lgcc"],
    }]}}
    for name, data in (("smoke.csolution.yml", solution), ("smoke.cproject.yml", project), ("cdefault.yml", default)):
        (WORKSPACE / name).write_text(json.dumps(data, indent=2) + "\n")
    return original_linker


def find_cbuild(explicit=None):
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise common.SetupError(f"cbuild not found: {candidate}")
    found = shutil.which("cbuild")
    if found:
        return Path(found).resolve()
    candidates = sorted((Path.home() / ".vscode" / "extensions").glob(
        "arm.cmsis-csolution-*/tools/cmsis-toolbox/bin/cbuild"))
    if candidates:
        return candidates[-1]
    raise common.SetupError("CMSIS cbuild not found; pass --cbuild /path/to/cbuild.")


def parse_symbols(text):
    symbols = {}
    for line in text.splitlines():
        fields = line.split(maxsplit=3)
        if len(fields) == 4 and re.fullmatch(r"[0-9a-fA-F]+", fields[0]):
            symbols[fields[3]] = (int(fields[0], 16), int(fields[1], 16), fields[2])
        elif len(fields) == 3 and re.fullmatch(r"[0-9a-fA-F]+", fields[0]):
            symbols[fields[2]] = (int(fields[0], 16), 0, fields[1])
    return symbols


def verify_arm_abi(attributes):
    enum_tags = {line.split(":", 1)[1].strip() for line in attributes.splitlines() if "Tag_ABI_enum_size:" in line}
    float_tags = {line.split(":", 1)[1].strip() for line in attributes.splitlines() if "Tag_ABI_VFP_args:" in line}
    if enum_tags != {"small"} or float_tags != {"VFP registers"}:
        raise common.SetupError("Expected the pinned runtime's small-enum / hard-float ARM ABI.")


def verify_layout(symbols, binary, model_size):
    for name in ("IRQ366_Handler", "SysTick_Handler", "HardFault_Handler", "_write", "et_pal_current_ticks"):
        if name not in symbols or symbols[name][2].upper() != "T":
            raise common.SetupError(f"Missing strong platform implementation: {name}")
    if "g_smoke_status" not in symbols or symbols["g_smoke_status"][1] != 60:
        raise common.SetupError("Diagnostic status record is missing or has an unexpected ABI.")
    model = symbols.get("reference_model_pte")
    arena = symbols.get("g_smoke_npu_arena")
    temporary = symbols.get("g_smoke_npu_temporary_pool")
    if not model or model[1] != model_size or model[0] % 16 or not (0x80200000 <= model[0] < model[0] + model[1] <= 0x80400000):
        raise common.SetupError("Model is not aligned and fully inside the HP application MRAM region.")
    if not arena or arena[1] != 4096 or arena[0] % 32 or not (0x02000000 <= arena[0] < arena[0] + arena[1] <= 0x02400000):
        raise common.SetupError("NPU arena is not aligned and fully inside SRAM0.")
    if not temporary or temporary[1] != 32 * 1024 or temporary[0] % 32 or not (
        0x02000000 <= temporary[0] < temporary[0] + temporary[1] <= 0x02400000
    ):
        raise common.SetupError("NPU temporary/scratch pool is not aligned and fully inside SRAM0.")
    if arena[0] < temporary[0] + temporary[1] and temporary[0] < arena[0] + arena[1]:
        raise common.SetupError("NPU planned arena overlaps the temporary/scratch pool.")
    table = symbols.get("__Vectors") or symbols.get("__VECTOR_TABLE")
    if not table or table[0] != 0x80200000 or len(binary) < (16 + 367) * 4:
        raise common.SetupError("Unexpected boot/vector-table layout.")
    for index, name in ((15, "SysTick_Handler"), (16 + 366, "IRQ366_Handler")):
        actual = struct.unpack_from("<I", binary, index * 4)[0]
        if actual != (symbols[name][0] | 1):
            raise common.SetupError(f"Vector table does not point at the strong {name}.")
    heap_start = symbols.get("__end__", (0,))[0]
    heap_limit = symbols.get("__HeapLimit", (0,))[0]
    stack_limit = symbols.get("__StackLimit", (0,))[0]
    stack_top = symbols.get("__StackTop", (0,))[0]
    if not (0x20000000 <= heap_start < heap_limit <= stack_limit < stack_top <= 0x20100000):
        raise common.SetupError("Unexpected or overlapping DTCM heap/stack layout.")
    return {"model_address": hex(model[0]), "model_bytes": model[1],
            "arena_address": hex(arena[0]), "arena_bytes": arena[1],
            "temporary_pool_address": hex(temporary[0]), "temporary_pool_bytes": temporary[1],
            "status_address": hex(symbols["g_smoke_status"][0]),
            "u85_irq": 366, "vector_table_verified": True,
            "heap_bytes": heap_limit - heap_start, "stack_bytes": stack_top - stack_limit,
            "heap_to_stack_gap_bytes": stack_limit - heap_limit}


def build(args):
    common.require_source()
    (WORKSPACE / "manifest.json").unlink(missing_ok=True)
    runtime = common.WORK / "runtime-cortex-m55"
    model = common.WORK / "models" / "smoke"
    runtime_meta, model_meta = check_artifacts(runtime, model)
    driver_source = common.SOURCE / "backends" / "arm" / "third-party" / "ethos-u-core-driver"
    if common.run(["git", "rev-parse", "HEAD"], cwd=driver_source, capture=True) != runtime_meta["driver_revision"]:
        raise common.SetupError("Driver source revision differs from the compiled driver manifest.")
    for name in ("ethosu_driver.h", "ethosu_types.h", "pmu_ethosu.h"):
        if common.sha256(runtime / "include" / name) != common.sha256(driver_source / "include" / name):
            raise common.SetupError(f"Installed driver header differs from its pinned source: {name}")
    common.check_space(1)
    compiler = common.find_toolchain(args.toolchain_bin)
    if common.run([compiler / "arm-none-eabi-gcc", "-dumpfullversion"], capture=True) != runtime_meta["compiler"]:
        raise common.SetupError("Firmware compiler differs from the runtime compiler.")
    for name in ("ethosu_core_driver", "executorch_delegate_ethos_u"):
        verify_arm_abi(common.run([compiler / "arm-none-eabi-readelf", "-A", runtime / "lib" / f"lib{name}.a"], capture=True))
    cmsis = common.find_cmsis()
    pack_root = cmsis.parents[2]
    pack = pack_root / "AlifSemiconductor" / "Ensemble" / "2.2.0"
    cbuild = find_cbuild(args.cbuild)
    linker = configure_workspace(pack, runtime, model)
    env = common.build_environment(args.jobs, compiler)
    env["PATH"] = str(cbuild.parent) + os.pathsep + env["PATH"]
    env["CMSIS_COMPILER_ROOT"] = str(cbuild.parent.parent / "etc")
    env["CMSIS_PACK_ROOT"] = str(pack_root)
    env["GCC_TOOLCHAIN_13_3_1"] = str(compiler)
    command = [cbuild, "smoke.csolution.yml", "--context", "smoke.debug+" + TARGET,
               "--update-rte", "--jobs", args.jobs]
    if args.rebuild:
        command.append("--rebuild")
    common.run(command, cwd=WORKSPACE, env=env)
    candidates = list((WORKSPACE / "out").rglob("smoke.elf"))
    if len(candidates) != 1:
        raise common.SetupError("Expected exactly one smoke ELF after building.")
    elf = candidates[0]
    verify_arm_abi(common.run([compiler / "arm-none-eabi-readelf", "-A", elf], capture=True))
    binary = elf.with_suffix(".bin")
    symbols = parse_symbols(common.run([compiler / "arm-none-eabi-nm", "-S", "-C", "--defined-only", elf], capture=True))
    layout = verify_layout(symbols, binary.read_bytes(), model_meta["pte_bytes"])
    report = {"target": TARGET, "build": "debug", "hardware_verified": False,
              "executorch_revision": common.REVISION, "driver_revision": runtime_meta["driver_revision"],
              "model_sha256": model_meta["pte_sha256"],
              "elf": str(elf.relative_to(ROOT)), "elf_sha256": common.sha256(elf),
              "bin": str(binary.relative_to(ROOT)), "bin_sha256": common.sha256(binary),
              "sdk_linker_sha256": common.sha256(linker), "layout": layout,
              "arm_abi": {"enums": "small", "floating_point": "hard"},
              "viewfinder_revision": common.run(["git", "rev-parse", "HEAD"], cwd=VENDOR, capture=True),
              "generator_sha256": common.sha256(Path(__file__)),
              "source_sha256": {str(path.relative_to(ROOT)): common.sha256(path)
                                for path in sorted(APPLICATION.rglob("*"))
                                if path.suffix in (".cpp", ".h")},
              "configuration_sha256": {str(path.relative_to(WORKSPACE)): common.sha256(path)
                                       for path in sorted((WORKSPACE / "RTE").rglob("*"))
                                       if path.suffix == ".h" or path.name == "linker_gnu_mram.ld.src"}}
    (WORKSPACE / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print("BUILD/LAYOUT CHECKS PASSED. No board was programmed. Hardware acceptance is still required.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=common.positive_jobs, default=1)
    parser.add_argument("--cbuild")
    parser.add_argument("--toolchain-bin")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild only the generated smoke firmware workspace")
    args = parser.parse_args()
    try:
        common.prepare_work()
        with (common.WORK / "command.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            build(args)
    except (common.SetupError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
