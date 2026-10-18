"""Build/revalidate the separate experimental live ASL HP image. Never flashes."""

import argparse
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import struct
import subprocess

from tools import camera_inference_firmware as camera
from tools import inference as common
from tools import inspect_asl_pte as inspector
from tools import memory_preflight as reports
from tools import prepare_camera_inference as capture
from tools import prepare_stored_image as accepted
from tools import program_smoke as shared
from tools import asl_selftest
from tools.asl_contract import STATIC_CLASSES, class_header


ROOT = common.ROOT
WORKSPACE = common.WORK / "asl-live-firmware-v7"
APPLICATION = ROOT / "firmware/asl_live"
PROFILE = camera.image.PROFILE
BUILD_TARGET = "camera-inference.debug+DevKit-E8-HP"
LIVE = dict(experimental=True, deployment_approved=False, threshold=0.8, threshold_calibrated=False,
            classes=list(STATIC_CLASSES), screen_rotation_degrees=180,
            camera_input_rotation_degrees=0, boot_selftest="ASL exact-input; three repeats",
            boot_frame_export=False, negative_display="--", consecutive_predictions=3,
            stale_ms=1000, inference_fault="latched until reset; clear letter; keep preview",
            selftest_gate_revision=2, selftest_gate_changed_after_hardware_failure=True,
            selftest_failure_display="SELFTEST FAIL + numeric code; known-inactive buffer only")


def preview_variant(text):
    text = camera.smoke.replace_once(text, '#include "preview_pixels.h"',
        '#include "preview_pixels.h"\n#include "live_asl.h"\n#include "live_display.h"\n#include "camera_rotation.h"')
    text = camera.smoke.replace_once(text, "    const unsigned next = active_lcd ^ 1u;",
        "    // One observation per complete frozen RGB frame, never the LCD pixels.\n"
        "    live_asl_predict(g_preview_rgb, sizeof(g_preview_rgb));\n"
        "    const unsigned next = active_lcd ^ 1u;")
    text = camera.smoke.replace_once(text, "bool ready;", "bool ready;\nbool live_flip_uncertain;")
    needle = "    if (!check(Driver_CDC200.Control(CDC200_FRAMEBUF_UPDATE_VSYNC,"
    text = camera.smoke.replace_once(text, needle,
        "    live_flip_uncertain = true; // Never reuse either LCD buffer after an ambiguous flip.\n" + needle)
    text = camera.smoke.replace_once(text, "    active_lcd = next;",
        "    active_lcd = next;\n    live_flip_uncertain = false;")
    needle = "    clean(g_preview_lcd[next], sizeof(g_preview_lcd[next]));"
    text = camera.smoke.replace_once(text, needle,
        "    if (!handgpt::draw_live_asl_panel(g_preview_lcd[next], 480 * 800, live_asl_letter(smoke_millis()))) { return false; }\n"
        "    if (!handgpt::rotate_lcd180(g_preview_lcd[next], 480 * 800)) { return false; }\n" + needle)
    return text + '''

// Clear stale letters without touching the camera allocation, even after a
// capture failure. Only the inactive LCD buffer is modified. Display failures
// cannot be repaired or guaranteed blank by a software-only path.
static bool live_asl_failure_display(uint32_t code, bool selftest) {
    if (!ready || display_error || live_flip_uncertain) { return false; }
    const unsigned next = active_lcd ^ 1u;
    std::memset(g_preview_lcd[next], 0, sizeof(g_preview_lcd[next]));
    const bool drawn = selftest ? handgpt::draw_selftest_failure(g_preview_lcd[next], 480 * 800, code) :
                       handgpt::draw_live_asl_panel(g_preview_lcd[next], 480 * 800, handgpt::AslLetter::None);
    if (!drawn ||
        !handgpt::rotate_lcd180(g_preview_lcd[next], 480 * 800)) { return false; }
    clean(g_preview_lcd[next], sizeof(g_preview_lcd[next]));
    live_flip_uncertain = true;
    if (!check(Driver_CDC200.Control(CDC200_FRAMEBUF_UPDATE_VSYNC,
               reinterpret_cast<uintptr_t>(g_preview_lcd[next])), "blank display")) { return false; }
    const uint32_t before = scanlines, start = smoke_millis();
    while (static_cast<uint32_t>(scanlines - before) < 2u) {
        if (display_error || handgpt::elapsed(start, smoke_millis(), kDisplayLimitMs)) { return false; }
        __WFI();
    }
    active_lcd = next;
    live_flip_uncertain = false;
    return true;
}
bool live_asl_blank_display() { return live_asl_failure_display(0, false); }
bool live_asl_selftest_failure(uint32_t code) { return live_asl_failure_display(code, true); }
'''


