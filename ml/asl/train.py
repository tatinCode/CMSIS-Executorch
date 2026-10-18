"""Train a frozen-feature MobileNetV2 baseline on CPU, then evaluate once."""

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import time

import numpy as np
from PIL import Image
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset, TensorDataset
import torchvision

from .data import CLASSES, SPLITS, sha256
from .model import contract, make_model, transform


def select_records(manifest, split, limit, seed):
    chosen = []
    for label in CLASSES:
        records = sorted((r for r in manifest["records"] if r["split"] == split and r["label"] == label),
                         key=lambda r: r["path"])
        random.Random(f"{seed}:{split}:{label}").shuffle(records)
        chosen.extend(records[:limit] if limit else records)
    return chosen


def validate_manifest(manifest, classes=CLASSES):
    if manifest.get("schema") != 1 or manifest.get("classes") != list(classes):
        raise ValueError("Unsupported dataset manifest or label order")
    paths, hashes, groups = set(), {}, {}
    counts = Counter()
    for record in manifest["records"]:
        path, split, label = record["path"], record["split"], record["label"]
        if split not in SPLITS or label not in classes or path in paths:
            raise ValueError("Invalid or repeated manifest record")
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise ValueError("Dataset paths must be relative and remain inside the dataset")
        paths.add(path)
        for mapping, key in ((hashes, record["pixels_sha256"]), (groups, record["group"])):
            if key in mapping and mapping[key] != split:
                raise ValueError("Cross-split duplicate/group leakage")
            mapping[key] = split
        counts[split, label] += 1
    if any(counts[s, c] == 0 for s in SPLITS for c in classes):
        raise ValueError("Every split must contain every class")


class Images(Dataset):
    def __init__(self, root, records, size, augment=False):
        self.root, self.records, self.transform = root, records, transform(size, augment)

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        record = self.records[index]
        with Image.open(self.root / record["path"]) as image:
            tensor = self.transform(image)
        return tensor, CLASSES.index(record["label"])


def features(model, dataset, batch_size, description):
    values, labels = [], []
    model.eval()
    start = time.monotonic()
    with torch.inference_mode():
        for i, (images, targets) in enumerate(DataLoader(dataset, batch_size=batch_size, num_workers=0)):
            values.append(model.features(images).mean((2, 3)))
            labels.append(targets)
            if i % 25 == 0:
                print(f"{description}: {min((i + 1) * batch_size, len(dataset))}/{len(dataset)}", flush=True)
    print(f"{description}: {time.monotonic() - start:.1f}s", flush=True)
    # Outside inference_mode, clone gives ordinary tensors usable in autograd.
    return torch.cat(values).clone(), torch.cat(labels).clone()


def metrics(logits, targets, threshold, classes=CLASSES):
    if (logits.ndim != 2 or logits.shape[1] != len(classes) or len(targets) != len(logits) or
            not len(targets) or targets.min() < 0 or targets.max() >= len(classes)):
        raise ValueError("Metrics require nonempty logits/targets matching the supplied class order")
    probabilities = logits.softmax(1)
    confidence, predicted = probabilities.max(1)
    confusion = torch.zeros((len(classes), len(classes)), dtype=torch.int64)
    for actual, prediction in zip(targets.tolist(), predicted.tolist()):
        confusion[actual, prediction] += 1
    accepted = confidence >= threshold
    support = confusion.sum(1)
    recalls = confusion.diag().float() / support.clamp_min(1)
    return dict(count=len(targets), accuracy=float((predicted == targets).float().mean()),
                 balanced_accuracy=float(recalls[support > 0].mean()), classes=list(classes),
                 per_class_support=dict(zip(classes, support.tolist())),
                 per_class_recall={label: float(recalls[i]) if support[i] else None for i, label in enumerate(classes)},
                confusion_matrix_rows_actual_columns_predicted=confusion.tolist(),
                confidence_threshold=threshold, accepted_count=int(accepted.sum()),
                coverage=float(accepted.float().mean()),
                accepted_accuracy=float((predicted[accepted] == targets[accepted]).float().mean()) if accepted.any() else None,
                loss=float(nn.functional.cross_entropy(logits, targets)))


