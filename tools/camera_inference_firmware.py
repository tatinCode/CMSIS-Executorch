"""Build an isolated HP camera/display + stored-image coexistence test. Never programs a board."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

from tools import inference as common
from tools import memory_preflight as reports
from tools import prepare_stored_image as accepted
from tools import smoke_firmware as smoke
from tools import stored_image_firmware as image


ROOT = common.ROOT
# Do not overwrite the working v3 full-resolution build or its package pointer.
WORKSPACE = common.WORK / "camera-inference-fast-firmware"
APPLICATION = ROOT / "firmware/camera_inference"
NAME = "camera-inference"
SRAM1_BASE, SRAM1_END = 0x02400000, 0x02800000
CAMERA_BYTES, LCD_BYTES = 480 * 480 * 3, 2 * 480 * 800 * 2
CAPTURE = dict(protocol=4, wire_version=2, width=128, height=128, payload_bytes=49152,
               source_width=480, source_height=480, crop="none", resize="PIL_bilinear_22bit")
PERIPHERAL_IRQS = {333: "CDC_SCANLINE0_IRQHandler", 343: "DSI_IRQHandler", 344: "CSI_IRQHandler",
                   345: "CAM_IRQHandler", 367: "ISP_IRQHandler", 368: "ISP_MI_IRQHandler"}
CAMERA_COMPONENTS = (
    "AlifSemiconductor::Device:SOC Peripherals:CDC", "AlifSemiconductor::Device:SOC Peripherals:CPI",
    "AlifSemiconductor::Device:SOC Peripherals:MIPI DSI CSI2 DPHY", "AlifSemiconductor::Device:SOC Peripherals:MIPI CSI2",
    "AlifSemiconductor::Device:SOC Peripherals:MIPI DSI", "AlifSemiconductor::Device:SOC Peripherals:ISP",
    "AlifSemiconductor::BSP:External peripherals:ILI9806E LCD panel",
    "AlifSemiconductor::BSP:External peripherals:CAMERA Sensor MT9M114", "AlifSemiconductor::CMSIS Driver:I2C",
)


def preview_linker(text):
    text = smoke.smoke_linker(text)
    # SRAM1 may not be powered until the SE run-profile request in main. These
    # POD buffers are explicitly initialised in preview_init, never at startup.
    return smoke.replace_once(text, "  .bss (NOLOAD) : ALIGN(8)",
        "  .bss.at_sram1 (NOLOAD) : ALIGN(32)\n  {\n    *(.bss.handgpt_preview)\n    . = ALIGN(32);\n  } > SRAM1\n\n  .bss (NOLOAD) : ALIGN(8)")


def platform_port(text):
    # Private generated copy only. Existing smoke/stored-image ports and hashes
    # are unchanged. The single shared UART/tick/IRQ/cache/syscall owner remains.
    text = smoke.replace_once(text, '#include "smoke_platform.h"', '#include "smoke_platform.h"\n#include "preview.h"')
    text = smoke.replace_once(text, "    se_services_port_init();",
                              "    se_services_port_init();\n    if (!preview_power_init()) { return false; }")
    # Boot-triggered export needs no host-to-board UART input. The exact
    # cause of the earlier stalls remains unproven. Use the unchanged smoke
    # UART initialization and callback; enable no RX and parse no requests.
    return text


def configure(pack, runtime):
    device = WORKSPACE / "RTE/Device" / smoke.DEVICE
    board = WORKSPACE / "RTE/BSP" / smoke.DEVICE
    device.mkdir(parents=True, exist_ok=True)
    board.mkdir(parents=True, exist_ok=True)
    baseline = smoke.VENDOR / "viewfinder/RTE/Device" / smoke.DEVICE
    for name in ("RTE_Device.h", "app_tz.h", "core_config.h"):
        shutil.copy2(baseline / name, device / name)
    (device / "app_mem_regions.h").write_text(image.candidate_memory((baseline / "app_mem_regions.h").read_text()))
    for name in ("board_defs.h", "gpios.h", "pins.h"):
        shutil.copy2(smoke.VENDOR / "board/devkit-e8/RTE/BSP" / smoke.DEVICE / name, board / name)
    sdk_linker = pack / "Device/core/rtss_hp/linker/linker_gnu_mram.ld.src"
    (device / sdk_linker.name).write_text(preview_linker(sdk_linker.read_text()))
    generated = WORKSPACE / "generated"
    generated.mkdir(exist_ok=True)
    port = generated / "platform.cpp"
    port.write_text(platform_port((ROOT / "firmware/smoke/src/platform.cpp").read_text()))
    shutil.copy2(smoke.VENDOR / "License.txt", WORKSPACE / "Alif-License.txt")
    assets = WORKSPACE / "assets"
    assets.mkdir(exist_ok=True)
    for name in ("reference_model_pte.h", "reference_input.h", "reference_logits.h", "image_config.h"):
        shutil.copy2(image.WORKSPACE / "assets" / name, assets / name)
    sources = [APPLICATION / "src" / name for name in ("main.cpp", "power.cpp", "preview.cpp", "frame_export.cpp")]
    sources += [ROOT / "firmware/stored_image/src/model_runner.cpp", port]
    sources += [smoke.VENDOR / "viewfinder/camera" / name for name in ("isp_calibration.c", "isp_calibration_mt9m114.c")]
    relative = lambda path: os.path.relpath(path, WORKSPACE)
    links = ["-Wl,--start-group", "-Wl,--whole-archive"]
    links += [str(runtime / "lib" / f"lib{name}.a") for name in smoke.WHOLE_LIBRARIES]
    links += ["-Wl,--no-whole-archive"]
    links += [str(runtime / "lib" / f"lib{name}.a") for name in smoke.SUPPORT_LIBRARIES]
    links += ["-Wl,--end-group"]
    project = {"project": {
        "groups": [{"group": "Coexistence", "files": [{"file": relative(path)} for path in sources]}],
        "components": [{"component": name} for name in (*image.COMPONENTS, *CAMERA_COMPONENTS)],
        "output": {"base-name": NAME, "type": ["elf", "bin", "map"]},
        "define": ["C10_USING_CUSTOM_GENERATED_MACROS", "ETHOSU85", "ENSEMBLE_SOC_GEN2"],
        "add-path": [relative(path) for path in (APPLICATION / "include", ROOT / "firmware/smoke/include",
                     ROOT / "firmware/stored_image/include", assets, smoke.VENDOR / "viewfinder/camera",
                     runtime / "include", runtime / "include/executorch/runtime/core/portable_type/c10")],
        "linker": [{"script": relative(device / sdk_linker.name), "for-compiler": "GCC"}],
        "misc": [{"for-compiler": "GCC", "Link": links}],
    }}
    solution = {"solution": {
        "created-for": "CMSIS-Toolbox@2.15.0", "cdefault": None, "compiler": "GCC@13.3.1",
        "packs": [{"pack": "AlifSemiconductor::Ensemble@2.2.0"}, {"pack": "ARM::CMSIS@6.1.0"}],
        "target-types": [{"type": smoke.TARGET, "device": "Alif Semiconductor::AE822FA0E5597LS0:M55_HP",
                          "board": "Alif Semiconductor::DevKit-E8",
                          "target-set": [{"set": None, "images": [{"project-context": NAME + ".debug"}]}]}],
        "build-types": [{"type": "debug", "optimize": "balanced", "debug": "on"}],
        "projects": [{"project": NAME + ".cproject.yml"}],
    }}
    default = {"default": {"misc": [{"for-compiler": "GCC",
        "C": ["-std=c99", "-fshort-enums", "-fdata-sections", "-ffunction-sections"],
        "CPP": ["-std=c++17", "-fshort-enums", "-fno-exceptions", "-fno-rtti", "-fno-unwind-tables", "-fdata-sections", "-ffunction-sections", "-Wall", "-Wextra", "-Werror"],
        "Link": ["--specs=nosys.specs", "--entry=Reset_Handler",
                 f"-Wl,-Map={WORKSPACE / (NAME + '.map')},--cref,--print-memory-usage,--gc-sections,--no-warn-rwx-segments"],
        "Library": ["-lm", "-lc", "-lgcc"],
    }]}}
    for name, data in ((NAME + ".cproject.yml", project), (NAME + ".csolution.yml", solution), ("cdefault.yml", default)):
        (WORKSPACE / name).write_text(json.dumps(data, indent=2) + "\n")
    return sources, sdk_linker


def verify_layout(symbols, binary, model_bytes):
    result = image.verify_layout(symbols, binary, model_bytes)
    kernel = symbols.get("handgpt::kResizeKernels")
    if (not kernel or kernel[1] != 128 * 36 or kernel[2] not in ("V", "R", "r") or
            not image.PROFILE.mram_base <= kernel[0] < kernel[0] + kernel[1] <= image.PROFILE.mram_base + len(binary)):
        raise common.SetupError("Fast capture resize coefficients must be read-only in MRAM")
    result["camera_resize_kernels"] = {"address": hex(kernel[0]), "bytes": kernel[1]}
    spans = []
    for name, size in (("g_preview_rgb", CAMERA_BYTES), ("g_preview_lcd", LCD_BYTES)):
        symbol = symbols.get(name)
        if not symbol or symbol[1] != size or symbol[2] != "B" or symbol[0] % 32 or not SRAM1_BASE <= symbol[0] < symbol[0] + size <= SRAM1_END:
            raise common.SetupError(f"Invalid shared SRAM1 camera/display buffer: {name}")
        spans.append((symbol[0], symbol[0] + size))
        result[name] = {"address": hex(symbol[0]), "bytes": size}
    spans.sort()
    if spans[1][0] < spans[0][1]:
        raise common.SetupError("Camera/display buffers overlap")
    begin = symbols["__zero_table_start__"][0] - image.PROFILE.mram_base
    end = symbols["__zero_table_end__"][0] - image.PROFILE.mram_base
    zeros = [(address, address + words * 4) for address, words in struct.iter_unpack("<II", binary[begin:end])]
    if any(a < SRAM1_END and b > SRAM1_BASE for a, b in zeros):
        raise common.SetupError("Startup must not touch SRAM1 before its run-profile power request")
    for irq, name in PERIPHERAL_IRQS.items():
        symbol = symbols.get(name)
        if not symbol or symbol[2] != "T" or not image.PROFILE.mram_base <= symbol[0] < image.PROFILE.mram_base + len(binary):
            raise common.SetupError(f"Missing/misplaced strong camera/display IRQ {irq}")
        if struct.unpack_from("<I", binary, (irq + 16) * 4)[0] != (symbol[0] | 1):
            raise common.SetupError(f"Camera/display vector mismatch {irq}")
    if any(name in symbols for name in ("d0_heap", "tracelib_init", "clk_init", "d2_init")):
        raise common.SetupError("Unexpected graphics heap or competing UART/tick owner linked")
    result.update(sram1_reserved_bytes=CAMERA_BYTES + LCD_BYTES,
                  sram1_headroom=SRAM1_END - SRAM1_BASE - CAMERA_BYTES - LCD_BYTES,
                  camera_display_vectors_verified=True,
                  sram1_initialization="explicit in preview_init after power profile; excluded from startup zero table")
    return result


def build(args):
    if args.layout != image.PROFILE.name:
        raise common.SetupError("Explicit --layout hp-only-large is required")
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "manifest.json").unlink(missing_ok=True)
    source_image = args.image.expanduser().resolve(strict=True)
    prerequisite = accepted.check_build(source_image)
    baseline_meta = reports.read_json(image.WORKSPACE / "manifest.json")
    common.check_space(1)
    compiler = common.find_toolchain(args.toolchain_bin)
    runtime_meta = reports.read_json(common.WORK / "runtime-cortex-m55/manifest.json")
    if common.run([compiler / "arm-none-eabi-gcc", "-dumpfullversion"], capture=True) != runtime_meta["compiler"]:
        raise common.SetupError("Coexistence compiler does not match the accepted runtime")
    pack = common.find_cmsis().parents[2] / "AlifSemiconductor/Ensemble/2.2.0"
    runtime = common.WORK / "runtime-cortex-m55"
    # The ISP archive has the same small-enum/hard-float ABI as the NPU runtime.
    isp_library = pack / "libs/isp/libisp_gcc.a"
    smoke.verify_arm_abi(common.run([compiler / "arm-none-eabi-readelf", "-A", isp_library], capture=True))
    sources, linker = configure(pack, runtime)
    asset_hashes = {str(path.relative_to(WORKSPACE)): common.sha256(path) for path in (WORKSPACE / "assets").glob("*.h")}
    config_hashes = {str(path.relative_to(WORKSPACE)): common.sha256(path) for path in (WORKSPACE / "RTE").rglob("*")
                     if path.is_file() and not ".base@" in path.name}
    inputs = sources + list(APPLICATION.glob("include/*.h")) + list((ROOT / "firmware/smoke/include").glob("*.h"))
    inputs += list((ROOT / "firmware/stored_image/include").glob("*.h"))
    inputs += [ROOT / "firmware/smoke/src/platform.cpp", smoke.VENDOR / "viewfinder/camera/isp_calibration.h"]
    before = {str(path.relative_to(ROOT)): common.sha256(path) for path in inputs}
    cbuild = smoke.find_cbuild(args.cbuild)
    env = common.build_environment(args.jobs, compiler)
    env.update(PATH=str(cbuild.parent) + os.pathsep + env["PATH"], CMSIS_COMPILER_ROOT=str(cbuild.parent.parent / "etc"),
               CMSIS_PACK_ROOT=str(pack.parents[2]), GCC_TOOLCHAIN_13_3_1=str(compiler))
    command = [cbuild, NAME + ".csolution.yml", "--context", NAME + ".debug+" + smoke.TARGET, "--update-rte", "--jobs", args.jobs]
    if args.rebuild: command.append("--rebuild")
    common.run(command, cwd=WORKSPACE, env=env)
    candidates = list((WORKSPACE / "out").rglob(NAME + ".elf"))
    if len(candidates) != 1: raise common.SetupError("Expected exactly one coexistence ELF")
    elf = candidates[0]
    binary = elf.with_suffix(".bin")
    smoke.verify_arm_abi(common.run([compiler / "arm-none-eabi-readelf", "-A", elf], capture=True))
    symbols = smoke.parse_symbols(common.run([compiler / "arm-none-eabi-nm", "-S", "-C", "--defined-only", elf], capture=True))
    data = binary.read_bytes()
    layout = verify_layout(symbols, data, baseline_meta["layout"]["reference_model_pte"]["bytes"])
    original = prerequisite["binary"].read_bytes()
    for name in ("reference_model_pte", "reference_input", "reference_logits"):
        address, size, _ = symbols[name]
        old_offset = int(baseline_meta["layout"][name]["address"], 16) - image.PROFILE.mram_base
        offset = address - image.PROFILE.mram_base
        if data[offset:offset + size] != original[old_offset:old_offset + size]:
            raise common.SetupError(f"Coexistence embedded {name} differs from the accepted stored-image build")
    if (accepted.check_build(source_image) != prerequisite or any(common.sha256(ROOT / name) != digest for name, digest in before.items())
            or any(common.sha256(WORKSPACE / name) != digest for name, digest in {**asset_hashes, **config_hashes}.items())):
        raise common.SetupError("Build/prerequisite inputs changed during compilation")
    compile_commands = json.loads((elf.parent / "compile_commands.json").read_text())
    if not isinstance(compile_commands, list):
        raise common.SetupError("Expected a compilation-command array for SDK provenance")
    pack_inputs = {isp_library, linker}
    for entry in compile_commands:
        path = Path(entry["file"])
        if not path.is_absolute(): path = Path(entry["directory"]) / path
        path = path.resolve()
        if path.is_relative_to(pack): pack_inputs.add(path)
    # Bind the public driver/ISP interfaces and SoC/MPU definitions, not only
    # the opaque archive version. These fingerprints are provenance for review,
    # not a claim of independent ISP implementation verification.
    for folder in ("Alif_CMSIS/Include", "libs/isp/inc", "Device/core/common/include", "Device/soc/AE822FA0E5597/include"):
        pack_inputs.update((pack / folder).rglob("*.h"))
    manifest = {
        "target": NAME + ".debug+" + smoke.TARGET, "layout_profile": image.PROFILE.report(),
        "elf": str(elf.relative_to(ROOT)), "elf_sha256": common.sha256(elf),
        "bin": str(binary.relative_to(ROOT)), "bin_sha256": common.sha256(binary),
        "layout": layout, "stored_image_build_manifest_sha256": prerequisite["build_manifest_sha256"],
        "model_sha256": baseline_meta["model_sha256"], "reference_hashes": baseline_meta["reference_hashes"],
        "executorch_revision": common.REVISION, "driver_revision": baseline_meta["driver_revision"],
        "comparison": baseline_meta["comparison"], "source_sha256": before,
        "generator_sha256": common.sha256(Path(__file__)), "sdk_linker_sha256": common.sha256(linker),
        "compile_commands_sha256": common.sha256(elf.parent / "compile_commands.json"),
        "tool_sha256": {name: common.sha256(ROOT / "tools" / name) for name in (
            "prepare_stored_image.py", "program_smoke.py", "stored_image_firmware.py", "firmware_layouts.py")},
        "isp_library_sha256": common.sha256(isp_library), "vendor_revision": common.run(["git", "rev-parse", "HEAD"], cwd=smoke.VENDOR, capture=True),
        "pack_input_sha256": {str(path.relative_to(pack)): common.sha256(path) for path in sorted(pack_inputs)},
        "generated_sha256": {str(path.relative_to(WORKSPACE)): common.sha256(path) for folder in ("assets", "RTE", "generated")
                             for path in sorted((WORKSPACE / folder).rglob("*")) if path.is_file()},
        "project_sha256": {name: common.sha256(WORKSPACE / name) for name in (NAME + ".csolution.yml", NAME + ".cproject.yml", "cdefault.yml")},
        "hardware_verified": False, "programming_authorized": False, "camera_classification_implemented": False,
        "capture": CAPTURE,
        "scope": "CPU preview; camera paused during stored-image self-test and boot-triggered CRC32 full-view 128x128 PIL-bilinear frame export; display scanout remains active; no GPU, live camera inference, deep sleep, or host-to-board UART input",
    }
    reports.write_report(WORKSPACE / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2))
    print("COEXISTENCE BUILD/LAYOUT CHECKS PASSED. No board programmed. Hardware validation is still required.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--layout", choices=[image.PROFILE.name], required=True)
    parser.add_argument("--image", type=Path, required=True, help="Original image used by the accepted stored-image build")
    parser.add_argument("--jobs", type=common.positive_jobs, default=1)
    parser.add_argument("--cbuild")
    parser.add_argument("--toolchain-bin")
    parser.add_argument("--rebuild", action="store_true")
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