def export_info(directory, inspect_binary=False):
    directory = Path(directory).resolve(strict=True)
    manifest = reports.read_json(directory / "manifest.json")
    if (manifest.get("schema") != 1 or manifest.get("classes") != LIVE["classes"] or
        manifest.get("target") != common.TARGET or manifest.get("memory_mode") != "Sram_Only" or
        manifest.get("executorch_revision") != common.REVISION or manifest.get("deployment_approved") is not False or
        manifest.get("hardware_verified") is not False or manifest.get("final_test_evaluated") is not False):
        raise common.SetupError("Expected a completed, explicitly experimental static-26 ASL export")
    files = manifest["artifact_sha256"]
    actual = {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file() and p.name != "manifest.json"}
    accepted.identities(files, actual, directory)
    for key in files:
        shared.inside(directory / key, directory)
    metadata = reports.read_json(directory / "program_metadata.json")
    inspector.validate_methods(metadata["methods"], manifest["classes"])
    reference = reports.read_json(directory / "reference/reference.json")
    gate = asl_selftest.validate_policy(manifest, reference)
    if reference.get("classes") != manifest["classes"] or len(reference.get("host_logits", [])) != len(STATIC_CLASSES):
        raise common.SetupError("Reference class order/count differs from model")
    if metadata.get("output_scale") != gate["output_scale"]:
        raise common.SetupError("Serialized PTE output scale differs from the self-test policy")
    if metadata["pte_sha256"] != manifest["pte_sha256"] or common.sha256(directory / "asl_ethosu85_256.pte") != manifest["pte_sha256"]:
        raise common.SetupError("ASL export PTE identities disagree")
    memory = inspector.memory_report(metadata, reports.read_json(common.WORK / "models/mv2/program_metadata.json"), manifest["classes"])
    if memory != manifest["memory"] or not memory["fits_existing_runtime_pools"]:
        raise common.SetupError("ASL does not fit the unreduced runtime reservations")
    config = reports.read_json(directory / "configuration.json")
    if (config["threshold"] != 0.8 or config.get("classes") != manifest["classes"] or
            config.get("preprocessing") != manifest.get("preprocessing")):
        raise common.SetupError("ASL threshold/classes/preprocessing changed or disagree")
    for name, digest in config["input_sha256"].items():
        if common.sha256(Path(name)) != digest:
            raise common.SetupError(f"ASL calibration/checkpoint/source changed: {name}")
    if inspect_binary:
        code = ("import json,sys; from tools.inspect_asl_pte import inspect; "
                "from tools.asl_contract import STATIC_CLASSES; print(json.dumps(inspect(sys.argv[1], STATIC_CLASSES)))")
        result = subprocess.run([str(common.PYTHON), "-B", "-c", code, str(directory / "asl_ethosu85_256.pte")],
                                cwd=ROOT, env=common.build_environment(1), capture_output=True, text=True, check=True)
        if json.loads(result.stdout) != metadata:
            raise common.SetupError("Fresh serialized ASL inspection disagrees with recorded metadata")
    return manifest


def reference_bytes(directory):
    directory = Path(directory)
    raw = (directory / "reference/input.f32").read_bytes()
    logits = reports.read_float_npy(directory / "reference/quantized-host-logits.npy", (1, len(STATIC_CLASSES)))
    if len(raw) != 3 * 128 * 128 * 4 or any(not math.isfinite(x) for x, in struct.iter_unpack("<f", raw)):
        raise common.SetupError("Invalid exact ASL reference input")
    if any(not math.isfinite(x) for x, in struct.iter_unpack("<f", logits)):
        raise common.SetupError("Invalid ASL reference logits")
    # Intentionally keep the original reference/pool extents, padding unused
    # elements with zero. This retains every existing layout/startup check.
    return {"reference_model_pte": (directory / "asl_ethosu85_256.pte").read_bytes(),
            "reference_input": raw + bytes(602112 - len(raw)),
            "reference_logits": logits + bytes(4000 - len(logits))}


