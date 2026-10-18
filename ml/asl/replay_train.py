"""Old-camera replay with explicit A/L/Y display floors; isolated head-only trial."""

import argparse
import importlib.util
from pathlib import Path

import torch

from . import personal_train
from .data import sha256
from .export import quantization_gate


FLOORS = {'A': 3, 'L': 5, 'Y': 3}


def protected_gate(before, after):
    return quantization_gate(before, after) and all(
        after['per_class'][label]['correct_display_count'] >= count
        for label, count in FLOORS.items())


def train(args):
    spec = importlib.util.spec_from_file_location('ml.asl._replay_training', personal_train.__file__)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    engine.quantization_gate = protected_gate
    original_write = engine.write_json
    original_features = engine.features

    def write(path, value):
        if Path(path).name == 'config.json':
            value['input_sha256'][str(Path(__file__).resolve())] = sha256(__file__)
            value['protected_display_floors'] = FLOORS
            value['selection'] = 'minimum joint development loss among trained epochs passing aggregate camera nonregression AND A>=3/6, L>=5/7, Y>=3/6 at unchanged 0.80'
            value['selection_caveat'] = 'Development tuning, not final evaluation; floors preserve prior development counts, not proof of live recovery.'
        original_write(path, value)

    def features(model, dataset, batch_size, description):
        result = original_features(model, dataset, batch_size, description)
        cache = args.output / 'features'
        cache.mkdir(exist_ok=True)
        name = description.replace('/', '-')
        index = len(list(cache.glob(name + '-*.pt')))
        torch.save(dict(x=result[0], y=result[1]), cache / f'{name}-{index}.pt')
        return result

    engine.write_json = write
    engine.features = features
    engine.train(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--camera-manifest', type=Path, default=Path('.work/asl/personal-camera-v4/manifest.json'))
    parser.add_argument('--label-review', type=Path, default=Path('.work/asl/personal-review-v2/label-review.json'))
    parser.add_argument('--manifest', type=Path, default=Path('.work/asl/static-dataset-v1/manifest.json'))
    parser.add_argument('--checkpoint', type=Path, default=Path('.work/asl/personal-train-v2/best.pth'))
    parser.add_argument('--output', type=Path, default=Path('.work/asl/replay-train-v1'))
    parser.add_argument('--epochs', type=int, default=60)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--train-per-class', type=int, default=600)
    parser.add_argument('--eval-per-class', type=int, default=100)
    parser.add_argument('--camera-fraction', type=float, default=0.9)
    parser.add_argument('--samples-per-epoch', type=int, default=8192)
    parser.add_argument('--learning-rate', type=float, default=0.001)
    parser.add_argument('--seed', type=int, default=2026)
    args = parser.parse_args()
    args.adapted_trial = True
    if (min(args.epochs, args.threads, args.batch_size, args.train_per_class,
            args.eval_per_class, args.samples_per_epoch) < 1 or
            not 0 < args.camera_fraction < 1 or not 0 < args.learning_rate < float('inf')):
        parser.error('Invalid training parameters')
    train(args)


if __name__ == '__main__':
    main()
