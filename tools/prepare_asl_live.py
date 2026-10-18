"""Prepare/review the experimental live ASL package, without hardware access."""

import argparse
import fcntl
import importlib.util
from pathlib import Path

from tools import asl_live_firmware as live
from tools import inference as common
from tools import prepare_camera_inference as capture
from tools import program_smoke as shared


PROGRAM = common.WORK / "asl-static-programming-v1"
TARGET = "asl-live-experimental.debug+DevKit-E8-HP"
IMAGE_NAME = "handgpt-asl-live-experimental.bin"
CONFIG_NAME = "handgpt-asl-live-experimental.json"


def helper_identities():
    names = ("prepare_asl_live.py", "program_asl_live.py", "asl_live_firmware.py", "inspect_asl_pte.py", "asl_selftest.py", "asl_contract.py",
             "prepare_camera_inference.py", "program_camera_inference.py", "camera_inference_firmware.py",
             "prepare_stored_image.py", "program_stored_image.py", "program_smoke.py", "smoke_firmware.py",
             "stored_image_firmware.py", "inference.py", "memory_preflight.py", "firmware_layouts.py")
    return {n: common.sha256(common.ROOT / "tools" / n) for n in names}


def print_review(receipt, stage):
    layout = receipt["package_layout"]
    print(f"EXPERIMENTAL LIVE ASL / NOT DEMO-APPROVED: {stage}")
    print(f"Image: {IMAGE_NAME}; {receipt['binary_bytes']} bytes; SHA-256 {receipt['binary_sha256']}")
    print(f"HP application: [{layout['image_address']}, {layout['image_end']})")
    print(f"DEVICE/certificates/ATOC: [{layout['package_address']}, {layout['package_end']}); {layout['package_bytes']} bytes")
    print("Real camera -> U85; 24 static letters (no J/Z/del/space); NO_HAND/OTHER/uncertainty -> --; no cycling.")
    print("19 letters have NO reviewed E8-camera examples. All letters remain experimental.")
    print("Per-class float/PT2E metrics and quantization nonregression results are in the bound export manifest.")
    print("Kaggle metrics are not camera accuracy; this package is NOT deployment-approved.")
    print("ASL exact-input boot self-test replaces ImageNet ONLY in this separate image; fallbacks retained.")
    print("GATE REVISION 2 (post-hoc, user-approved): <=8 output steps; cosine>=0.999; top-1 and 0.80 display decision match.")
    print("Previous v5 boot failed gate 0.5 at 0.557068 (error 325); this revision still needs hardware acceptance.")
    print("Whole LCD rotated 180; original camera/ML orientation unchanged; no boot frame export.")
    print(f"Receipt: {stage / 'receipt.json'}; hardware verification pending.")
    print("Preparation/review only: no serial port opened or board programmed.")


def package_engine():
    spec = importlib.util.spec_from_file_location("tools._live_package", capture.__file__)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    engine.PROGRAM, engine.FIRMWARE = PROGRAM, live.WORKSPACE
    engine.TARGET, engine.IMAGE_NAME, engine.CONFIG_NAME = TARGET, IMAGE_NAME, CONFIG_NAME
    engine.CRITICAL = (shared.CRITICAL_FILES - {"build/images/handgpt-smoke.bin"}) | {f"build/images/{IMAGE_NAME}"}
    engine.check_build = live.check_build
    engine.helper_identities, engine.print_review = helper_identities, print_review
    return engine


def prepare(setools, directory, hp_only_board, experimental):
    if not experimental:
        raise common.SetupError("Explicit --experimental is required; model is not deployment-approved")
    root = Path(setools).expanduser().resolve(strict=True)
    if root.is_relative_to(common.WORK.resolve()) or common.WORK.resolve().is_relative_to(root):
        raise common.SetupError("Use installed SETOOLS outside private staging/builds")
    return package_engine().prepare(root, Path(directory).resolve(strict=True) / "manifest.json", hp_only_board)


def load_prepared():
    return package_engine().load_prepared()


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare", allow_abbrev=False)
    create.add_argument("--setools-root", required=True)
    create.add_argument("--export", type=Path, required=True)
    create.add_argument("--experimental", action="store_true")
    create.add_argument("--hp-only-board", action="store_true")
    commands.add_parser("review", allow_abbrev=False)
    args = parser.parse_args()
    with (common.WORK / "command.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == "prepare":
            prepare(args.setools_root, args.export, args.hp_only_board, args.experimental)
        else:
            print_review(*load_prepared())


if __name__ == "__main__":
    main()
