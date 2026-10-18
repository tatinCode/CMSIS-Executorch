"""Isolated personal-trial build; existing static live image/pointer remain unchanged."""

import argparse
import fcntl
import importlib.util
from pathlib import Path
from tools import asl_live_firmware as live, inference as common

WORKSPACE=common.WORK/'asl-personal-firmware-v1'


def validate_personal_export(value):
    count=value.get('new_training_count')
    fits=value.get('new_camera_training_fit_only')
    if (value.get('personal_trial') is not True or value.get('new_camera_validation') is not False or
            type(count) is not int or count<=0 or not isinstance(fits,dict) or
            any(fits.get(k,{}).get('count')!=count for k in ('float','pt2e'))):
        raise common.SetupError('Personal package requires a reviewed personal export, NOT the old static model')
    return value


def private_builder():
    spec=importlib.util.spec_from_file_location('tools._personal_live_builder',live.__file__)
    engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
    engine.WORKSPACE=WORKSPACE
    original_sources=engine.source_paths
    engine.source_paths=lambda:original_sources()|{Path(__file__)}
    original_export=engine.export_info
    def personal_export(directory,inspect_binary=False):
        return validate_personal_export(original_export(directory,inspect_binary))
    engine.export_info=personal_export
    return engine


def check_build(image):return private_builder().check_build(image)


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--export',type=Path,default=Path('.work/asl/personal-export-v1'))
    parser.add_argument('--experimental',action='store_true')
    parser.add_argument('--layout',choices=[live.PROFILE.name],required=True)
    parser.add_argument('--jobs',type=int,default=1)
    parser.add_argument('--baseline-image',type=Path,default=common.ROOT/'ml/reference/images/cat.jpg')
    parser.add_argument('--cbuild')
    parser.add_argument('--toolchain-bin')
    args=parser.parse_args()
    with (common.WORK/'command.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        private_builder().build(args)


if __name__=='__main__':main()
