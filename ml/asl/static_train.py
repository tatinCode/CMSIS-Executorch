"""26-output frozen-feature experiment. No J/Z/del/space images or device access."""

import argparse
import copy
import json
from pathlib import Path
import random
import time

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset, WeightedRandomSampler

from tools.asl_contract import STATIC_CLASSES, STATIC_LETTERS
from .adapt import load_camera_data, sampling_weights, balanced_loss
from .camera_data import write_json
from .data import sha256
from .model import load_checkpoint, contract
from .static_data import StaticImages, validate_manifest, select_records, decisions
from .train import features


def expand_head(model, old_classes):
    """Remap by NAME, never assume old indices survive alphabet expansion."""
    old = copy.deepcopy(model.classifier[1])
    if old.out_features != len(old_classes) or any(c not in STATIC_CLASSES for c in old_classes):
        raise ValueError("Checkpoint classifier does not match its ordered labels")
    head = nn.Linear(model.last_channel, len(STATIC_CLASSES))
    with torch.no_grad():
        for index, label in enumerate(old_classes):
            target = STATIC_CLASSES.index(label)
            head.weight[target].copy_(old.weight[index])
            head.bias[target].copy_(old.bias[index])
    model.requires_grad_(False)
    model.classifier = nn.Sequential(nn.Dropout(0.2), head)
    model.eval()


