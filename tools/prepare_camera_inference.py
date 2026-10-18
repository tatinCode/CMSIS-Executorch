"""Prepare/review a private HP-only camera coexistence package. No device-writing command."""

import argparse
import fcntl
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

from tools import camera_inference_firmware as firmware
from tools import inference as common
from tools import memory_preflight as reports
from tools import prepare_stored_image as accepted
from tools import program_smoke as shared


ROOT = common.ROOT
FIRMWARE = firmware.WORKSPACE
PROGRAM = common.WORK / "camera-inference-fast-programming"
PROFILE = firmware.image.PROFILE
TARGET = "camera-inference.debug+DevKit-E8-HP"
IMAGE_NAME = "handgpt-camera-inference.bin"
CONFIG_NAME = "handgpt-camera-inference.json"
PROJECTS = {"camera-inference.csolution.yml", "camera-inference.cproject.yml", "cdefault.yml"}
TOOLS = {"prepare_stored_image.py", "program_smoke.py", "stored_image_firmware.py", "firmware_layouts.py"}
CRITICAL = (shared.CRITICAL_FILES - {"build/images/handgpt-smoke.bin"}) | {f"build/images/{IMAGE_NAME}"}
identities = accepted.identities


def check_build(image):
    manifest = FIRMWARE / "manifest.json"
    meta = reports.read_json(manifest)
    if (meta.get("target") != TARGET or meta.get("layout_profile") != PROFILE.report()
            or meta.get("executorch_revision") != common.REVISION or meta.get("capture") != firmware.CAPTURE
            or any(meta.get(name) is not False for name in ("hardware_verified", "programming_authorized", "camera_classification_implemented"))):
        raise common.SetupError("Expected the separate hp-only-large camera coexistence build; rebuild first.")
    expected = FIRMWARE / "out/camera-inference/DevKit-E8-HP/debug/camera-inference.bin"
    binary = shared.inside(ROOT / meta["bin"], FIRMWARE)
    elf = shared.inside(ROOT / meta["elf"], FIRMWARE)
    if binary != expected.resolve() or elf != expected.with_suffix(".elf").resolve():
        raise common.SetupError("Build does not name the exact camera-inference binary/ELF.")
    commands = elf.parent / "compile_commands.json"
    for path, digest in ((binary, meta["bin_sha256"]), (elf, meta["elf_sha256"]),
                         (Path(firmware.__file__), meta["generator_sha256"]),
                         (commands, meta["compile_commands_sha256"])):
        if common.sha256(path) != digest:
            raise common.SetupError(f"Build artifact/helper changed: {path.name}; rebuild first.")
    identities(meta["tool_sha256"], TOOLS, ROOT / "tools")
    sources = [ROOT / "firmware/camera_inference/src" / name for name in ("main.cpp", "power.cpp", "preview.cpp", "frame_export.cpp")]
    sources += [ROOT / "firmware/stored_image/src/model_runner.cpp", ROOT / "firmware/smoke/src/platform.cpp",
                FIRMWARE / "generated/platform.cpp"]
    sources += [firmware.smoke.VENDOR / "viewfinder/camera" / name for name in (
        "isp_calibration.c", "isp_calibration_mt9m114.c", "isp_calibration.h")]
    for directory in ("firmware/camera_inference/include", "firmware/smoke/include", "firmware/stored_image/include"):
        sources += list((ROOT / directory).glob("*.h"))
    identities(meta["source_sha256"], {str(path.relative_to(ROOT)) for path in sources}, ROOT)
    generated = {str(path.relative_to(FIRMWARE)) for folder in ("RTE", "assets", "generated")
                 for path in (FIRMWARE / folder).rglob("*") if path.is_file()}
    required = {"generated/platform.cpp"} | {f"assets/{name}.h" for name in (
        "image_config", "reference_model_pte", "reference_input", "reference_logits")}
    if not required.issubset(generated):
        raise common.SetupError("Missing generated camera coexistence assets/platform port.")
    identities(meta["generated_sha256"], generated, FIRMWARE)
    identities(meta["project_sha256"], PROJECTS, FIRMWARE)

    # Validate the unchanged stored-image prerequisite, runtime, model/reference,
    # toolchain and its source/header/project identities independently of this link.
    prerequisite = accepted.check_build(image)
    baseline = reports.read_json(firmware.image.WORKSPACE / "manifest.json")
    if (meta["stored_image_build_manifest_sha256"] != prerequisite["build_manifest_sha256"]
            or any(meta[name] != baseline[name] for name in ("model_sha256", "reference_hashes", "driver_revision", "comparison"))):
        raise common.SetupError("Camera build no longer matches its stored-image prerequisite; rebuild first.")
    vendor = firmware.smoke.VENDOR
    if common.run(["git", "rev-parse", "HEAD"], cwd=vendor, capture=True) != meta["vendor_revision"]:
        raise common.SetupError("Viewfinder revision changed; rebuild first.")
    pack = common.find_cmsis().parents[2] / "AlifSemiconductor/Ensemble/2.2.0"
    linker = pack / "Device/core/rtss_hp/linker/linker_gnu_mram.ld.src"
    isp = pack / "libs/isp/libisp_gcc.a"
    compile_commands = json.loads(commands.read_text())
    if not isinstance(compile_commands, list):
        raise common.SetupError("Expected a compilation-command array for SDK provenance.")
    pack_inputs = {linker, isp}
    for entry in compile_commands:
        path = Path(entry["file"])
        if not path.is_absolute():
            path = Path(entry["directory"]) / path
        path = path.resolve()
        if path.is_relative_to(pack):
            pack_inputs.add(path)
    for folder in ("Alif_CMSIS/Include", "libs/isp/inc", "Device/core/common/include", "Device/soc/AE822FA0E5597/include"):
        pack_inputs.update((pack / folder).rglob("*.h"))
    identities(meta["pack_input_sha256"], {str(path.relative_to(pack)) for path in pack_inputs}, pack)
    if common.sha256(linker) != meta["sdk_linker_sha256"] or common.sha256(isp) != meta["isp_library_sha256"]:
        raise common.SetupError("SDK linker/ISP library identity changed; rebuild first.")
    compiler = common.find_toolchain(None)
    firmware.smoke.verify_arm_abi(common.run([compiler / "arm-none-eabi-readelf", "-A", elf], capture=True))
    firmware.smoke.verify_arm_abi(common.run([compiler / "arm-none-eabi-readelf", "-A", isp], capture=True))
    symbols = firmware.smoke.parse_symbols(common.run([compiler / "arm-none-eabi-nm", "-S", "-C", "--defined-only", elf], capture=True))
    data = binary.read_bytes()
    layout = firmware.verify_layout(symbols, data, baseline["layout"]["reference_model_pte"]["bytes"])
    if layout != meta["layout"]:
        raise common.SetupError("Fresh camera/NPU memory/vector/startup layout differs from the build.")
    original = prerequisite["binary"].read_bytes()
    for name in ("reference_model_pte", "reference_input", "reference_logits"):
        address, size, _ = symbols[name]
        old_offset = int(baseline["layout"][name]["address"], 16) - PROFILE.mram_base
        offset = address - PROFILE.mram_base
        if data[offset:offset + size] != original[old_offset:old_offset + size]:
            raise common.SetupError(f"Embedded {name} differs from its accepted stored-image artifact.")
    return {"binary": binary, "binary_sha256": meta["bin_sha256"], "binary_bytes": len(data),
            "build_manifest_sha256": common.sha256(manifest), "image": str(image.resolve())}


