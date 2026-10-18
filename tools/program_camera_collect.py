"""Review by default. A continuous-collector write requires typed confirmation."""

import builtins
import importlib.util
from tools import prepare_camera_collect as prepared, program_camera_inference as camera_writer

CONFIRMATION='PROGRAM E8-HP CONTINUOUS COLLECTOR'


def print_review(receipt,stage,port):
    prepared.print_review(receipt,stage)
    print(f'Selected port: {port}; writing replaces live recognition with DATA COLLECTION.')


def writer_print(*args,**kwargs):
    old='Require COEXIST SELFTEST PASS and continued PREVIEW progress without later FAIL; repeat from cold boot.'
    if len(args)==1 and args[0]==old:
        args=('Require COEXIST SELFTEST PASS, COLLECT READY v=5, repeated CRC-checked cycles and upright preview; repeat from cold boot.',)
    builtins.print(*args,**kwargs)


def writer_engine():
    spec=importlib.util.spec_from_file_location('tools._collect_writer',camera_writer.__file__)
    engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
    engine.prepared,engine.CONFIRMATION=prepared,CONFIRMATION
    engine.print_review,engine.print=print_review,writer_print
    engine.__file__,engine.__doc__=__file__,__doc__
    return engine


def program(port,write=False):return writer_engine().program(port,write)
def main():return writer_engine().main()


if __name__=='__main__':raise SystemExit(main())