def write_float_header(path, symbol, data):
    values = [x for x, in struct.iter_unpack("<f", data)]
    lines = ["#pragma once", "// ASL reference first; zero padding preserves the reviewed allocation extent.",
             f"alignas(16) static const float {symbol}[] = {{"]
    for offset in range(0, len(values), 8):
        lines.append("    " + ", ".join(camera.image.image_tool.float_literal(x) for x in values[offset:offset + 8]) + ",")
    path.write_text("\n".join([*lines, "};", ""]))


def gate_header(gate):
    asl_selftest.validate_policy({"selftest_gate": gate}, {"selftest_gate": gate})
    return ("#pragma once\n// Post-hoc gate revision approved after first hardware failure.\n"
            "namespace handgpt {\n"
            f"constexpr unsigned kReferenceGateRevision = {gate['revision']};\n"
            f"constexpr double kReferenceOutputScale = {gate['output_scale']!r};\n"
            f"constexpr unsigned kReferenceMaxOutputSteps = {gate['max_output_steps']};\n"
            f"constexpr double kReferenceMinimumCosine = {gate['min_cosine']!r};\n"
            f"constexpr float kReferenceThreshold = {gate['threshold']!r}f;\n"
            "}\n")


def configure(pack, runtime, directory):
    # Reuse only a private configuration generator; no working globals/pointers
    # are changed and the base build() is deliberately NOT called.
    spec = importlib.util.spec_from_file_location("tools._live_camera_config", camera.__file__)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    generator.WORKSPACE = WORKSPACE
    sources, linker = generator.configure(pack, runtime)
    generated = WORKSPACE / "generated/preview.cpp"
    generated.write_text(preview_variant((camera.APPLICATION / "src/preview.cpp").read_text()))
    replacements = {camera.APPLICATION / "src/main.cpp": APPLICATION / "src/main.cpp",
                    camera.APPLICATION / "src/preview.cpp": generated,
                    ROOT / "firmware/stored_image/src/model_runner.cpp": APPLICATION / "src/model_runner.cpp"}
    removed = camera.APPLICATION / "src/frame_export.cpp"
    project_path = WORKSPACE / "camera-inference.cproject.yml"
    project = reports.read_json(project_path)
    for group in project["project"]["groups"]:
        entries = []
        for entry in group["files"]:
            path = (WORKSPACE / entry["file"]).resolve()
            if path == removed:
                continue
            entry["file"] = os.path.relpath(replacements.get(path, path), WORKSPACE)
            entries.append(entry)
        group["files"] = entries
    project["project"]["add-path"] += [os.path.relpath(APPLICATION / "include", WORKSPACE),
                                        os.path.relpath(ROOT / "firmware/camera_overlay/include", WORKSPACE)]
    project_path.write_text(json.dumps(project, indent=2) + "\n")
    assets = WORKSPACE / "assets"
    generated_classes = class_header(STATIC_CLASSES)
    if (APPLICATION / "include/asl_classes.h").read_text() != generated_classes:
        raise common.SetupError("Checked-in ASL class header differs from the export contract")
    (assets / "asl_classes.h").write_text(generated_classes)
    common.emit_model_header(directory / "asl_ethosu85_256.pte", assets / "reference_model_pte.h")
    data = reference_bytes(directory)
    for name in ("reference_input", "reference_logits"):
        write_float_header(assets / (name + ".h"), name, data[name])
    gate = reports.read_json(directory / "manifest.json")["selftest_gate"]
    (assets / "reference_gate.h").write_text(gate_header(gate))
    config = (assets / "image_config.h").read_text()
    baseline = reports.read_json(camera.image.WORKSPACE / "manifest.json")
    config = config.replace(baseline["model_sha256"], common.sha256(directory / "asl_ethosu85_256.pte"))
    # Rewrite hash macros explicitly; never depend on a particular reference hash layout.
    import re
    config = re.sub(r'(#define IMAGE_INPUT_SHA256 )"[^"]+"',
                    lambda m: m[1] + json.dumps(common.sha256(directory / "reference/input.f32")), config)
    (assets / "image_config.h").write_text(config)
    return [replacements.get(p, p) for p in sources if p != removed], linker


