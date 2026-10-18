"""Prepare/review a private HP-only stored-image boot package. No device-writing command."""

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

from tools import inference as common
from tools import memory_preflight as preflight
from tools import program_smoke as shared
from tools import stored_image_firmware as firmware


ROOT = common.ROOT
FIRMWARE = firmware.WORKSPACE
PROGRAM = common.WORK / "stored-image-programming"
PROFILE = firmware.PROFILE
IMAGE_NAME = "handgpt-stored-image.bin"
CONFIG_NAME = "handgpt-stored-image.json"
PROJECTS = {"stored-image.csolution.yml", "stored-image.cproject.yml", "cdefault.yml", "preflight.json"}
TOOLS = {"inference.py", "smoke_firmware.py", "memory_preflight.py", "inspect_reference_pte.py", "prepare_reference_image.py"}
CRITICAL = (shared.CRITICAL_FILES - {"build/images/handgpt-smoke.bin"}) | {f"build/images/{IMAGE_NAME}"}


def identities(recorded, paths, base):
    if set(recorded) != set(paths):
        raise common.SetupError("Incomplete or unexpected build identities; rebuild first.")
    for name, digest in recorded.items():
        if common.sha256(shared.inside(base / name, base)) != digest:
            raise common.SetupError(f"Build input changed: {name}; rebuild first.")


def check_build(image):
    manifest = FIRMWARE / "manifest.json"
    meta = preflight.read_json(manifest)
    if (meta.get("target") != "stored-image.debug+DevKit-E8-HP" or meta.get("layout_profile") != PROFILE.report()
            or meta.get("executorch_revision") != common.REVISION):
        raise common.SetupError("Expected the separate verified hp-only-large stored-image build.")
    expected = FIRMWARE / "out/stored-image/DevKit-E8-HP/debug/stored-image.bin"
    binary = shared.inside(ROOT / meta["bin"], FIRMWARE)
    elf = shared.inside(ROOT / meta["elf"], FIRMWARE)
    if binary != expected.resolve() or elf != expected.with_suffix(".elf").resolve():
        raise common.SetupError("Build does not name the exact stored-image binary/ELF.")
    for path, digest in ((binary, meta["bin_sha256"]), (elf, meta["elf_sha256"]),
                         (Path(firmware.__file__), meta["generator_sha256"]),
                         (ROOT / "tools/firmware_layouts.py", meta["layout_definitions_sha256"])):
        if common.sha256(path) != digest:
            raise common.SetupError(f"Build artifact/helper changed: {path.name}; rebuild first.")
    sources = [ROOT / "firmware/stored_image/src/main.cpp", ROOT / "firmware/stored_image/src/model_runner.cpp",
               ROOT / "firmware/smoke/src/platform.cpp"]
    sources += list((ROOT / "firmware/stored_image/include").glob("*.h"))
    sources += list((ROOT / "firmware/smoke/include").glob("*.h"))
    identities(meta["source_sha256"], {str(path.relative_to(ROOT)) for path in sources}, ROOT)
    generated = {str(path.relative_to(FIRMWARE)) for folder in ("RTE", "assets")
                 for path in (FIRMWARE / folder).rglob("*") if path.is_file()}
    if not {"assets/image_config.h", "assets/reference_model_pte.h", "assets/reference_input.h", "assets/reference_logits.h"}.issubset(generated):
        raise common.SetupError("Missing generated stored-image assets.")
    identities(meta["generated_sha256"], generated, FIRMWARE)
    identities(meta["project_sha256"], PROJECTS, FIRMWARE)
    identities(meta["tool_sha256"], TOOLS, ROOT / "tools")
    runtime = common.WORK / "runtime-cortex-m55"
    if common.sha256(runtime / "manifest.json") != meta["runtime_manifest_sha256"]:
        raise common.SetupError("Runtime manifest changed; rebuild first.")
    runtime_meta = firmware.check_runtime(runtime)
    if runtime_meta["driver_revision"] != meta["driver_revision"]:
        raise common.SetupError("Runtime driver identity changed.")
    linker = common.find_cmsis().parents[2] / "AlifSemiconductor/Ensemble/2.2.0/Device/core/rtss_hp/linker/linker_gnu_mram.ld.src"
    if common.sha256(linker) != meta["sdk_linker_sha256"]:
        raise common.SetupError("SDK linker changed; rebuild first.")
    report = preflight.preflight(common.WORK / "models/mv2", firmware.budgets(), image, PROFILE.name)
    if (not report["fits"] or report != preflight.read_json(FIRMWARE / "preflight.json")
            or report["requirements"]["hashes"] != meta["reference_hashes"]
            or meta["model_sha256"] != report["requirements"]["hashes"]["model"]):
        raise common.SetupError("Fresh model/reference preflight differs from the build; rebuild first.")
    compiler = common.find_toolchain(None)
    if common.run([compiler / "arm-none-eabi-gcc", "-dumpfullversion"], capture=True) != runtime_meta["compiler"]:
        raise common.SetupError("Compiler does not match the recorded runtime.")
    firmware.smoke.verify_arm_abi(common.run([compiler / "arm-none-eabi-readelf", "-A", elf], capture=True))
    symbols = firmware.smoke.parse_symbols(common.run([compiler / "arm-none-eabi-nm", "-S", "-C", "--defined-only", elf], capture=True))
    data = binary.read_bytes()
    layout = firmware.verify_layout(symbols, data, report["requirements"]["model_bytes"])
    if layout != meta["layout"]:
        raise common.SetupError("Fresh linked layout differs from the build manifest.")
    # Check the actual embedded bytes, not merely the generated C++ headers.
    model = common.WORK / "models/mv2"
    embedded = {
        "reference_model_pte": (model / "mv2_ethosu85_256.pte").read_bytes(),
        "reference_input": (model / "reference/input.f32").read_bytes(),
        "reference_logits": preflight.read_float_npy(model / "reference/host_logits.npy", (1, 1000)),
    }
    for name, content in embedded.items():
        offset = symbols[name][0] - PROFILE.mram_base
        if data[offset:offset + len(content)] != content:
            raise common.SetupError(f"Embedded {name} differs from its accepted artifact.")
    return {"binary": binary, "binary_sha256": meta["bin_sha256"], "binary_bytes": len(data),
            "build_manifest_sha256": common.sha256(manifest), "image": str(image.resolve())}


