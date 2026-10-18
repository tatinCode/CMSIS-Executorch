"""Offline preparation of the separate experimental personal ASL package."""

import argparse
import fcntl
from pathlib import Path
from tools import prepare_asl_live as live, personal_asl_firmware as firmware, inference as common

ROOT=common.ROOT
FIRMWARE=firmware.WORKSPACE
PROGRAM=common.WORK/'asl-personal-programming-v1'
TARGET='asl-personal-experimental.debug+DevKit-E8-HP'
IMAGE_NAME='handgpt-asl-personal-experimental.bin'
CONFIG_NAME='handgpt-asl-personal-experimental.json'
PROFILE=live.live.PROFILE


def helper_identities():
    values=live.helper_identities()
    values.update({name:common.sha256(ROOT/'tools'/name) for name in
        ('prepare_personal_asl.py','program_personal_asl.py','personal_asl_firmware.py')})
    return values


def print_review(receipt,stage):
    print(f'PERSONAL ASL EXPERIMENT / NOT DEMO-APPROVED: {stage}')
    print(f"Image: {IMAGE_NAME}; {receipt['binary_bytes']} bytes; SHA-256 {receipt['binary_sha256']}")
    print('24 static letters, NO_HAND/OTHER; threshold 0.80. No J/Z/delete/space or name accumulation.')
    print('Old reviewed training + new reviewed TRAIN ONLY + Kaggle. Old validation NEVER trained/calibrated.')
    print('New-letter training-fit is NOT held-out accuracy; single-presenter trial requires fresh hardware testing.')
    print('Unchanged revision-2 numerical gate: 8 output steps, cosine >=0.999, matching top-1 and display decision.')
    print(f"Receipt: {stage/'receipt.json'}; no serial access in preparation/review. Hardware acceptance pending.")


def package_engine():
    engine=live.package_engine()
    engine.PROGRAM,engine.FIRMWARE=PROGRAM,FIRMWARE
    engine.TARGET,engine.IMAGE_NAME,engine.CONFIG_NAME=TARGET,IMAGE_NAME,CONFIG_NAME
    engine.CRITICAL=(engine.CRITICAL-{f'build/images/{live.IMAGE_NAME}'})|{f'build/images/{IMAGE_NAME}'}
    engine.check_build=firmware.check_build
    engine.helper_identities,engine.print_review=helper_identities,print_review
    return engine


def prepare(setools,directory,hp_only_board=False,experimental=False):
    if not experimental:raise common.SetupError('Require --experimental; this is not deployment approved')
    root=Path(setools).expanduser().resolve(strict=True)
    if root.is_relative_to(common.WORK.resolve()) or common.WORK.resolve().is_relative_to(root):
        raise common.SetupError('Use installed SETOOLS outside staging')
    return package_engine().prepare(root,Path(directory).resolve(strict=True)/'manifest.json',hp_only_board)


def load_prepared():return package_engine().load_prepared()


def main():
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    commands=parser.add_subparsers(dest='command',required=True)
    create=commands.add_parser('prepare',allow_abbrev=False)
    create.add_argument('--setools-root',required=True)
    create.add_argument('--export',type=Path,default=Path('.work/asl/personal-export-v1'))
    create.add_argument('--hp-only-board',action='store_true')
    create.add_argument('--experimental',action='store_true')
    commands.add_parser('review',allow_abbrev=False)
    args=parser.parse_args()
    with (common.WORK/'command.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.command=='prepare':prepare(args.setools_root,args.export,args.hp_only_board,args.experimental)
        else:print_review(*load_prepared())


if __name__=='__main__':main()