def source_paths():
    paths = {Path(__file__), ROOT / "tools/inspect_asl_pte.py", ROOT / "tools/asl_selftest.py", ROOT / "tools/asl_contract.py", camera.APPLICATION / "src/preview.cpp",
             camera.APPLICATION / "src/power.cpp", ROOT / "firmware/smoke/src/platform.cpp"}
    for folder in (APPLICATION, camera.APPLICATION / "include", ROOT / "firmware/smoke/include",
                   ROOT / "firmware/stored_image/include", ROOT / "firmware/camera_overlay/include"):
        paths.update(folder.rglob("*.h"))
        paths.update(folder.rglob("*.cpp"))
    paths.update(WORKSPACE / "generated" / n for n in ("preview.cpp", "platform.cpp"))
    paths.update(camera.smoke.VENDOR / "viewfinder/camera" / n for n in (
        "isp_calibration.c", "isp_calibration_mt9m114.c", "isp_calibration.h"))
    return paths


def check_build(export_manifest):
    meta = reports.read_json(WORKSPACE / "manifest.json")
    if (meta.get("target") != BUILD_TARGET or meta.get("live") != LIVE or
        meta.get("experimental_acknowledged") is not True or meta.get("layout_profile") != PROFILE.report() or
        any(meta.get(n) is not False for n in ("hardware_verified", "programming_authorized", "deployment_approved"))):
        raise common.SetupError("Expected the separately acknowledged experimental LIVE ASL build")
    directory = Path(export_manifest).resolve(strict=True).parent
    export = export_info(directory, inspect_binary=True)
    if (meta.get("target") != BUILD_TARGET or meta.get("live") != LIVE or
        meta.get("experimental_acknowledged") is not True or meta.get("layout_profile") != PROFILE.report() or
        meta.get("export_manifest") != str(directory / "manifest.json") or
        meta.get("export_manifest_sha256") != common.sha256(directory / "manifest.json") or
        any(meta.get(n) is not False for n in ("hardware_verified", "programming_authorized", "deployment_approved"))):
        raise common.SetupError("Expected the separately acknowledged experimental LIVE ASL build")
    accepted.identities(meta["source_sha256"], {str(p.relative_to(ROOT)) for p in source_paths()}, ROOT)
    accepted.identities(meta["helper_sha256"], {"camera_inference_firmware.py", "stored_image_firmware.py",
        "smoke_firmware.py", "inference.py", "memory_preflight.py", "firmware_layouts.py", "prepare_stored_image.py"}, ROOT / "tools")
    for name, convert, original in (("preview.cpp", preview_variant, camera.APPLICATION / "src/preview.cpp"),
                                    ("platform.cpp", camera.platform_port, ROOT / "firmware/smoke/src/platform.cpp")):
        if (WORKSPACE / "generated" / name).read_text() != convert(original.read_text()):
            raise common.SetupError("Generated live source differs from reviewed transformation")
    generated = {str(p.relative_to(WORKSPACE)) for folder in ("assets", "RTE", "generated")
                 for p in (WORKSPACE / folder).rglob("*") if p.is_file()}
    accepted.identities(meta["generated_sha256"], generated, WORKSPACE)
    if (WORKSPACE / "assets/reference_gate.h").read_text() != gate_header(export["selftest_gate"]):
        raise common.SetupError("Generated ASL gate differs from exported reference policy")
    if ((WORKSPACE / "assets/asl_classes.h").read_text() != class_header(export["classes"]) or
            (APPLICATION / "include/asl_classes.h").read_text() != class_header(export["classes"])):
        raise common.SetupError("Generated/checked-in class orders differ from the model")
    accepted.identities(meta["project_sha256"], capture.PROJECTS, WORKSPACE)
    binary = WORKSPACE / "out/camera-inference/DevKit-E8-HP/debug/camera-inference.bin"
    elf, commands = binary.with_suffix(".elf"), binary.parent / "compile_commands.json"
    if (meta["bin"] != str(binary.relative_to(ROOT)) or meta["elf"] != str(elf.relative_to(ROOT)) or
        common.sha256(binary) != meta["bin_sha256"] or common.sha256(elf) != meta["elf_sha256"] or
        common.sha256(commands) != meta["compile_commands_sha256"]):
        raise common.SetupError("Live binary/ELF/compile identities changed")
    compiled = []
    for entry in json.loads(commands.read_text()):
        path = Path(entry["file"])
        compiled.append((path if path.is_absolute() else Path(entry["directory"]) / path).resolve())
    required = {APPLICATION / "src/main.cpp", APPLICATION / "src/model_runner.cpp",
                WORKSPACE / "generated/preview.cpp", WORKSPACE / "generated/platform.cpp"}
    if any(compiled.count(p.resolve()) != 1 for p in required) or any(
        p in compiled for p in ((camera.APPLICATION / "src/main.cpp").resolve(),
                                (ROOT / "firmware/stored_image/src/model_runner.cpp").resolve(),
                                (camera.APPLICATION / "src/frame_export.cpp").resolve())):
        raise common.SetupError("Live target compiled the wrong main/runner/export sources")
    camera.image.check_runtime(common.WORK / "runtime-cortex-m55")
    if accepted.check_build(Path(meta["baseline_image"]))["build_manifest_sha256"] != meta["baseline_manifest_sha256"]:
        raise common.SetupError("Stored-image fallback prerequisite changed")
    compiler = common.find_toolchain(None)
    pack = common.find_cmsis().parents[2] / "AlifSemiconductor/Ensemble/2.2.0"
    pack_paths = {p for p in compiled if p.is_relative_to(pack)} | {
        pack / "libs/isp/libisp_gcc.a", pack / "Device/core/rtss_hp/linker/linker_gnu_mram.ld.src"}
    for folder in ("Alif_CMSIS/Include", "libs/isp/inc", "Device/core/common/include", "Device/soc/AE822FA0E5597/include"):
        pack_paths.update((pack / folder).rglob("*.h"))
    accepted.identities(meta["pack_input_sha256"], {str(p.relative_to(pack)) for p in pack_paths}, pack)
    if common.run(["git", "rev-parse", "HEAD"], cwd=camera.smoke.VENDOR, capture=True) != meta["vendor_revision"]:
        raise common.SetupError("Viewfinder revision changed")
    for path in (elf, pack / "libs/isp/libisp_gcc.a"):
        camera.smoke.verify_arm_abi(common.run([compiler / "arm-none-eabi-readelf", "-A", path], capture=True))
    symbols = camera.smoke.parse_symbols(common.run([compiler / "arm-none-eabi-nm", "-S", "-C", "--defined-only", elf], capture=True))
    data = binary.read_bytes()
    layout = camera.verify_layout(symbols, data, export["pte_bytes"])
    if layout != meta["layout"]:
        raise common.SetupError("Fresh live memory/vector/startup layout changed")
    for name, expected in reference_bytes(directory).items():
        address, size, _ = symbols[name]
        offset = address - PROFILE.mram_base
        if size != len(expected) or data[offset:offset + size] != expected:
            raise common.SetupError(f"Embedded ASL {name} differs from exact exported bytes")
    return dict(binary=binary, binary_sha256=meta["bin_sha256"], binary_bytes=len(data),
                build_manifest_sha256=common.sha256(WORKSPACE / "manifest.json"), image=str(directory / "manifest.json"))