def make_config(baseline):
    # Validate the original smoke address/core safeguards without relaxing them.
    result = shared.make_toc_config(baseline)
    result["USER_APP"].update(binary=IMAGE_NAME, mramAddress=hex(PROFILE.mram_base))
    return result


def baseline_config():
    return preflight.read_json(firmware.smoke.VENDOR / ".alif/M55_HP_cfg.json")


def verify_package(text, package, image_bytes, device):
    text = text.replace("\x00", "")
    marker = "* * * APP Package Start Address"
    if text.count(marker) != 1 or not 1984 <= image_bytes <= PROFILE.limits["mram_bytes"] or image_bytes % 16:
        raise common.SetupError("Invalid package map/image extent.")
    user, tail = text.split(marker)
    pattern = r"^0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+(\d+)\s+(.+?)\s*$"

    def rows(section):
        result = []
        for address, size, decimal, description in re.findall(pattern, section, re.MULTILINE):
            if int(size, 16) != int(decimal):
                raise common.SetupError("Package map has conflicting sizes.")
            result.append((int(address, 16), int(size, 16), " ".join(description.split())))
        if len(result) != len(re.findall(r"^0x", section, re.MULTILINE)):
            raise common.SetupError("Unparsed package map row.")
        return result

    if rows(user) != [(PROFILE.mram_base, image_bytes, f"unsigned {IMAGE_NAME}")]:
        raise common.SetupError("Expected only the exact stored-image binary at 0x80001000.")
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
    # SETOOLS 1.112.00 E8 Rev A0 encoding, checked against the verified smoke
    # package: CPU 4 / flags 0x42 is the sole unsigned XIP HP boot entry.
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


def prepare(setools_root, image, hp_only_board=False):
    if not hp_only_board:
        raise common.SetupError("Confirm the board is dedicated to HP only with --hp-only-board; this is NOT write authorization.")
    PROGRAM.mkdir(parents=True, exist_ok=True, mode=0o700)
    PROGRAM.chmod(0o700)
    (PROGRAM / "latest.json").unlink(missing_ok=True)
    info = check_build(image)
    root = Path(setools_root).expanduser().resolve(strict=True)
    for work in (PROGRAM, shared.PROGRAM):
        if root.is_relative_to(work.resolve()) or work.resolve().is_relative_to(root):
            raise common.SetupError("Use the installed SETOOLS directory, not a prepared toolkit or its parent.")
    version, device = shared.validate_toolkit(root)
    baseline = baseline_config()
    config = make_config(baseline)
    common.check_space(1)
    original = shared.protected_state(root)
    stage = Path(tempfile.mkdtemp(prefix="stored-image-", dir=PROGRAM))
    toolkit = stage / "setools"
    # Never reuse an old generated TOC; copy private signing material only into
    # this ignored, mode-0700 workspace. The installed toolkit is never run.
    shutil.copytree(root, toolkit, ignore=lambda directory, names: {"build"} if Path(directory) == root else set())
    for folder in ("config", "images"):
        shutil.copytree(root / "build" / folder, toolkit / "build" / folder)
    (toolkit / "build/logs").mkdir()
    staged_image = toolkit / "build/images" / IMAGE_NAME
    shutil.copy2(info["binary"], staged_image)
    if common.sha256(staged_image) != info["binary_sha256"]:
        raise common.SetupError("Staged stored-image binary differs from the build.")
    config_path = toolkit / CONFIG_NAME
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    shared.run_logged([toolkit / "app-gen-toc", "-f", CONFIG_NAME], toolkit, stage / "prepare.log")
    if shared.protected_state(root) != original:
        raise common.SetupError("Installed SETOOLS state changed; no receipt published.")
    if common.sha256(toolkit / "build/config/app-device-config.json") != original["build/config/app-device-config.json"]:
        raise common.SetupError("Baseline DEVICE configuration changed during generation.")
    layout = inspect_package(toolkit, info)
    if check_build(image) != info or common.sha256(staged_image) != info["binary_sha256"]:
        raise common.SetupError("Build/staged inputs changed while preparing.")
    receipt = {"format": 1, "target": "stored-image.debug+DevKit-E8-HP", "layout_profile": PROFILE.report(),
               "hp_only_board_confirmed": True, "programming_authorized": False, "hardware_programmed": False,
               "toolkit": str(toolkit.relative_to(PROGRAM)), "original_toolkit": str(root),
               "setools_version": version, "configured_device": device,
               "binary_sha256": info["binary_sha256"], "binary_bytes": info["binary_bytes"],
               "build_manifest_sha256": info["build_manifest_sha256"], "image": info["image"],
               "config_sha256": common.sha256(config_path), "baseline_config": baseline,
               "helper_sha256": common.sha256(Path(__file__)),
               "files": shared.package_fingerprints(toolkit), "package_layout": layout}
    preflight.write_report(stage / "receipt.json", receipt)
    preflight.write_report(PROGRAM / "latest.json", {"receipt": str((stage / "receipt.json").relative_to(PROGRAM))})
    print_review(receipt, stage)


