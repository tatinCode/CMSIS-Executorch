"""Review by default; camera coexistence writes require --write and typed terminal confirmation."""

import argparse
import copy
from datetime import datetime, timezone
import fcntl
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from tools import inference as common
from tools import memory_preflight as reports
from tools import prepare_camera_inference as prepared
from tools.program_stored_image import port_identity, run_writer, validate_port


CONFIRMATION = "PROGRAM E8-HP CAMERA INFERENCE"


def print_review(receipt, stage, port):
    layout = receipt["package_layout"]
    print(f"Target: camera-inference / DevKit-E8-HP / M55_HP ONLY; port: {port}")
    print(f"Image: {prepared.IMAGE_NAME}, {receipt['binary_bytes']} bytes, MRAM [{layout['image_address']}, {layout['image_end']})")
    print(f"SHA-256: {receipt['binary_sha256']}")
    print("Capture v4: full-view 128x128 RGB888P; 49,152-byte payload (not the v3 full-resolution image).")
    print(f"DEVICE/certificates/ATOC: [{layout['package_address']}, {layout['package_end']}), {layout['package_bytes']} bytes")
    print(f"Receipt: {stage / 'receipt.json'}")
    print("WARNING: Writing replaces the current HP application (including standalone stored-image inference).")
    print("This board must be dedicated to HandGPT, HP only; default HE/user MRAM is reclaimed, not preserved.")
    print("No full erase, system-package update, provisioning, discovery, or debug stubs are requested.")
    print("This tests preview + STORED-image inference, NOT camera classification; it is not yet hardware-verified.")


def program(port, write=False):
    validate_port(port)
    receipt, stage = prepared.load_prepared()
    toolkit = stage / "setools"
    command = [toolkit / "app-write-mram", "-p", "-c", port]
    print_review(receipt, stage, port)
    print("Planned device-writing command: " + shlex.join(map(str, command)))
    if not write:
        print("REVIEW ONLY: no serial port opened and no writer run. Add --write to request a typed confirmation.")
        return
    if not sys.stdin.isatty():
        raise common.SetupError("Writing requires an interactive terminal; piped/redirected confirmations are not accepted.")
    approved_receipt = copy.deepcopy(receipt)
    receipt_sha256 = common.sha256(stage / "receipt.json")
    approved_port = port_identity(port)
    print(f"Selected serial device: {port} -> {approved_port['resolved']}")
    print("Close all serial monitors. Check this is the intended E8 Rev A0 board with the MT9M114 camera/panel.")
    print("Before changing SW4: disconnect board power/USB, select SE, then reconnect for SETOOLS.")
    print("SETOOLS normally resets the board to enter maintenance mode and again after the update.")
    print("Additional SETOOLS prompts are disabled; investigate any device mismatch instead of overriding it.")
    print("Do not change the board, cable, port, build, or package during confirmation.")
    if input(f"Type {CONFIRMATION!r} to write this package: ").strip() != CONFIRMATION:
        raise common.SetupError("Confirmation did not match. No board write was started.")
    # Repeat build/source/model/runtime/SDK/layout and package/map/binary-TOC/
    # configuration/tool verification after the user approves this exact image.
    current_receipt, current_stage = prepared.load_prepared()
    if (current_receipt != approved_receipt or current_stage != stage
            or common.sha256(stage / "receipt.json") != receipt_sha256):
        raise common.SetupError("Prepared package changed during confirmation. Review again; no write started.")
    if port_identity(port) != approved_port:
        raise common.SetupError("Serial device changed during confirmation. Review again; no write started.")

    attempt_dir = Path(tempfile.mkdtemp(prefix="program-", dir=stage))
    log = attempt_dir / "program.log"
    record = {"format": 1, "status": "writer_requested", "requested_at": datetime.now(timezone.utc).isoformat(),
              "target": current_receipt["target"], "binary_sha256": current_receipt["binary_sha256"],
              "receipt_sha256": receipt_sha256, "writer_helper_sha256": common.sha256(Path(__file__)),
              "port": port, "port_identity": approved_port, "package_layout": current_receipt["package_layout"],
              "command": list(map(str, command)), "hardware_verified": False}
    record_path = attempt_dir / "attempt.json"
    reports.write_report(record_path, record)
    print(f"Starting explicitly confirmed write; log: {log}", flush=True)
    try:
        # Shared transport only: argument-list Popen, child stdin=DEVNULL,
        # merged private log, nonzero/tool-error detection. Never calls the
        # standalone stored-image program() or selects its receipt/package.
        run_writer(command, toolkit, log)
    except (Exception, KeyboardInterrupt) as error:
        record.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "writer_failed",
                      finished_at=datetime.now(timezone.utc).isoformat())
        reports.write_report(record_path, record)
        print(f"Write did not complete successfully; MRAM may be partially written. Inspect {log}. No wrapper retry/reset.", file=sys.stderr)
        raise
    record.update(status="tool_exited_successfully", finished_at=datetime.now(timezone.utc).isoformat())
    reports.write_report(record_path, record)
    print(f"Programming tool exited successfully. Inspect {log} before resetting.")
    print("This is NOT an inference result; preparation receipts/manifests remain unverified on hardware.")
    print("Disconnect power before selecting SW4 U4; reconnect and capture the full UART log plus visual preview.")
    print("Require COEXIST SELFTEST PASS and continued PREVIEW progress without later FAIL; repeat from cold boot.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("review", help="Revalidate the camera package; no device access", allow_abbrev=False)
    writer = commands.add_parser("program", help="Review only unless --write AND typed terminal confirmation", allow_abbrev=False)
    writer.add_argument("--port", required=True, help="Explicit Linux USB serial port; never auto-detected")
    writer.add_argument("--write", action="store_true", help="WRITES BOARD only after typed confirmation and revalidation")
    args = parser.parse_args()
    try:
        common.prepare_work()
        with (common.WORK / "command.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == "review":
                receipt, stage = prepared.load_prepared()
                prepared.print_review(receipt, stage)
            else:
                program(args.port, args.write)
    except KeyboardInterrupt:
        print("Cancelled. If a write had started, treat MRAM as potentially partially written; inspect its attempt log.", file=sys.stderr)
        return 130
    except (common.SetupError, OSError, ValueError, KeyError, TypeError, EOFError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
