"""Frozen best-effort demo package; accuracy limits explicitly accepted, numerical checks unchanged."""

import importlib.util
import sys
from pathlib import Path
from tools import replay_asl, inference as common

WORKSPACE = common.WORK / 'asl-demo-firmware-v2'
PROGRAM = common.WORK / 'asl-demo-programming-v2'
EXPORT = common.ROOT / '.work/asl/demo-export-v1'
CHECKPOINT = '0d3f831307bfcd19e8f7f7c4f201a2ba8c48a453576324c92345f59c1deb300d'
CONFIRMATION = 'PROGRAM E8-HP BEST EFFORT ASL'


def validate_demo(value):
    if (value.get('replay_trial') is not True or value.get('checkpoint_sha256') != CHECKPOINT or
            value.get('deployment_approved') is not False or value.get('hardware_verified') is not False):
        raise common.SetupError('Use only the frozen, explicitly accepted best-effort candidate')
    return value


def engine():
    spec = importlib.util.spec_from_file_location('tools._frozen_demo', replay_asl.__file__)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.WORKSPACE, module.PROGRAM, module.EXPORT = WORKSPACE, PROGRAM, EXPORT
    module.CONFIRMATION = CONFIRMATION
    module.validate_replay = validate_demo
    original_builder = module.builder
    original_helpers = module.helper_identities
    def builder():
        result = original_builder()
        paths = result.source_paths
        result.source_paths = lambda: paths() | {Path(__file__)}
        result.LIVE = {**result.LIVE, 'known_accuracy_failures_accepted':True}
        return result
    module.builder = builder
    module.helper_identities = lambda: {**original_helpers(), 'demo_asl.py':common.sha256(Path(__file__))}
    def review(receipt,stage):
        print('FROZEN BEST-EFFORT DEMO: ACCURACY FAILURES ACCEPTED BY USER; NOT VERIFIED ON HARDWARE.')
        print('Known old-camera int8 results: A 2/6, L 3/7, Y 3/6; threshold remains 0.80.')
        print('Both quantization accuracy gates FAIL. Numerical boot checks are NOT relaxed.')
        print(f"Binary {receipt['binary_bytes']} bytes; SHA-256 {receipt['binary_sha256']}")
        print(f'Receipt: {stage / "receipt.json"}; writer confirmation: {CONFIRMATION}')
    module.print_review = review
    return module


def main():
    if len(sys.argv)>1 and sys.argv[1]=='build':
        acknowledgement = '--accept-known-accuracy-limitations'
        if acknowledgement not in sys.argv:
            raise common.SetupError('Require --accept-known-accuracy-limitations for this best-effort build')
        sys.argv.remove(acknowledgement)
    return engine().main()


if __name__ == '__main__':
    main()
