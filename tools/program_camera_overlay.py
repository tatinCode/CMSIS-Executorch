"""TEST UI package review by default; writing requires --write and exact typed confirmation."""

import argparse
import fcntl
import importlib.util
from pathlib import Path
import subprocess
import sys

from tools import inference as common
from tools import prepare_camera_overlay as prepared
from tools import program_camera_inference as camera_writer


CONFIRMATION = "PROGRAM E8-HP TEST UI"


def print_review(receipt, stage, port):
    prepared.print_review(receipt, stage)
    print(f"Selected port: {port}; target: {prepared.TARGET}")
    print("Writing replaces the HP application with the TEST UI, not a trained ASL model.")
    print("HP-only dedicated board; existing camera/NPU memory profile retained.")


def writer_engine():
    """Retain the tested terminal/port/revalidation/write-log contract verbatim.

    Only package selection, review wording, confirmation and helper identity are
    changed in this private instance. The ordinary camera writer is untouched.
    """
    spec = importlib.util.spec_from_file_location("tools._overlay_writer", camera_writer.__file__)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    engine.prepared = prepared
    engine.CONFIRMATION = CONFIRMATION
    engine.print_review = print_review
    engine.__file__ = __file__  # Bind the attempt record to this entry point.
    return engine


def program(port, write=False):
    writer_engine().program(port, write)
    if write:
        print("Check upright text and moving preview: the whole LCD is rotated 180 degrees.")
        print("Check TEST UI / NOT LIVE ASL and all six cycling symbols; saved captures stay upright and unchanged.")
        print("The boot export briefly freezes preview; verify it resumes and repeat from cold power-on.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("review", allow_abbrev=False)
    writer = commands.add_parser("program", allow_abbrev=False)
    writer.add_argument("--port", required=True)
    writer.add_argument("--write", action="store_true", help="Write only after interactive confirmation and revalidation")
    args = parser.parse_args()
    try:
        common.prepare_work()
        with (common.WORK / "command.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == "review":
                prepared.print_review(*prepared.load_prepared())
            else:
                program(args.port, args.write)
    except KeyboardInterrupt:
        print("Cancelled. If writing started, inspect the attempt log before any further action.", file=sys.stderr)
        return 130
    except (common.SetupError, OSError, ValueError, KeyError, TypeError, EOFError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
