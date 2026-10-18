"""Isolated static-alphabet inventory, labels and behavior; legacy experiments stay valid."""

import argparse
from pathlib import Path

from PIL import Image
import torch
from torch.utils.data import Dataset

from tools.asl_contract import DATASET_CLASSES, STATIC_CLASSES, STATIC_LETTERS, EXCLUDED_FOLDERS
from . import data
from .model import transform
from .train import metrics, validate_manifest as legacy_validate_manifest


def validate_manifest(manifest):
    # Reuse structural/leakage validation without changing legacy globals.
    if manifest.get("classes") != list(DATASET_CLASSES):
        raise ValueError("Expected all 24 static letters + NO_HAND; no J/Z/del/space")
    legacy_validate_manifest(manifest, DATASET_CLASSES)
    for r in manifest["records"]:
        folder = "nothing" if r["label"] == "NO_HAND" else r["label"]
        if Path(r["path"]).parts[0] != folder or folder in EXCLUDED_FOLDERS:
            raise ValueError("Dataset folder/label mismatch or excluded motion/command class")


def select_records(manifest, split, limit, seed):
    import random
    rows = []
    for label in DATASET_CLASSES:
        chosen = sorted((r for r in manifest["records"] if r["split"] == split and r["label"] == label),
                        key=lambda r: r["path"])
        random.Random(f"{seed}:{split}:{label}").shuffle(chosen)
        rows.extend(chosen[:limit] if limit else chosen)
    return rows


class StaticImages(Dataset):
    def __init__(self, root, records, augment=False):
        self.root, self.records = Path(root), records
        self.preprocess = transform(128, augment)

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        row = self.records[index]
        with Image.open(self.root / row["path"]) as image:
            value = self.preprocess(image)
        return value, STATIC_CLASSES.index(row["label"])


def decisions(logits, targets, threshold=0.8):
    if (logits.shape != (len(targets), len(STATIC_CLASSES)) or not len(targets) or
            not torch.isfinite(logits).all() or any(t not in STATIC_CLASSES for t in targets)):
        raise ValueError("Invalid static alphabet logits/targets")
    confidence, top = logits.softmax(1).max(1)
    rows = []
    for target, index, score in zip(targets, top.tolist(), confidence.tolist()):
        name = STATIC_CLASSES[index]
        display = name if name in STATIC_LETTERS and score >= threshold else "--"
        rows.append(dict(expected=target, top_class=name, confidence=score, display=display))
    letters = [r for r in rows if r["expected"] in STATIC_LETTERS]
    negatives = [r for r in rows if r["expected"] not in STATIC_LETTERS]
    per_class = {}
    for label in STATIC_CLASSES:
        group = [r for r in rows if r["expected"] == label]
        per_class[label] = dict(count=len(group), top1_correct_count=sum(r["top_class"] == label for r in group),
            correct_display_count=sum(r["display"] == (label if label in STATIC_LETTERS else "--") for r in group),
            false_letter_count=sum(r["display"] not in ("--", label) for r in group))
    return dict(count=len(rows), threshold=threshold, threshold_calibrated=False,
                letter_count=len(letters), negative_count=len(negatives),
                correct_letter_count=sum(r["display"] == r["expected"] for r in letters),
                wrong_letter_count=sum(r["display"] not in ("--", r["expected"]) for r in letters),
                suppressed_letter_count=sum(r["display"] == "--" for r in letters),
                negative_false_letter_count=sum(r["display"] != "--" for r in negatives), per_class=per_class,
                raw_class_metrics=metrics(logits, torch.tensor([STATIC_CLASSES.index(t) for t in targets]), threshold, STATIC_CLASSES)), rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("asl_alphabet_train/asl_alphabet_train"))
    parser.add_argument("--output", type=Path, default=Path(".work/asl/static-dataset-v1"))
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--block-size", type=int, default=100)
    args = parser.parse_args()
    data.prepare(args, DATASET_CLASSES, (*STATIC_LETTERS, "nothing"))


if __name__ == "__main__":
    main()