def make_config(baseline):
    # Keep the smoke validator strict; select the opt-in address only here.
    config = shared.make_toc_config(baseline)
    config["USER_APP"].update(binary=IMAGE_NAME, mramAddress=hex(PROFILE.mram_base))
    return config


def baseline_config():
    return reports.read_json(firmware.smoke.VENDOR / ".alif/M55_HP_cfg.json")


def verify_package(text, package, image_bytes, device):
    # Same reviewed SETOOLS 1.112.00 binary encoding as the standalone package,
    # but require this target's exact image/certificate names. Never translate a
    # stored-image map/receipt into a camera package or weaken its validator.
    text = text.replace("\x00", "")
    marker = "* * * APP Package Start Address"
    if text.count(marker) != 1 or not 1984 <= image_bytes <= PROFILE.limits["mram_bytes"] or image_bytes % 16:
        raise common.SetupError("Invalid package map/image extent.")
    user, tail = text.split(marker)

    def rows(section):
        pattern = r"^0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+(\d+)\s+(.+?)\s*$"
        result = []
        for address, size, decimal, description in re.findall(pattern, section, re.MULTILINE):
            if int(size, 16) != int(decimal):
                raise common.SetupError("Package map has conflicting sizes.")
            result.append((int(address, 16), int(size, 16), " ".join(description.split())))
        if len(result) != len(re.findall(r"^0x", section, re.MULTILINE)):
            raise common.SetupError("Unparsed package map row.")
        return result

    if rows(user) != [(PROFILE.mram_base, image_bytes, f"unsigned {IMAGE_NAME}")]:
        raise common.SetupError("Expected only the exact camera-inference binary at 0x80001000.")
    locations = rows(tail)
    if len(locations) != 8:
        raise common.SetupError("Unexpected device/certificate/ATOC objects.")
    start = PROFILE.user_end - len(package)
    cursor = start
    if not PROFILE.mram_end <= start < PROFILE.user_end:
        raise common.SetupError("Boot package overlaps firmware or system memory.")
    for address, size, _ in locations:
        if address != cursor or size <= 0 or address % 16 or size % 16:
            raise common.SetupError("Package rows overlap, have gaps, or are unaligned.")
        cursor += size
    if cursor != PROFILE.user_end:
        raise common.SetupError("Package rows do not cover the complete reserved-tail package.")
    app_cert, keys, device_cert, device_image, header, device_entry, app_entry, end = locations
    descriptions = [f"SB{IMAGE_NAME}.crt", "Key1/Key2 Certificates", "SBapp-device-config.bin.crt",
                    "app-device-config.bin", "APP TOC Header",
                    f"APP TOC entry for DEVICE obj_address {hex(keys[0])}",
                    f"APP TOC entry for USER_APP obj_address {hex(app_cert[0])}", "APP TOC Tail"]
    if [row[2] for row in locations] != descriptions or [row[1] for row in locations[4:]] != [32, 32, 32, 16]:
        raise common.SetupError("Unexpected package entries/TOC layout; HP-only package required.")

    def summary(label):
        values = re.findall(rf"^\s*- {re.escape(label)}: (0x[0-9a-fA-F]+|\d+)(?: bytes)?\s*$", text, re.MULTILINE)
        if len(values) != 1:
            raise common.SetupError(f"Missing/ambiguous map summary: {label}")
        return int(values[0], 16 if values[0].startswith("0x") else 10)

    if (summary("APP Package total size"), summary("APP Package Start Address"), summary("APP TOC size"), summary("APP TOC Start Address")) != (len(package), start, 112, header[0]):
        raise common.SetupError("Package summaries disagree with actual extents.")
    table = package[header[0] - start:]
    expected_header = b"OEMTOC01" + struct.pack("<II", 0x20020, 0x20020) + b"AE822FA0E5597LS0"
    if table[:32] != expected_header:
        raise common.SetupError("Binary TOC header/device identity differs from the reviewed E8 format.")
    expected_device = struct.pack("<5I12s", keys[0], len(device), 3, 0xF, 0x50000, b"DEVICE")
    expected_app = struct.pack("<5I12s", app_cert[0], image_bytes, 4, 0x42, 0x1000000, b"USER_APP")
    if table[32:64] != expected_device or table[64:96] != expected_app:
        raise common.SetupError("Binary TOC entries are not the intended DEVICE + HP-only boot.")
    crc = zlib.crc32(table[:96])
    if table[96:] != struct.pack("<4I", crc, header[0], start, len(package)) or summary("APP CRC32") != crc:
        raise common.SetupError("Binary TOC tail/CRC does not match the package/map.")
    offset = device_image[0] - start
    if device_image[1] != len(device) or package[offset:offset + len(device)] != device:
        raise common.SetupError("Packaged DEVICE bytes differ from the generated baseline DEVICE image.")
    return {"image_address": hex(PROFILE.mram_base), "image_end": hex(PROFILE.mram_base + image_bytes),
            "package_address": hex(start), "package_bytes": len(package), "package_end": hex(PROFILE.user_end),
            "boot_cpu": "M55_HP", "entries": ["DEVICE", "USER_APP"], "toc_crc32": hex(crc),
            "complete_tail_coverage_verified": True, "binary_toc_verified": True}


