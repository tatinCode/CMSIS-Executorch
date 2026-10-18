"""Separate replay candidate: offline build/prepare/review; deliberate confirmed program only."""

import argparse
import fcntl
import json
from pathlib import Path
from tools import inference as common, personal_asl_firmware as firmware
from tools import prepare_personal_asl as preparation, program_personal_asl as programmer

WORKSPACE = common.WORK / 'asl-replay-firmware-v1'
PROGRAM = common.WORK / 'asl-replay-programming-v1'
EXPORT = common.ROOT / '.work/asl/replay-export-v1'
CONFIRMATION = 'PROGRAM E8-HP REPLAY ASL'


def finalize_export(directory):
    """Keep the acceptance receipt outside the hash-bound export inventory."""
    directory = Path(directory).resolve(strict=True)
    receipt = directory / 'replay-acceptance.json'
    if not receipt.exists():
        return
    manifest = directory / 'manifest.json'
    value = json.loads(receipt.read_text())
    if value.get('manifest_sha256') != common.sha256(manifest):
        raise common.SetupError('Replay acceptance receipt does not bind this export')
    outside = directory.with_name(directory.name + '-acceptance.json')
    if outside.exists():
        raise common.SetupError('Acceptance receipt destination already exists; do not overwrite')
    receipt.rename(outside)


def validate_replay(value):
    report = value.get('pt2e_quantized_validation', {})
    floors = {'A': 3, 'L': 5, 'Y': 3}
    if (value.get('replay_trial') is not True or value.get('protected_letters_int8_passed') is not True or
            value.get('protected_display_floors') != floors or
            report.get('negative_false_letter_count') != 0 or report.get('wrong_letter_count', 999) > 1 or
            any(report.get('per_class', {}).get(c, {}).get('correct_display_count', -1) < n for c, n in floors.items())):
        raise common.SetupError('Replay int8 A/L/Y acceptance failed; do not package a regression')
    return value


def builder():
    engine = firmware.private_builder()
    engine.WORKSPACE = WORKSPACE
    sources = engine.source_paths
    engine.source_paths = lambda: sources() | {Path(__file__)}
    export_info = engine.export_info
    engine.export_info = lambda directory, inspect_binary=False: validate_replay(export_info(directory, inspect_binary))
    return engine


def check_build(image):
    return builder().check_build(image)


def helper_identities():
    return {**preparation.helper_identities(), 'replay_asl.py': common.sha256(Path(__file__))}


def print_review(receipt, stage):
    print('REPLAY CANDIDATE: old A/L/Y + all reviewed new captures. NOT hardware verified.')
    preparation.print_review(receipt, stage)


def package_engine():
    engine = preparation.package_engine()
    engine.PROGRAM, engine.FIRMWARE = PROGRAM, WORKSPACE
    engine.check_build = check_build
    engine.helper_identities = helper_identities
    engine.print_review = print_review
    return engine


def load_prepared():
    return package_engine().load_prepared()


def writer_engine():
    import sys
    engine = programmer.writer_engine()
    engine.prepared = sys.modules[__name__]
    engine.CONFIRMATION = CONFIRMATION
    engine.__file__ = __file__
    def review(receipt, stage, port):
        print_review(receipt, stage)
        print(f'Selected port: {port}; writing replaces the current HP application.')
    engine.print_review = review
    return engine


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build', allow_abbrev=False)
    build.add_argument('--export', type=Path, default=EXPORT)
    build.add_argument('--experimental', action='store_true')
    build.add_argument('--layout', choices=[firmware.live.PROFILE.name], required=True)
    build.add_argument('--jobs', type=int, default=1)
    build.add_argument('--baseline-image', type=Path, default=common.ROOT / 'ml/reference/images/cat.jpg')
    build.add_argument('--cbuild')
    build.add_argument('--toolchain-bin')
    prepare = commands.add_parser('prepare', allow_abbrev=False)
    prepare.add_argument('--export', type=Path, default=EXPORT)
    prepare.add_argument('--setools-root', type=Path, required=True)
    prepare.add_argument('--experimental', action='store_true')
    prepare.add_argument('--hp-only-board', action='store_true')
    commands.add_parser('review', allow_abbrev=False)
    program = commands.add_parser('program', allow_abbrev=False)
    program.add_argument('--port', required=True)
    program.add_argument('--write', action='store_true')
    args = parser.parse_args()
    with (common.WORK / 'command.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == 'build':
            finalize_export(args.export)
            builder().build(args)
        elif args.command == 'prepare':
            if not args.experimental:
                raise common.SetupError('Require --experimental')
            root = args.setools_root.expanduser().resolve(strict=True)
            if root.is_relative_to(common.WORK.resolve()) or common.WORK.resolve().is_relative_to(root):
                raise common.SetupError('Use installed SETOOLS outside staging')
            package_engine().prepare(root, args.export.resolve(strict=True) / 'manifest.json', args.hp_only_board)
        elif args.command == 'review':
            print_review(*load_prepared())
        else:
            writer_engine().program(args.port, args.write)


if __name__ == '__main__':
    main()
