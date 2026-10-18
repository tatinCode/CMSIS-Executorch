"""Prepare/review a private SETOOLS package; device writes require --write AND confirmation."""

import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from tools import inference as common
from tools import smoke_firmware as smoke


ROOT = common.ROOT
FIRMWARE = smoke.WORKSPACE
PROGRAM = common.WORK / "programming"
CONFIRMATION = "PROGRAM E8-HP SMOKE"
IMAGE_NAME = "handgpt-smoke.bin"
CRITICAL_FILES = {
    "build/AppTocPackage.bin", "build/app-package-map.txt",
    "build/images/handgpt-smoke.bin", "build/images/app-device-config.bin",
    "build/config/app-device-config.json", "app-gen-toc", "app-write-mram",
    "app-sign-image", "utils/global-cfg.db", "version.txt",
}


def inside(path, directory):
    path = Path(path).resolve()
    if not path.is_relative_to(Path(directory).resolve()):
        raise common.SetupError(f"Path escapes the expected workspace: {path}")
    return path


def check_build():
    manifest_path = FIRMWARE / "manifest.json"
    meta = json.loads(manifest_path.read_text())
    if meta.get("target") != smoke.TARGET or meta.get("build") != "debug":
        raise common.SetupError("Expected a verified DevKit-E8-HP debug smoke build.")
    if meta.get("executorch_revision") != common.REVISION or not meta.get("layout", {}).get("vector_table_verified"):
        raise common.SetupError("Missing matching runtime/vector-layout verification.")
    expected = FIRMWARE / "out" / "smoke" / smoke.TARGET / "debug" / "smoke.bin"
    binary = inside(ROOT / meta["bin"], FIRMWARE)
    elf = inside(ROOT / meta["elf"], FIRMWARE)
    if binary != expected.resolve() or elf != expected.with_suffix(".elf").resolve():
        raise common.SetupError("Build manifest does not name this target's exact smoke.bin/ELF.")
    for path, digest in ((binary, meta["bin_sha256"]), (elf, meta["elf_sha256"])):
        if common.sha256(path) != digest:
            raise common.SetupError(f"Built artifact changed: {path.name}. Rebuild before preparing.")
    if not meta.get("source_sha256") or not meta.get("configuration_sha256"):
        raise common.SetupError("Build manifest lacks source/configuration identities; rebuild.")
    for name, digest in meta["source_sha256"].items():
        path = inside(ROOT / name, ROOT / "firmware" / "smoke")
        if common.sha256(path) != digest:
            raise common.SetupError(f"Firmware source changed since building: {name}. Rebuild first.")
    for name, digest in meta["configuration_sha256"].items():
        if common.sha256(inside(FIRMWARE / name, FIRMWARE / "RTE")) != digest:
            raise common.SetupError(f"RTE configuration changed since building: {name}. Rebuild first.")
    if common.sha256(Path(smoke.__file__)) != meta.get("generator_sha256"):
        raise common.SetupError("Smoke build helper changed; rebuild before preparing.")
    data = binary.read_bytes()
    if len(data) < 8 or len(data) > 0x200000:
        raise common.SetupError("Invalid binary size for the 2 MiB HP application region.")
    stack = int.from_bytes(data[:4], "little")
    reset = int.from_bytes(data[4:8], "little")
    if not (0x20000000 < stack <= 0x20100000 and 0x80200000 <= reset < 0x80400000 and reset & 1):
        raise common.SetupError("Binary boot vectors do not match HP MRAM / DTCM.")
    return {"binary": binary, "binary_sha256": meta["bin_sha256"],
            "build_manifest_sha256": common.sha256(manifest_path),
            "binary_bytes": len(data), "reset_vector": hex(reset)}


def validate_port(port):
    if not port.startswith("/dev/") or ".." in Path(port).parts or any(character.isspace() or ord(character) < 32 for character in port):
        raise common.SetupError("Specify an explicit Linux serial device such as /dev/ttyACM0.")