def inspect_package(toolkit, info):
    return verify_package((toolkit / "build/app-package-map.txt").read_text(),
                          (toolkit / "build/AppTocPackage.bin").read_bytes(), info["binary_bytes"],
                          (toolkit / "build/images/app-device-config.bin").read_bytes())


def package_fingerprints(toolkit):
    # Include helper modules/resources as well as the executables and package.
    # Reject symlink escapes; exclude only the tool's runtime log directory.
    files = {}
    for path in sorted(toolkit.rglob("*")):
        name = path.relative_to(toolkit)
        if path.is_symlink():
            raise common.SetupError("Symlinks are not permitted in the private camera toolkit.")
        if name.parts[:2] == ("build", "logs"):
            continue
        if path.is_file():
            files[str(name)] = common.sha256(shared.inside(path, toolkit))
    return files


def helper_identities():
    return {name: common.sha256(ROOT / "tools" / name) for name in (
        "prepare_camera_inference.py", "program_camera_inference.py", "program_stored_image.py",
        "prepare_stored_image.py", "program_smoke.py")}


def prepare(setools_root, image, hp_only_board=False):
    if not hp_only_board:
        raise common.SetupError("Confirm the board is dedicated to HP only with --hp-only-board; this is NOT write authorization.")
    PROGRAM.mkdir(parents=True, exist_ok=True, mode=0o700)
    PROGRAM.chmod(0o700)
    (PROGRAM / "latest.json").unlink(missing_ok=True)
    info = check_build(image)
    root = Path(setools_root).expanduser().resolve(strict=True)
    for work in (PROGRAM, common.WORK / "camera-inference-programming", accepted.PROGRAM, shared.PROGRAM):
        if root.is_relative_to(work.resolve()) or work.resolve().is_relative_to(root):
            raise common.SetupError("Use the installed SETOOLS directory, not a prepared toolkit or its parent.")
    version, device = shared.validate_toolkit(root)
    baseline = baseline_config()
    config = make_config(baseline)
    helpers = helper_identities()
    common.check_space(1)
    original = shared.protected_state(root)
    stage = Path(tempfile.mkdtemp(prefix="camera-inference-", dir=PROGRAM))
    toolkit = stage / "setools"
    # The installed toolkit is never executed. Signing material stays private
    # in this ignored 0700 stage; never publish its contents or raw logs.
    shutil.copytree(root, toolkit, symlinks=True,
                    ignore=lambda directory, names: {"build"} if Path(directory) == root else set())
    for folder in ("config", "images"):
        shutil.copytree(root / "build" / folder, toolkit / "build" / folder, symlinks=True)
    (toolkit / "build/logs").mkdir()
    package_fingerprints(toolkit)  # Reject symlinks before executing the clone.
    staged = toolkit / "build/images" / IMAGE_NAME
    shutil.copy2(info["binary"], staged)
    config_path = toolkit / CONFIG_NAME
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    if common.sha256(staged) != info["binary_sha256"]:
        raise common.SetupError("Staged camera-inference binary differs from the build.")
    shared.run_logged([toolkit / "app-gen-toc", "-f", CONFIG_NAME], toolkit, stage / "prepare.log")
    if shared.protected_state(root) != original:
        raise common.SetupError("Installed SETOOLS state changed; no receipt published.")
    if common.sha256(toolkit / "build/config/app-device-config.json") != original["build/config/app-device-config.json"]:
        raise common.SetupError("Baseline DEVICE configuration changed during generation.")
    layout = inspect_package(toolkit, info)
    if check_build(image) != info or common.sha256(staged) != info["binary_sha256"] or helper_identities() != helpers:
        raise common.SetupError("Build/staged inputs/helpers changed while preparing.")
    files = package_fingerprints(toolkit)
    if not CRITICAL.issubset(files) or reports.read_json(config_path) != config:
        raise common.SetupError("Generated camera package lacks required tools/files or changed its boot configuration.")
    receipt = {"format": 1, "target": TARGET, "layout_profile": PROFILE.report(),
               "hp_only_board_confirmed": True, "programming_authorized": False, "hardware_programmed": False,
               "toolkit": str(toolkit.relative_to(PROGRAM)), "original_toolkit": str(root),
               "setools_version": version, "configured_device": device,
               "binary_sha256": info["binary_sha256"], "binary_bytes": info["binary_bytes"],
               "build_manifest_sha256": info["build_manifest_sha256"], "image": info["image"],
               "config_sha256": common.sha256(config_path), "baseline_config": baseline,
               "helper_sha256": helpers, "files": files, "package_layout": layout}
    reports.write_report(stage / "receipt.json", receipt)
    reports.write_report(PROGRAM / "latest.json", {"receipt": str((stage / "receipt.json").relative_to(PROGRAM))})
    print_review(receipt, stage)


