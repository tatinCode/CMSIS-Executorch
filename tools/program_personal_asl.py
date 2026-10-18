"""Personal trial review by default; writing requires --write and typed confirmation."""

import importlib.util
from tools import prepare_personal_asl as prepared, program_camera_inference as writer, program_asl_live as live

CONFIRMATION='PROGRAM E8-HP PERSONAL ASL'


def print_review(receipt,stage,port):
    prepared.print_review(receipt,stage)
    print(f'Selected port: {port}; writing replaces the collector/current HP application with experimental personal ASL.')


def writer_engine():
    spec=importlib.util.spec_from_file_location('tools._personal_asl_writer',writer.__file__)
    engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
    engine.prepared,engine.CONFIRMATION=prepared,CONFIRMATION
    engine.print_review,engine.print=print_review,live.writer_print
    engine.__file__,engine.__doc__=__file__,__doc__
    return engine


def program(port,write=False):return writer_engine().program(port,write)
def main():return writer_engine().main()


if __name__=='__main__':raise SystemExit(main())
