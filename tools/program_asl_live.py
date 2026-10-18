"""Review by default. Experimental LIVE ASL writes require --write and typed confirmation."""

import builtins
import importlib.util

from tools import prepare_asl_live as prepared
from tools import program_camera_inference as camera_writer


CONFIRMATION = "PROGRAM E8-HP EXPERIMENTAL LIVE ASL"


def print_review(receipt, stage, port):
    prepared.print_review(receipt, stage)
    print(f"Selected port: {port}; target: {prepared.TARGET}")
    print("Writing replaces the current HP application with experimental real recognition, not TEST UI.")


def writer_print(*args, **kwargs):
    # Only adapt the reused writer's final acceptance wording; never suppress
    # writer output, errors, warnings or audit records.
    old = "Require COEXIST SELFTEST PASS and continued PREVIEW progress without later FAIL; repeat from cold boot."
    if len(args) == 1 and args[0] == old:
        args = ("Require ASL SELFTEST PASS (three repeats), LIVE ASL START, increasing predictions and fault=0; repeat from cold boot.",)
    builtins.print(*args, **kwargs)


def writer_engine():
    spec = importlib.util.spec_from_file_location("tools._live_writer", camera_writer.__file__)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    engine.prepared = prepared
    engine.CONFIRMATION = CONFIRMATION
    engine.print_review = print_review
    engine.print = writer_print
    engine.__file__, engine.__doc__ = __file__, __doc__
    return engine


def program(port, write=False):
    writer_engine().program(port, write)


def main():
    return writer_engine().main()


if __name__ == "__main__":
    raise SystemExit(main())