def load_prepared():
    pointer = preflight.read_json(PROGRAM / "latest.json")
    path = shared.inside(PROGRAM / pointer["receipt"], PROGRAM)
    receipt = preflight.read_json(path)
    if (receipt.get("format") != 1 or receipt.get("target") != "stored-image.debug+DevKit-E8-HP"
            or receipt.get("layout_profile") != PROFILE.report() or receipt.get("hp_only_board_confirmed") is not True
            or receipt.get("programming_authorized") is not False or receipt.get("hardware_programmed") is not False
            or receipt.get("helper_sha256") != common.sha256(Path(__file__))):
        raise common.SetupError("Unexpected/stale stored-image receipt; prepare again.")
    info = check_build(Path(receipt["image"]))
    if any(receipt[name] != info[name] for name in ("binary_sha256", "binary_bytes", "build_manifest_sha256")):
        raise common.SetupError("Prepared package no longer matches the build; prepare again.")
    toolkit = shared.inside(PROGRAM / receipt["toolkit"], path.parent)
    if toolkit != (path.parent / "setools").resolve():
        raise common.SetupError("Unexpected staged toolkit path.")
    config_path = toolkit / CONFIG_NAME
    if (common.sha256(config_path) != receipt["config_sha256"] or baseline_config() != receipt["baseline_config"]
            or preflight.read_json(config_path) != make_config(baseline_config())):
        raise common.SetupError("Prepared/baseline HP boot configuration changed.")
    if not CRITICAL.issubset(receipt["files"]) or shared.package_fingerprints(toolkit) != receipt["files"]:
        raise common.SetupError("Prepared package/tool identities changed; prepare again.")
    if common.sha256(toolkit / "build/images" / IMAGE_NAME) != info["binary_sha256"]:
        raise common.SetupError("Staged binary is not the verified stored-image build.")
    version, device = shared.validate_toolkit(toolkit)
    if version != receipt["setools_version"] or device != receipt["configured_device"] or inspect_package(toolkit, info) != receipt["package_layout"]:
        raise common.SetupError("Fresh package/device inspection differs from the receipt.")
    return receipt, path.parent


def print_review(receipt, stage):
    layout = receipt["package_layout"]
    print(f"PREPARED/REVIEWED ONLY: {stage}")
    print(f"HP-only image: {IMAGE_NAME}, {receipt['binary_bytes']} bytes at {layout['image_address']}")
    print(f"Binary SHA-256: {receipt['binary_sha256']}")
    print(f"DEVICE/certificates/ATOC: {layout['package_bytes']} bytes at {layout['package_address']} through {layout['package_end']} (exclusive)")
    print(f"Boot entries: DEVICE + USER_APP (M55_HP only); receipt: {stage / 'receipt.json'}")
    print("Board preservation confirmation recorded; this is NOT authorization to write.")
    print("No serial port was opened. No writer was run. No board was programmed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare", help="Host-only isolated package generation")
    create.add_argument("--setools-root", required=True)
    create.add_argument("--image", type=Path, required=True)
    create.add_argument("--hp-only-board", action="store_true", help="Confirm no HE/A32 or reclaimed MRAM data needs preservation; NOT write permission")
    commands.add_parser("review", help="Revalidate the latest stored-image package; no device access")
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
    except (common.SetupError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