def make_toc_config(baseline):
    if set(baseline) != {"DEVICE", "USER_APP"}:
        raise common.SetupError("Unexpected baseline TOC entries; review before generating a smoke package.")
    app = baseline["USER_APP"]
    if (set(app) - {"binary", "mramAddress", "version", "cpu_id", "flags", "signed", "disabled"}
            or set(baseline["DEVICE"]) != {"binary", "version", "signed", "disabled"}):
        raise common.SetupError("Unexpected fields in the HP boot configuration; manual review is required.")
    if (app.get("cpu_id") != "M55_HP" or app.get("mramAddress") != "0x80200000"
            or app.get("flags") != ["boot"] or app.get("signed") is not False or app.get("disabled", False) is not False):
        raise common.SetupError("Baseline HP boot configuration changed; manual review is required.")
    device = baseline["DEVICE"]
    if device.get("binary") != "app-device-config.json" or device.get("signed") is not True or device.get("disabled") is not False:
        raise common.SetupError("Baseline DEVICE configuration changed; manual review is required.")
    result = copy.deepcopy(baseline)
    result["USER_APP"]["binary"] = IMAGE_NAME
    return result


def validate_toolkit(root):
    for name in ("app-gen-toc", "app-write-mram", "app-sign-image"):
        if not (root / name).is_file() or not os.access(root / name, os.X_OK):
            raise common.SetupError(f"Missing executable in SETOOLS directory: {name}")
    version = (root / "version.txt").read_text().strip()
    if version != "1.112.00":
        raise common.SetupError(f"This workflow was reviewed for SETOOLS 1.112.00, found {version}.")
    settings = json.loads((root / "utils" / "global-cfg.db").read_text())
    device = settings["DEVICE"]
    if "AE822FA0E5597LS0" not in device.get("Part#", "") or device.get("Revision") != "A0":
        raise common.SetupError("SETOOLS must already be configured for AE822FA0E5597LS0 Rev A0; no device configuration is changed automatically.")
    if settings.get("MRAM-BURNER", {}).get("Interface") != "isp":
        raise common.SetupError("SETOOLS must already select the ISP serial interface; JTAG/discovery is not used by this workflow.")
    return version, device


def protected_state(root):
    names = {"build/AppTocPackage.bin", "build/app-package-map.txt", "utils/global-cfg.db", "version.txt"}
    for directory in ("build/config", "build/images"):
        names.update(str(path.relative_to(root)) for path in (root / directory).rglob("*") if path.is_file())
    return {name: common.sha256(root / name) if (root / name).is_file() else None for name in names}