def build(args):
    if not args.experimental or args.jobs != 1 or args.layout != PROFILE.name:
        raise common.SetupError("Require --experimental --jobs 1 --layout hp-only-large")
    if WORKSPACE.exists():
        raise common.SetupError("Live workspace exists; retain it, do not overwrite")
    directory = args.export.resolve(strict=True)
    export = export_info(directory, inspect_binary=True)
    baseline_image = args.baseline_image.resolve(strict=True)
    prerequisite = accepted.check_build(baseline_image)
    runtime = common.WORK / "runtime-cortex-m55"
    runtime_meta = camera.image.check_runtime(runtime)
    compiler = common.find_toolchain(args.toolchain_bin)
    if common.run([compiler / "arm-none-eabi-gcc", "-dumpfullversion"], capture=True) != runtime_meta["compiler"]:
        raise common.SetupError("Compiler differs from accepted runtime")
    pack = common.find_cmsis().parents[2] / "AlifSemiconductor/Ensemble/2.2.0"
    WORKSPACE.mkdir(parents=True)
    configure(pack, runtime, directory)
    sources = {str(p.relative_to(ROOT)): common.sha256(p) for p in source_paths()}
    generated = {str(p.relative_to(WORKSPACE)): common.sha256(p) for folder in ("assets", "RTE", "generated")
                 for p in (WORKSPACE / folder).rglob("*") if p.is_file()}
    cbuild = camera.smoke.find_cbuild(args.cbuild)
    env = common.build_environment(1, compiler)
    env.update(PATH=str(cbuild.parent) + os.pathsep + env["PATH"], CMSIS_COMPILER_ROOT=str(cbuild.parent.parent / "etc"),
               CMSIS_PACK_ROOT=str(pack.parents[2]), GCC_TOOLCHAIN_13_3_1=str(compiler))
    common.run([cbuild, "camera-inference.csolution.yml", "--context", BUILD_TARGET, "--update-rte", "--jobs", "1"],
               cwd=WORKSPACE, env=env)
    binary = WORKSPACE / "out/camera-inference/DevKit-E8-HP/debug/camera-inference.bin"
    elf, commands = binary.with_suffix(".elf"), binary.parent / "compile_commands.json"
    symbols = camera.smoke.parse_symbols(common.run([compiler / "arm-none-eabi-nm", "-S", "-C", "--defined-only", elf], capture=True))
    layout = camera.verify_layout(symbols, binary.read_bytes(), export["pte_bytes"])
    if any(common.sha256(ROOT / n) != h for n, h in sources.items()) or any(common.sha256(WORKSPACE / n) != h for n, h in generated.items()):
        raise common.SetupError("Live build inputs changed while compiling")
    # CMSIS generates additional RTE files during --update-rte; bind them all.
    generated = {str(p.relative_to(WORKSPACE)): common.sha256(p) for folder in ("assets", "RTE", "generated")
                 for p in (WORKSPACE / folder).rglob("*") if p.is_file()}
    pack_paths = {pack / "libs/isp/libisp_gcc.a", pack / "Device/core/rtss_hp/linker/linker_gnu_mram.ld.src"}
    for entry in json.loads(commands.read_text()):
        path = Path(entry["file"])
        path = (path if path.is_absolute() else Path(entry["directory"]) / path).resolve()
        if path.is_relative_to(pack):
            pack_paths.add(path)
    for folder in ("Alif_CMSIS/Include", "libs/isp/inc", "Device/core/common/include", "Device/soc/AE822FA0E5597/include"):
        pack_paths.update((pack / folder).rglob("*.h"))
    meta = dict(target=BUILD_TARGET, live=LIVE, experimental_acknowledged=True, layout_profile=PROFILE.report(),
        export_manifest=str(directory / "manifest.json"), export_manifest_sha256=common.sha256(directory / "manifest.json"),
        baseline_image=str(baseline_image), baseline_manifest_sha256=prerequisite["build_manifest_sha256"],
        bin=str(binary.relative_to(ROOT)), elf=str(elf.relative_to(ROOT)), bin_sha256=common.sha256(binary), elf_sha256=common.sha256(elf),
        compile_commands_sha256=common.sha256(commands), source_sha256=sources, generated_sha256=generated,
        helper_sha256={n: common.sha256(ROOT / "tools" / n) for n in ("camera_inference_firmware.py", "stored_image_firmware.py",
            "smoke_firmware.py", "inference.py", "memory_preflight.py", "firmware_layouts.py", "prepare_stored_image.py")},
        project_sha256={n: common.sha256(WORKSPACE / n) for n in capture.PROJECTS},
        pack_input_sha256={str(p.relative_to(pack)): common.sha256(p) for p in pack_paths},
        vendor_revision=common.run(["git", "rev-parse", "HEAD"], cwd=camera.smoke.VENDOR, capture=True),
        layout=layout, hardware_verified=False, programming_authorized=False, deployment_approved=False)
    reports.write_report(WORKSPACE / "manifest.json", meta)
    try:
        check_build(directory / "manifest.json")
    except Exception:
        (WORKSPACE / "manifest.json").unlink(missing_ok=True)
        raise
    print("EXPERIMENTAL LIVE ASL BUILD/REVIEW PASSED. No package prepared or board accessed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--experimental", action="store_true")
    parser.add_argument("--layout", choices=[PROFILE.name], required=True)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--baseline-image", type=Path, default=ROOT / "ml/reference/images/cat.jpg")
    parser.add_argument("--cbuild")
    parser.add_argument("--toolchain-bin")
    args = parser.parse_args()
    with (common.WORK / "command.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        build(args)


if __name__ == "__main__":
    main()