def train(args):
    if args.output.exists():
        raise ValueError("Choose a new static training directory; never overwrite a run")
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.use_deterministic_algorithms(True)
    started = time.monotonic()
    source = json.loads(args.manifest.read_text())
    validate_manifest(source)
    source_root = Path(source["root"])
    camera_root, camera, _, _ = load_camera_data(args.camera_manifest, args.label_review)
    model, old = load_checkpoint(args.checkpoint)
    if old["schema"] != 2 or old["preprocessing"] != contract(128):
        raise ValueError("Start from the preserved seven-class head-only 128x128 checkpoint")
    expand_head(model, old["classes"])
    backbone = {k: v.detach().clone() for k, v in model.features.state_dict().items()}
    selected = {"camera_train": [r for r in camera if r["split"] == "train"],
                "camera_val": [r for r in camera if r["split"] == "val"],
                **{f"kaggle_{s}": select_records(source, s, args.train_per_class if s == "train" else args.eval_per_class,
                                               args.seed) for s in ("train", "val", "test")}}
    for key, rows in selected.items():
        root = camera_root if key.startswith("camera_") else source_root
        if any(sha256(root / r["path"]) != r["sha256"] for r in rows):
            raise ValueError("Selected image changed after preparation")
    bound = (args.manifest, args.camera_manifest, args.label_review, args.checkpoint, Path("tools/asl_contract.py"),
             *(Path(__file__).parent / name for name in
               ("static_train.py", "static_data.py", "model.py", "train.py", "data.py", "adapt.py", "camera_data.py")))
    identities = {str(p): sha256(p) for p in bound}
    args.output.mkdir(parents=True)
    write_json(args.output / "selected.json", selected)
    config = dict(classes=list(STATIC_CLASSES), preprocessing=contract(128), seed=args.seed,
                  input_sha256=identities, source_manifest=str(args.manifest), camera_manifest=str(args.camera_manifest),
                  label_review=str(args.label_review), frozen_backbone=True, camera_fraction=args.camera_fraction,
                  training_per_class=args.train_per_class, eval_per_class=args.eval_per_class,
                  threshold=0.8, threshold_calibrated=False, final_test_evaluated=False,
                  selection="mean of camera and Kaggle development class-balanced cross-entropy",
                  camera_unvalidated_letters=[c for c in STATIC_LETTERS if not any(r['label'] == c for r in selected['camera_val'])])
    write_json(args.output / "config.json", config)

    def extract(key, augment=False):
        root = camera_root if key.startswith("camera_") else source_root
        return features(model, StaticImages(root, selected[key], augment), args.batch_size,
                        key + ("/augmented" if augment else "/base"))

    cx, cy = extract("camera_train")
    for _ in range(3):
        x, y = extract("camera_train", True)
        cx, cy = torch.cat((cx, x)), torch.cat((cy, y))
    kx, ky = extract("kaggle_train")
    x, y = extract("kaggle_train", True)
    kx, ky = torch.cat((kx, x)), torch.cat((ky, y))
    vx, vy = extract("camera_val")
    kvx, kvy = extract("kaggle_val")
    tx, ty = torch.cat((cx, kx)), torch.cat((cy, ky))
    sampler = WeightedRandomSampler(sampling_weights(ty, len(cy), args.camera_fraction), args.samples_per_epoch,
                                   replacement=True, generator=torch.Generator().manual_seed(args.seed))
    loader = DataLoader(TensorDataset(tx, ty), batch_size=args.batch_size, sampler=sampler)
    optimizer = torch.optim.Adam(model.classifier.parameters(), lr=0.001, weight_decay=1e-4)
    history, best, state, best_epoch = [], float("inf"), None, None
    for epoch in range(1, args.epochs + 1):
        model.classifier.train()
        for x, y in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = nn.functional.cross_entropy(model.classifier(x), y)
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            camera_logits, kaggle_logits = model.classifier(vx), model.classifier(kvx)
            selection = (balanced_loss(camera_logits, vy) + balanced_loss(kaggle_logits, kvy)) / 2
            cm, _ = decisions(camera_logits, [r["label"] for r in selected['camera_val']])
            km, _ = decisions(kaggle_logits, [r["label"] for r in selected['kaggle_val']])
        if selection < best:
            best, best_epoch, state = selection, epoch, copy.deepcopy(model.classifier.state_dict())
        history.append(dict(epoch=epoch, selection_loss=selection, camera=cm, kaggle=km))
        print(f"epoch={epoch} selection={selection:.4f} camera={cm['correct_letter_count']}/{cm['letter_count']} "
              f"wrong={cm['wrong_letter_count']} negative_false={cm['negative_false_letter_count']} "
              f"kaggle_accuracy={km['raw_class_metrics']['accuracy']:.4f}", flush=True)
    model.classifier.load_state_dict(state)
    model.eval()
    if any(not torch.equal(v, model.features.state_dict()[k]) for k, v in backbone.items()):
        raise ValueError("Frozen backbone/BatchNorm state changed")
    checkpoint = args.output / "best.pth"
    torch.save(dict(schema=3, architecture="mobilenet_v2", classes=list(STATIC_CLASSES), preprocessing=contract(128),
                    state_dict=model.state_dict(), best_epoch=best_epoch, configuration=config), checkpoint)
    reloaded, _ = load_checkpoint(checkpoint)
    if any(not torch.equal(v, reloaded.state_dict()[k]) for k, v in model.state_dict().items()):
        raise ValueError("Checkpoint reload changed model tensors")
    with torch.no_grad():
        direct = torch.cat([reloaded(x) for x, _ in DataLoader(StaticImages(camera_root, selected['camera_val']),
                                                            batch_size=args.batch_size)])
        cached = model.classifier(vx)
    delta = float((direct - cached).abs().max())
    cm, cr = decisions(direct, [r['label'] for r in selected['camera_val']])
    cached_report, cached_rows = decisions(cached, [r['label'] for r in selected['camera_val']])
    if delta > 1e-4 or [(r['display'], r['top_class']) for r in cr] != [(r['display'], r['top_class']) for r in cached_rows]:
        raise ValueError("Full-model reload disagrees with cached features")
    with torch.no_grad():
        km, kr = decisions(reloaded.classifier(kvx), [r['label'] for r in selected['kaggle_val']])
    # Evaluate dataset test only once AFTER checkpoint selection; never camera final.
    testx, _ = extract('kaggle_test')
    with torch.no_grad():
        tm, tr = decisions(reloaded.classifier(testx), [r['label'] for r in selected['kaggle_test']])
    for name, report, rows in (("camera-validation", cm, cr), ("kaggle-validation", km, kr), ("kaggle-test", tm, tr)):
        write_json(args.output / (name + '.json'), dict(metrics=report, predictions=rows))
    if any(sha256(p) != h for p, h in identities.items()):
        raise ValueError("Training source or manifest changed during run")
    load_camera_data(args.camera_manifest, args.label_review)
    for key, rows in selected.items():
        root = camera_root if key.startswith('camera_') else source_root
        if any(sha256(root / r['path']) != r['sha256'] for r in rows):
            raise ValueError("Selected image changed during run")
    write_json(args.output / 'history.json', history)
    write_json(args.output / 'metrics.json', dict(schema=3, checkpoint_sha256=sha256(checkpoint), best_epoch=best_epoch,
        camera_validation=cm, kaggle_validation=km, kaggle_test=tm, frozen_backbone_verified=True,
        full_model_reload_max_logit_difference=delta, elapsed_seconds=time.monotonic()-started,
        final_test_evaluated=False, dataset_test_evaluated=True, hardware_verified=False, deployment_approved=False,
        camera_unvalidated_letters=config['camera_unvalidated_letters']))


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--manifest', type=Path, default=Path('.work/asl/static-dataset-v1/manifest.json'))
    parser.add_argument('--camera-manifest', type=Path, default=Path('.work/asl/camera-audit-v1/manifest.json'))
    parser.add_argument('--label-review', type=Path, default=Path('.work/asl/camera-audit-v1/label-review.json'))
    parser.add_argument('--checkpoint', type=Path, default=Path('.work/asl/adapt-v1/best.pth'))
    parser.add_argument('--output', type=Path, default=Path('.work/asl/static-train-v1'))
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--train-per-class', type=int, default=600)
    parser.add_argument('--eval-per-class', type=int, default=100)
    parser.add_argument('--epochs', type=int, default=40)
    parser.add_argument('--samples-per-epoch', type=int, default=8192)
    parser.add_argument('--camera-fraction', type=float, default=0.5)
    parser.add_argument('--seed', type=int, default=2026)
    args = parser.parse_args()
    if min(args.threads, args.batch_size, args.train_per_class, args.eval_per_class, args.epochs, args.samples_per_epoch) < 1 or not 0 < args.camera_fraction < 1:
        parser.error('Positive counts and camera fraction strictly between zero and one required')
    train(args)


if __name__ == '__main__':
    main()
