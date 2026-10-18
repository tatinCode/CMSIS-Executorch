"""Prepare/review the separate TEST UI package. No serial access or writer command."""

import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from tools import camera_inference_firmware as camera
from tools import camera_overlay_firmware as overlay
from tools import inference as common
from tools import memory_preflight as reports
from tools import prepare_camera_inference as capture
from tools import prepare_stored_image as accepted
from tools import program_smoke as shared


ROOT = common.ROOT
FIRMWARE = overlay.WORKSPACE
PROGRAM = common.WORK / "camera-overlay-screen-programming"
PROFILE = camera.image.PROFILE
# The compiled project retains the camera name; the package identity is distinct.
BUILD_TARGET = "camera-inference.debug+DevKit-E8-HP"
TARGET = "camera-overlay-screen-test-ui.debug+DevKit-E8-HP"
IMAGE_NAME = "handgpt-camera-overlay-screen-test-ui.bin"
CONFIG_NAME = "handgpt-camera-overlay-screen-test-ui.json"
OVERLAY = overlay.OVERLAY
identities = accepted.identities


def check_build(image):
    manifest = FIRMWARE / "manifest.json"
    meta = reports.read_json(manifest)
    if (meta.get("target") != BUILD_TARGET or meta.get("variant") != "test-ui-only" or
            meta.get("overlay") != OVERLAY or meta.get("layout_profile") != PROFILE.report() or
            meta.get("executorch_revision") != common.REVISION or meta.get("capture") != camera.CAPTURE or
            any(meta.get(n) is not False for n in ("hardware_verified", "programming_authorized", "camera_classification_implemented"))):
        raise common.SetupError("Expected the separate TEST UI overlay build, NOT a capture/live-ASL build")
    expected = FIRMWARE / "out/camera-inference/DevKit-E8-HP/debug/camera-inference.bin"
    binary = shared.inside(ROOT / meta["bin"], FIRMWARE)
    elf = shared.inside(ROOT / meta["elf"], FIRMWARE)
    if binary != expected.resolve() or elf != expected.with_suffix(".elf").resolve():
        raise common.SetupError("Overlay build does not name its exact binary/ELF")
    commands = elf.parent / "compile_commands.json"
    for path, digest in ((binary, meta["bin_sha256"]), (elf, meta["elf_sha256"]),
                         (Path(camera.__file__), meta["generator_sha256"]),
                         (commands, meta["compile_commands_sha256"])):
        if common.sha256(path) != digest:
            raise common.SetupError(f"Overlay artifact/helper changed: {path.name}")
    identities(meta["tool_sha256"], capture.TOOLS, ROOT / "tools")
    sources = [camera.APPLICATION / "src" / n for n in ("main.cpp", "power.cpp", "preview.cpp", "frame_export.cpp")]
    sources += [ROOT / "firmware/stored_image/src/model_runner.cpp", ROOT / "firmware/smoke/src/platform.cpp"]
    sources += [FIRMWARE / "generated" / n for n in ("platform.cpp", "main.cpp", "preview.cpp")]
    sources += [camera.smoke.VENDOR / "viewfinder/camera" / n for n in (
        "isp_calibration.c", "isp_calibration_mt9m114.c", "isp_calibration.h")]
    for folder in ("firmware/camera_inference/include", "firmware/smoke/include", "firmware/stored_image/include"):
        sources += list((ROOT / folder).glob("*.h"))
    sources += [overlay.HEADER, overlay.ROTATION_HEADER, Path(overlay.__file__)]
    identities(meta["source_sha256"], {str(p.relative_to(ROOT)) for p in sources}, ROOT)
    overlay_inputs = [Path(overlay.__file__), Path(camera.__file__), overlay.HEADER, overlay.ROTATION_HEADER]
    overlay_inputs += list(camera.APPLICATION.rglob("*.cpp")) + list(camera.APPLICATION.rglob("*.h"))
    identities(meta["overlay_input_sha256"], {str(p.relative_to(ROOT)) for p in overlay_inputs}, ROOT)
    for name, convert in (("main.cpp", overlay.main_variant), ("preview.cpp", overlay.preview_variant)):
        if (FIRMWARE / "generated" / name).read_text() != convert((camera.APPLICATION / "src" / name).read_text()):
            raise common.SetupError(f"Generated overlay {name} differs from the exact reviewed transformation")
    if (FIRMWARE / "generated/platform.cpp").read_text() != camera.platform_port(
            (ROOT / "firmware/smoke/src/platform.cpp").read_text()):
        raise common.SetupError("Generated overlay platform port differs from the reviewed transformation")
    generated = {str(p.relative_to(FIRMWARE)) for folder in ("RTE", "assets", "generated")
                 for p in (FIRMWARE / folder).rglob("*") if p.is_file()}
    required = {f"generated/{n}.cpp" for n in ("main", "preview", "platform")} | {
        f"assets/{n}.h" for n in ("image_config", "reference_model_pte", "reference_input", "reference_logits")}
    if not required.issubset(generated):
        raise common.SetupError("Missing generated overlay source/assets")
    identities(meta["generated_sha256"], generated, FIRMWARE)
    identities(meta["project_sha256"], capture.PROJECTS, FIRMWARE)
    compile_commands = json.loads(commands.read_text())
    if not isinstance(compile_commands, list):
        raise common.SetupError("Expected an overlay compilation-command array")
    compiled = []
    for entry in compile_commands:
        path = Path(entry["file"])
        if not path.is_absolute():
            path = Path(entry["directory"]) / path
        compiled.append(path.resolve())
    for name in ("main.cpp", "preview.cpp", "platform.cpp"):
        if compiled.count((FIRMWARE / "generated" / name).resolve()) != 1:
            raise common.SetupError(f"Overlay must compile exactly one generated {name}")
    if any((camera.APPLICATION / "src" / n).resolve() in compiled for n in ("main.cpp", "preview.cpp")):
        raise common.SetupError("Overlay compiled unmodified capture sources instead of its rotated TEST UI variants")
    if compiled.count((camera.APPLICATION / "src/frame_export.cpp").resolve()) != 1:
        raise common.SetupError("Screen rotation must compile the unchanged v4 frame exporter exactly once")

    # Revalidate the complete stored-image prerequisite/runtime independently.
    prerequisite = accepted.check_build(image)
    baseline = reports.read_json(camera.image.WORKSPACE / "manifest.json")
    if (meta["stored_image_build_manifest_sha256"] != prerequisite["build_manifest_sha256"] or
            any(meta[n] != baseline[n] for n in ("model_sha256", "reference_hashes", "driver_revision", "comparison"))):
        raise common.SetupError("Overlay no longer matches its stored-image prerequisite")
    if common.run(["git", "rev-parse", "HEAD"], cwd=camera.smoke.VENDOR, capture=True) != meta["vendor_revision"]:
        raise common.SetupError("Viewfinder revision changed")
    pack = common.find_cmsis().parents[2] / "AlifSemiconductor/Ensemble/2.2.0"
    linker = pack / "Device/core/rtss_hp/linker/linker_gnu_mram.ld.src"
    isp = pack / "libs/isp/libisp_gcc.a"
    pack_inputs = {linker, isp} | {p for p in compiled if p.is_relative_to(pack)}
    for folder in ("Alif_CMSIS/Include", "libs/isp/inc", "Device/core/common/include", "Device/soc/AE822FA0E5597/include"):
        pack_inputs.update((pack / folder).rglob("*.h"))
    identities(meta["pack_input_sha256"], {str(p.relative_to(pack)) for p in pack_inputs}, pack)
    if common.sha256(linker) != meta["sdk_linker_sha256"] or common.sha256(isp) != meta["isp_library_sha256"]:
        raise common.SetupError("Overlay SDK linker/ISP identity changed")
    compiler = common.find_toolchain(None)
    for path in (elf, isp):
        camera.smoke.verify_arm_abi(common.run([compiler / "arm-none-eabi-readelf", "-A", path], capture=True))
    symbols = camera.smoke.parse_symbols(common.run([compiler / "arm-none-eabi-nm", "-S", "-C", "--defined-only", elf], capture=True))
    data = binary.read_bytes()
    layout = camera.verify_layout(symbols, data, baseline["layout"]["reference_model_pte"]["bytes"])
    if layout != meta["layout"]:
        raise common.SetupError("Fresh overlay camera/NPU memory/vector/startup layout differs from its build")
    original = prerequisite["binary"].read_bytes()
    for name in ("reference_model_pte", "reference_input", "reference_logits"):
        address, size, _ = symbols[name]
        old = int(baseline["layout"][name]["address"], 16) - PROFILE.mram_base
        offset = address - PROFILE.mram_base
        if data[offset:offset + size] != original[old:old + size]:
            raise common.SetupError(f"Embedded {name} differs from accepted stored-image bytes")
    return dict(binary=binary, binary_sha256=meta["bin_sha256"], binary_bytes=len(data),
                build_manifest_sha256=common.sha256(manifest), image=str(image.resolve()))