def load_prepared():
    pointer = reports.read_json(PROGRAM / "latest.json")
    path = shared.inside(PROGRAM / pointer["receipt"], PROGRAM)
    receipt = reports.read_json(path)
    if (receipt.get("format") != 1 or receipt.get("target") != TARGET
            or receipt.get("layout_profile") != PROFILE.report() or receipt.get("hp_only_board_confirmed") is not True
            or receipt.get("programming_authorized") is not False or receipt.get("hardware_programmed") is not False
            or receipt.get("helper_sha256") != helper_identities()):
        raise common.SetupError("Unexpected/stale camera-inference receipt; prepare again.")
    info = check_build(Path(receipt["image"]))
    if any(receipt[name] != info[name] for name in ("binary_sha256", "binary_bytes", "build_manifest_sha256")):
        raise common.SetupError("Prepared camera package no longer matches the build; prepare again.")
    toolkit = shared.inside(PROGRAM / receipt["toolkit"], path.parent)
    if toolkit != (path.parent / "setools").resolve():
        raise common.SetupError("Unexpected staged toolkit path.")
    config_path = toolkit / CONFIG_NAME
    if (common.sha256(config_path) != receipt["config_sha256"] or baseline_config() != receipt["baseline_config"]
            or reports.read_json(config_path) != make_config(baseline_config())):
        raise common.SetupError("Prepared/baseline HP boot configuration changed.")
    if not CRITICAL.issubset(receipt["files"]) or package_fingerprints(toolkit) != receipt["files"]:
        raise common.SetupError("Prepared package/tool identities changed; prepare again.")
    if common.sha256(toolkit / "build/images" / IMAGE_NAME) != info["binary_sha256"]:
        raise common.SetupError("Staged binary is not the checked camera-inference build.")
    version, device = shared.validate_toolkit(toolkit)
    if version != receipt["setools_version"] or device != receipt["configured_device"] or inspect_package(toolkit, info) != receipt["package_layout"]:
        raise common.SetupError("Fresh package/device inspection differs from the receipt.")
    return receipt, path.parent


