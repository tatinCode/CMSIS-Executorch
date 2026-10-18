"""Review by default; writing the HP-only stored-image package requires --write and a terminal confirmation."""

import argparse
import copy
from datetime import datetime, timezone
import fcntl
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile

from tools import inference as common
from tools import memory_preflight as reports
from tools import prepare_stored_image as prepared


CONFIRMATION = "PROGRAM E8-HP STORED IMAGE"
DIRECT_PORT = r"/dev/tty(?:ACM|USB)[0-9]+"
ALIAS_PORT = r"/dev/serial/by-(?:id|path)/[^/]+"


def validate_port(port):
    prepared.shared.validate_port(port)
    if not re.fullmatch(rf"(?:{DIRECT_PORT}|{ALIAS_PORT})", port):
        raise common.SetupError("Select /dev/ttyACM<N>, /dev/ttyUSB<N>, or an explicit /dev/serial/by-id or by-path device.")


def port_identity(port):
    # stat/resolve only; do not open, probe, reset, or auto-detect any device.
    resolved = Path(port).resolve(strict=True)
    if not re.fullmatch(DIRECT_PORT, str(resolved)):
        raise common.SetupError("The serial alias must resolve to a ttyACM or ttyUSB device.")
    info = resolved.stat()
    if not stat.S_ISCHR(info.st_mode):
        raise common.SetupError("The selected serial port is not a character device.")
    return {"resolved": str(resolved), "device": info.st_dev, "inode": info.st_ino, "rdev": info.st_rdev}


def print_review(receipt, stage, port):
    layout = receipt["package_layout"]
    print(f"Target: stored-image / DevKit-E8-HP / M55_HP ONLY; port: {port}")
    print(f"Image: {prepared.IMAGE_NAME}, {receipt['binary_bytes']} bytes, MRAM [{layout['image_address']}, {layout['image_end']})")
    print(f"SHA-256: {receipt['binary_sha256']}")
    print(f"DEVICE/certificates/ATOC: [{layout['package_address']}, {layout['package_end']}), {layout['package_bytes']} bytes")
    print(f"Receipt: {stage / 'receipt.json'}")
    print("WARNING: Writing replaces the on-board smoke/Viewfinder application and reclaims default HE/user MRAM.")
    print("This board must be dedicated to HandGPT, HP only; no HE/A32 firmware or data there will be preserved.")
    print("No full erase, system-package update, provisioning, discovery, or debug stubs are requested.")


def run_writer(command, toolkit, log):
    # Unlike the legacy smoke transport, do not give the child a terminal input
    # stream. An unexpected prompt (notably a part/revision mismatch override)
    # must fail, not accept a second user confirmation inside SETOOLS.
    print("+ " + shlex.join(map(str, command)), flush=True)
    with log.open("wb") as output:
        with subprocess.Popen(list(map(str, command)), cwd=toolkit, stdin=subprocess.DEVNULL,
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
    text = log.read_text(errors="replace")
    if result or "[ERROR]" in text or "Traceback (most recent call last):" in text:
        raise common.SetupError(f"Programming tool failed (exit {result}); inspect {log}. Do not override device mismatch warnings.")


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
    print("Close all serial monitors. Check this is the intended E8 Rev A0 board.")
    print("Before changing SW4: disconnect board power/USB, select SE, then reconnect for SETOOLS.")
    print("SETOOLS normally resets the board to enter maintenance mode and again after the update.")
    print("Additional SETOOLS prompts are disabled; investigate any device mismatch instead of overriding it.")
    print("Do not change the board, cable, port, build, or package during confirmation.")
    if input(f"Type {CONFIRMATION!r} to write this package: ").strip() != CONFIRMATION:
        raise common.SetupError("Confirmation did not match. No board write was started.")
    # Repeat the complete build, model/reference, package/map/TOC, tool and
    # configuration verification after the human has reviewed the exact image.
    current_receipt, current_stage = prepared.load_prepared()
    if (current_receipt != approved_receipt or current_stage != stage
            or common.sha256(stage / "receipt.json") != receipt_sha256):
        raise common.SetupError("Prepared package changed during confirmation. Review again; no write started.")
    if port_identity(port) != approved_port:
        raise common.SetupError("Serial device changed during confirmation. Review again; no write started.")

    # Keep each attempt/log separate. Never mutate the immutable preparation
    # receipt or claim hardware inference acceptance merely from a tool exit.
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
    print("Disconnect power before selecting SW4 U4; reconnect, capture UART IMAGE PASS/FAIL, and repeat from cold boot.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("review", help="Revalidate the stored-image package; no device access")
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
    except (common.SetupError, OSError, ValueError, KeyError, EOFError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