def helper_identities():
    return {n: common.sha256(ROOT / "tools" / n) for n in (
        "prepare_camera_overlay.py", "program_camera_overlay.py", "camera_overlay_firmware.py",
        "camera_inference_firmware.py", "prepare_camera_inference.py", "program_camera_inference.py",
        "program_stored_image.py", "prepare_stored_image.py", "program_smoke.py",
        "inference.py", "memory_preflight.py")}


def print_review(receipt, stage):
    layout = receipt["package_layout"]
    print(f"TEST UI ONLY / NOT LIVE ASL: {stage}")
    print(f"Image: {IMAGE_NAME}; {receipt['binary_bytes']} bytes; SHA-256 {receipt['binary_sha256']}")
    print(f"HP application: [{layout['image_address']}, {layout['image_end']})")
    print(f"DEVICE/certificates/ATOC: [{layout['package_address']}, {layout['package_end']}); {layout['package_bytes']} bytes")
    print("Whole LCD (camera and text) rotated 180 degrees; cycles -- A B C L Y; no gesture recognition.")
    print("Stored-image self-test and upright v4 boot export unchanged. Hardware verification pending.")
    print(f"Receipt: {stage / 'receipt.json'}")
    print("Preparation/review only: no serial port opened or board programmed.")


def package_engine():
    """Private instance of the established package/map/TOC checks, not a bypass.

    Its exact image/certificate names, workspace and receipt target are replaced;
    source/build acceptance is the overlay-specific validator above. No mutation
    of the working capture module or its package pointer occurs.
    """
    spec = importlib.util.spec_from_file_location("tools._overlay_package", capture.__file__)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    engine.ROOT, engine.FIRMWARE, engine.PROGRAM = ROOT, FIRMWARE, PROGRAM
    engine.TARGET, engine.IMAGE_NAME, engine.CONFIG_NAME = TARGET, IMAGE_NAME, CONFIG_NAME
    engine.CRITICAL = (shared.CRITICAL_FILES - {"build/images/handgpt-smoke.bin"}) | {f"build/images/{IMAGE_NAME}"}
    engine.check_build = check_build
    engine.helper_identities = helper_identities
    engine.print_review = print_review
    return engine


def prepare(setools_root, image, hp_only_board=False):
    root = Path(setools_root).expanduser().resolve(strict=True)
    # Only an installed toolkit outside all private inference staging/builds.
    if root.is_relative_to(common.WORK.resolve()) or common.WORK.resolve().is_relative_to(root):
        raise common.SetupError("Use installed SETOOLS outside the private inference workspace")
    return package_engine().prepare(root, image, hp_only_board)


def load_prepared():
    return package_engine().load_prepared()


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare", allow_abbrev=False)
    create.add_argument("--setools-root", required=True)
    create.add_argument("--image", type=Path, required=True)
    create.add_argument("--hp-only-board", action="store_true")
    commands.add_parser("review", allow_abbrev=False)
    args = parser.parse_args()
    try:
        common.prepare_work()
        with (common.WORK / "command.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == "prepare":
                prepare(args.setools_root, args.image.expanduser().resolve(strict=True), args.hp_only_board)
            else:
                print_review(*load_prepared())
    except (common.SetupError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