def run_logged(command, cwd, log):
    print("+ " + " ".join(map(str, command)), flush=True)
    # These commands are argument lists, not interpolated shell commands.
    with Path(log).open("wb") as output:
        with subprocess.Popen([str(value) for value in command], cwd=cwd,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
            while True:
                chunk = process.stdout.read1(4096)
                if not chunk:
                    break
                output.write(chunk)
                output.flush()
                if hasattr(sys.stdout, "buffer"):
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                else:
                    sys.stdout.write(chunk.decode(errors="replace"))
                    sys.stdout.flush()
            result = process.wait()
    if result:
        raise common.SetupError(f"Command failed ({result}); inspect {log}")
    text = Path(log).read_text(errors="replace")
    if "[ERROR]" in text or "Traceback (most recent call last):" in text:
        raise common.SetupError(f"Tool reported an error despite exit code zero; inspect {log}")


def package_fingerprints(toolkit):
    paths = [path for path in (toolkit / "build").rglob("*")
             if path.is_file() and "logs" not in path.relative_to(toolkit / "build").parts]
    paths += [toolkit / name for name in ("app-gen-toc", "app-write-mram", "app-sign-image", "utils/global-cfg.db", "version.txt")]
    return {str(path.relative_to(toolkit)): common.sha256(path) for path in sorted(paths)}


def verify_package_map(text, image_bytes, package_bytes):
    # The SETOOLS map contains NUL padding in TOC names; read it as text explicitly.
    user_section = text.split("* * * APP Package Start Address", 1)[0]
    rows = re.findall(r"^0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+(\d+)\s+(\S+)\s+(\S+)\s*$",
                      user_section, re.MULTILINE)
    if len(rows) != 1:
        raise common.SetupError("Expected exactly one user-managed image in the SETOOLS package map.")
    address, size, decimal, signing, name = rows[0]
    if (int(address, 16), int(size, 16), int(decimal), signing, name) != (
            0x80200000, image_bytes, image_bytes, "unsigned", IMAGE_NAME):
        raise common.SetupError("SETOOLS map does not place the exact smoke image at HP MRAM 0x80200000.")
    summary = {}
    for label in ("APP Package total size", "APP Package Start Address"):
        values = re.findall(rf"^\s*- {re.escape(label)}: (0x[0-9a-fA-F]+|\d+)(?: bytes)?\s*$", text, re.MULTILINE)
        if len(values) != 1:
            raise common.SetupError(f"Missing or ambiguous SETOOLS map field: {label}")
        summary[label] = int(values[0], 16 if values[0].startswith("0x") else 10)
    start = summary["APP Package Start Address"]
    if (summary["APP Package total size"] != package_bytes or package_bytes <= 0
            or start < 0x80400000 or start + package_bytes != 0x80580000):
        raise common.SetupError("Unexpected application TOC region; package could overlap application/system memory.")
    entries = re.findall(r"APP TOC entry for ([A-Z_]+)", text)
    if sorted(entries) != ["DEVICE", "USER_APP"]:
        raise common.SetupError("Unexpected boot entries in generated TOC map.")
    return {"image_address": "0x80200000", "package_address": hex(start), "package_bytes": package_bytes}


def prepare(setools_root, port):
    (PROGRAM / "latest.json").unlink(missing_ok=True)
    validate_port(port)
    info = check_build()
    root = Path(setools_root).expanduser().resolve(strict=True)
    if root.is_relative_to(PROGRAM.resolve()) or PROGRAM.resolve().is_relative_to(root):
        raise common.SetupError("Use the original installed SETOOLS directory, not a programming workspace or its parent.")
    version, device = validate_toolkit(root)
    config = make_toc_config(json.loads((smoke.VENDOR / ".alif" / "M55_HP_cfg.json").read_text()))
    common.check_space(1)
    original = protected_state(root)
    PROGRAM.mkdir(parents=True, exist_ok=True, mode=0o700)
    PROGRAM.chmod(0o700)
    stage = Path(tempfile.mkdtemp(prefix="smoke-", dir=PROGRAM))
    toolkit = stage / "setools"
    # Keep the full installed tool/resource layout, but NOT its old generated TOC.
    shutil.copytree(root, toolkit, ignore=lambda directory, names: {"build"} if Path(directory) == root else set())
    shutil.copytree(root / "build" / "config", toolkit / "build" / "config")
    shutil.copytree(root / "build" / "images", toolkit / "build" / "images")
    (toolkit / "build" / "logs").mkdir()
    image = toolkit / "build" / "images" / IMAGE_NAME
    shutil.copy2(info["binary"], image)
    if common.sha256(image) != info["binary_sha256"]:
        raise common.SetupError("Staged firmware differs from the verified build.")
    config_path = toolkit / "handgpt-smoke.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    log = stage / "prepare.log"
    run_logged([toolkit / "app-gen-toc", "-f", config_path.name], toolkit, log)
    if protected_state(root) != original:
        raise common.SetupError("Original SETOOLS state changed during preparation; no programming receipt published.")
    toc = toolkit / "build" / "AppTocPackage.bin"
    if not toc.is_file() or toc.stat().st_size == 0 or not (toolkit / "build" / "app-package-map.txt").is_file():
        raise common.SetupError("Fresh TOC/package map were not generated. Do not program.")
    map_layout = verify_package_map((toolkit / "build" / "app-package-map.txt").read_text(),
                                    info["binary_bytes"], toc.stat().st_size)
    receipt = {
        "format": 1, "target": smoke.TARGET, "cpu": "M55_HP", "mram_address": "0x80200000",
        "toolkit": str(toolkit.relative_to(PROGRAM)), "original_toolkit": str(root),
        "setools_version": version, "configured_device": device, "port": port,
        "binary_sha256": info["binary_sha256"], "binary_bytes": info["binary_bytes"],
        "build_manifest_sha256": info["build_manifest_sha256"],
        "config_sha256": common.sha256(config_path), "files": package_fingerprints(toolkit),
        "package_layout": map_layout,
        "device_config_included": True, "hardware_programmed": False,
    }
    (stage / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    (PROGRAM / "latest.json").write_text(json.dumps({"receipt": str((stage / "receipt.json").relative_to(PROGRAM))}, indent=2) + "\n")
    print(f"PREPARED ONLY: {stage}\nNo serial port was opened. No board was programmed.")
    print_review(receipt, stage)


def load_prepared():
    pointer = json.loads((PROGRAM / "latest.json").read_text())
    path = inside(PROGRAM / pointer["receipt"], PROGRAM)
    receipt = json.loads(path.read_text())
    if receipt.get("format") != 1 or receipt.get("target") != smoke.TARGET or receipt.get("cpu") != "M55_HP" or receipt.get("mram_address") != "0x80200000":
        raise common.SetupError("Unexpected programming receipt; prepare again.")
    validate_port(receipt["port"])
    info = check_build()
    if receipt["binary_sha256"] != info["binary_sha256"] or receipt["build_manifest_sha256"] != info["build_manifest_sha256"]:
        raise common.SetupError("The prepared package does not match the current build. Prepare again.")
    toolkit = inside(PROGRAM / receipt["toolkit"], path.parent)
    config_path = toolkit / "handgpt-smoke.json"
    if common.sha256(config_path) != receipt["config_sha256"]:
        raise common.SetupError("Prepared boot configuration changed. Prepare again.")
    config = json.loads(config_path.read_text())
    if make_toc_config(config) != config:
        raise common.SetupError("Prepared configuration no longer names the smoke image.")
    for name, digest in receipt["files"].items():
        if common.sha256(inside(toolkit / name, toolkit)) != digest:
            raise common.SetupError(f"Prepared package/tool changed: {name}. Prepare again.")
    if not CRITICAL_FILES.issubset(receipt["files"]):
        raise common.SetupError("Programming receipt lacks required package/tool identities.")
    if common.sha256(toolkit / "build" / "images" / IMAGE_NAME) != info["binary_sha256"]:
        raise common.SetupError("Prepared image hash is not the verified smoke binary.")
    validate_toolkit(toolkit)
    verify_package_map((toolkit / "build" / "app-package-map.txt").read_text(),
                       info["binary_bytes"], (toolkit / "build" / "AppTocPackage.bin").stat().st_size)
    return receipt, path.parent, toolkit


def print_review(receipt, stage):
    print(f"Target: {receipt['target']} / M55_HP, MRAM 0x80200000")
    print(f"SETOOLS: {receipt['setools_version']}; serial port: {receipt['port']}")
    print(f"Image: {IMAGE_NAME}, {receipt['binary_bytes']} bytes")
    print(f"SHA-256: {receipt['binary_sha256']}")
    print(f"Review configuration: {stage / 'setools' / 'handgpt-smoke.json'}")
    print(f"Receipt: {stage / 'receipt.json'}")
    print(f"Package map: {stage / 'setools' / 'build' / 'app-package-map.txt'}")
    print("Programming replaces the HP camera application and writes its boot table plus the baseline DEVICE configuration.")
    print("It does NOT update the system package, provision the device, erase all memory, or install debug stubs.")


def program(write=False):
    receipt, stage, toolkit = load_prepared()
    print_review(receipt, stage)
    command = [toolkit / "app-write-mram", "-p", "-c", receipt["port"]]
    print("Planned device-writing command: " + " ".join(map(str, command)))
    if not write:
        print("REVIEW ONLY. To request programming, use 'program --write'; a typed confirmation is still required.")
        return
    approved_receipt, approved_stage, approved_toolkit = receipt, stage, toolkit
    print("Close all serial monitors. Verify the attached board is the intended E8 Rev A0.")
    if input(f"Type {CONFIRMATION!r} to write the board: ").strip() != CONFIRMATION:
        raise common.SetupError("Confirmation did not match. No board write was started.")
    # Revalidate after confirmation so edits while reviewing cannot select another image.
    receipt, stage, toolkit = load_prepared()
    if receipt != approved_receipt or stage != approved_stage or toolkit != approved_toolkit:
        raise common.SetupError("The prepared package/port changed during confirmation. Review again; no write started.")
    if not stat.S_ISCHR(Path(receipt["port"]).stat().st_mode):
        raise common.SetupError("The selected serial port is not a character device.")
    run_logged([toolkit / "app-write-mram", "-p", "-c", receipt["port"]], toolkit, stage / "program.log")
    print(f"Programming tool exited successfully. Inspect {stage / 'program.log'} before resetting.")
    print("This is not an inference result. Observe/reset the board and capture SMOKE PASS/FAIL separately.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_command = commands.add_parser("prepare", help="Host-only: validate build, clone toolkit, generate package")
    prepare_command.add_argument("--setools-root", required=True)
    prepare_command.add_argument("--port", default="/dev/ttyACM0")
    commands.add_parser("review", help="Verify and display the last prepared package; never opens a serial port")
    write_command = commands.add_parser("program", help="Review by default; --write also requires typed confirmation")
    write_command.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        common.prepare_work()
        with (common.WORK / "command.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == "prepare":
                prepare(args.setools_root, args.port)
            elif args.command == "review":
                receipt, stage, _ = load_prepared()
                print_review(receipt, stage)
            else:
                program(args.write)
    except (common.SetupError, OSError, ValueError, KeyError, EOFError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