def train(args):
    if args.output.exists():
        raise ValueError(f"Refusing to overwrite {args.output}; choose a new run directory")
    if not args.weights.is_file():
        raise ValueError(f"Pretrained weights missing: {args.weights}. No automatic download is performed.")
    # This pin is the torchvision IMAGENET1K_V2 artifact already used by the project.
    weights_hash = sha256(args.weights)
    if not weights_hash.startswith("7ebf99e0"):
        raise ValueError("Expected torchvision MobileNetV2 IMAGENET1K_V2 weights (7ebf99e0)")
    manifest = json.loads(args.manifest.read_text())
    validate_manifest(manifest)
    root = Path(manifest["root"])
    selected = {split: select_records(manifest, split, args.train_per_class if split == "train" else args.eval_per_class,
                                      args.seed) for split in SPLITS}
    for records in selected.values():
        for record in records:
            if sha256(root / record["path"]) != record["sha256"]:
                raise ValueError(f"Dataset changed after preparation: {record['path']}")
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    torch.use_deterministic_algorithms(True)
    args.output.mkdir(parents=True)
    configuration = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}
    configuration.update(dataset_manifest_sha256=sha256(args.manifest), pretrained_sha256=weights_hash,
                         torch=str(torch.__version__), torchvision=str(torchvision.__version__),
                         preprocessing=contract(args.size), classes=list(CLASSES),
                         frozen_backbone=True, hardware_verified=False,
                         selected_counts={s: dict(Counter(r["label"] for r in rows)) for s, rows in selected.items()})
    (args.output / "config.json").write_text(json.dumps(configuration, indent=2) + "\n")
    (args.output / "selected.json").write_text(json.dumps(selected, indent=2) + "\n")
    model = make_model(args.weights)
    model.requires_grad_(False)
    model.classifier.requires_grad_(True)
    train_x, train_y = features(model, Images(root, selected["train"], args.size), args.batch_size, "train/base")
    if args.augment:
        extra_x, extra_y = features(model, Images(root, selected["train"], args.size, True), args.batch_size, "train/augmented")
        train_x, train_y = torch.cat((train_x, extra_x)), torch.cat((train_y, extra_y))
    val_x, val_y = features(model, Images(root, selected["val"], args.size), args.batch_size, "validation")
    optimizer = torch.optim.Adam(model.classifier.parameters(), lr=args.learning_rate, weight_decay=1e-4)
    loader = DataLoader(TensorDataset(train_x, train_y), batch_size=args.batch_size, shuffle=True, num_workers=0,
                        generator=torch.Generator().manual_seed(args.seed))
    history, best_loss, best_state = [], float("inf"), None
    for epoch in range(1, args.epochs + 1):
        model.classifier.train()
        total_loss = 0.0
        for inputs, targets in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = nn.functional.cross_entropy(model.classifier(inputs), targets)
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach()) * len(targets)
        model.eval()
        with torch.inference_mode():
            validation = metrics(model.classifier(val_x), val_y, args.threshold)
        history.append(dict(epoch=epoch, train_loss=total_loss / len(train_y), validation=validation))
        if validation["loss"] < best_loss:
            best_loss, best_epoch = validation["loss"], epoch
            best_state = {key: value.detach().clone() for key, value in model.classifier.state_dict().items()}
        print(f"epoch={epoch:02d} train_loss={total_loss / len(train_y):.4f} "
              f"val_loss={validation['loss']:.4f} val_accuracy={validation['accuracy']:.3%}", flush=True)
    model.classifier.load_state_dict(best_state)
    model.eval()
    checkpoint = dict(schema=1, architecture="mobilenet_v2", classes=list(CLASSES), preprocessing=contract(args.size),
                      state_dict=model.state_dict(), best_epoch=best_epoch, configuration=configuration)
    torch.save(checkpoint, args.output / "best.pth")
    (args.output / "history.json").write_text(json.dumps(history, indent=2) + "\n")
    # Test split is evaluated only after choosing the checkpoint on validation.
    test_x, test_y = features(model, Images(root, selected["test"], args.size), args.batch_size, "held-out test")
    with torch.inference_mode():
        val_logits, test_logits = model.classifier(val_x), model.classifier(test_x)
    report = dict(best_epoch=best_epoch, validation=metrics(val_logits, val_y, args.threshold),
                  test=metrics(test_logits, test_y, args.threshold),
                  parameters=sum(p.numel() for p in model.parameters()),
                  checkpoint_bytes=(args.output / "best.pth").stat().st_size,
                  checkpoint_sha256=sha256(args.output / "best.pth"),
                  dataset_manifest_sha256=sha256(args.manifest),
                  limitations=["Dataset-only evaluation; not signer-independent or E8-camera validation.",
                               "Threshold is provisional, not calibrated for unsupported gestures.",
                               "NO_HAND is a trained class, not a general unknown-sign detector.",
                               "No quantized export, E8 memory fit or hardware inference established."])
    np.savez(args.output / "test_predictions.npz", logits=test_logits.numpy(), targets=test_y.numpy(),
             paths=np.array([r["path"] for r in selected["test"]]))
    (args.output / "metrics.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(".work/asl/dataset-v1/manifest.json"))
    parser.add_argument("--output", type=Path, default=Path(".work/asl/run-v1"))
    parser.add_argument("--weights", type=Path, default=Path(".work/inference/cache/torch/hub/checkpoints/mobilenet_v2-7ebf99e0.pth"))
    parser.add_argument("--size", type=int, choices=(96, 128, 160, 224), default=128)
    parser.add_argument("--train-per-class", type=int, default=600, help="0 uses all retained training images")
    parser.add_argument("--eval-per-class", type=int, default=200, help="0 uses all retained evaluation images")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--augment", action=argparse.BooleanOptionalAction, default=True,
                        help="Add one reproducible augmented view per training image to the feature cache")
    args = parser.parse_args()
    if min(args.train_per_class, args.eval_per_class) < 0 or min(args.epochs, args.batch_size, args.threads) < 1:
        parser.error("Counts must be nonnegative; epochs, batch size and threads must be positive")
    if not 0 <= args.threshold <= 1 or not 0 < args.learning_rate < float("inf"):
        parser.error("Invalid confidence threshold or learning rate")
    try:
        train(args)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