def print_review(receipt, stage):
    layout = receipt["package_layout"]
    print(f"PREPARED/REVIEWED ONLY: {stage}")
    print(f"HP-only image: {IMAGE_NAME}, {receipt['binary_bytes']} bytes at {layout['image_address']}")
    print(f"Binary SHA-256: {receipt['binary_sha256']}")
    print("Capture v4: full-view 480 -> 128 PIL-bilinear RGB888P; 49,152-byte payload. Not a crop.")
    print(f"DEVICE/certificates/ATOC: {layout['package_bytes']} bytes at {layout['package_address']} through {layout['package_end']} (exclusive)")
    print(f"Boot entries: DEVICE + USER_APP (M55_HP only); receipt: {stage / 'receipt.json'}")
    print("Preview + STORED-image self-test only; NOT camera classification. Hardware validation remains required.")
    print("Board preservation confirmation recorded; this is NOT authorization to write.")
    print("No serial port was opened. No writer was run. No board was programmed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare", help="Host-only isolated package generation", allow_abbrev=False)
    create.add_argument("--setools-root", required=True)
    create.add_argument("--image", type=Path, required=True)
    create.add_argument("--hp-only-board", action="store_true", help="Confirm no HE/A32 or reclaimed MRAM data needs preservation; NOT write permission")
    commands.add_parser("review", help="Revalidate the camera package; no device access", allow_abbrev=False)
    args = parser.parse_args()
    try:
        common.prepare_work()
        with (common.WORK / "command.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == "prepare":
                prepare(args.setools_root, args.image.expanduser().resolve(strict=True), args.hp_only_board)
            else:
                receipt, stage = load_prepared()
                print_review(receipt, stage)
    except (common.SetupError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
