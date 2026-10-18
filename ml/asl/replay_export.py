"""Export camera replay; record A/L/Y acceptance on the int8 model itself."""

import argparse
import json
from pathlib import Path

import torch

from . import personal_export
from .camera_data import write_json
from .data import sha256
from .personal_data import load_personal_data
from .replay_train import FLOORS
from .static_export import score


def acceptance(manifest):
    report = manifest['pt2e_quantized_validation']
    return (report['negative_false_letter_count'] == 0 and
            report['wrong_letter_count'] <= 1 and
            all(report['per_class'][c]['correct_display_count'] >= n for c, n in FLOORS.items()))


def write_final_manifest(path, manifest):
    # The underlying exporter already created manifest.json using exclusive
    # creation. Finalize our own manifest atomically, not via another 'x' open.
    temporary = path.with_name('.' + path.name + '.final')
    write_json(temporary, manifest)
    temporary.replace(path)


def run(args):
    personal_export.run(args)
    config = torch.load(args.checkpoint, map_location='cpu', weights_only=True)['configuration']
    root, records, _, _ = load_personal_data(Path(config['camera_manifest']), Path(config['label_review']))
    training = [r for r in records if r['split'] == 'train']
    model = torch.export.load(args.output / 'quantized.pt2').module(check_guards=False)
    _, training_report, predictions = score(model, root, training)
    diagnostic = args.output / 'all-camera-training-fit-NOT-validation.json'
    write_json(diagnostic, dict(metrics=training_report, predictions=predictions, held_out=False))
    path = args.output / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest.update(replay_trial=True, protected_display_floors=FLOORS,
                    all_camera_training_fit_only=training_report,
                    protected_letters_int8_passed=acceptance(manifest))
    manifest['artifact_sha256'][diagnostic.name] = sha256(diagnostic)
    write_final_manifest(path, manifest)
    write_json(args.output / 'replay-acceptance.json', dict(
        protected_letters_int8_passed=acceptance(manifest), floors=FLOORS,
        manifest_sha256=sha256(path), threshold=0.8, hardware_verified=False))
    print('A/L/Y int8 acceptance:', acceptance(manifest), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--checkpoint', type=Path, default=Path('.work/asl/replay-train-v3/best.pth'))
    parser.add_argument('--output', type=Path, default=Path('.work/asl/replay-export-v1'))
    run(parser.parse_args())


if __name__ == '__main__':
    main()
