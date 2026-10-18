"""Measure threshold tradeoffs using validation and previously inspected negatives.

The old 570-image unknown report is development data, never a fresh final test.
No checkpoint is retrained and no existing test labels select the threshold.
"""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import re

import numpy as np
from PIL import Image, ImageOps
import torch

from .data import CLASSES, HashIndex, dhash, sha256
from .model import load_checkpoint, transform


def operating_point(known_probabilities, targets, unknown_probabilities, threshold):
    known_confidence = known_probabilities.max(1)
    known_predicted = known_probabilities.argmax(1)
    accepted = known_confidence >= threshold
    unknown_accepted = unknown_probabilities.max(1) >= threshold
    return dict(threshold=float(threshold), known_coverage=float(accepted.mean()),
                known_accepted_accuracy=float((known_predicted[accepted] == targets[accepted]).mean()) if accepted.any() else None,
                known_correct_coverage=float(((known_predicted == targets) & accepted).mean()),
                unknown_acceptance_rate=float(unknown_accepted.mean()),
                unknown_false_letter_rate=float((unknown_accepted & (unknown_probabilities.argmax(1) != CLASSES.index("NO_HAND"))).mean()))


def choose_threshold(known, targets, unknown, min_coverage=.9, max_unknown_acceptance=.05):
    frontier = [operating_point(known, targets, unknown, threshold)
                for threshold in sorted(set(np.linspace(.5, 1.0, 201).tolist() + [.8]))]
    usable = [row for row in frontier if row["known_coverage"] >= min_coverage and
              row["known_accepted_accuracy"] is not None and row["known_accepted_accuracy"] >= .95]
    feasible = [row for row in usable if row["unknown_acceptance_rate"] <= max_unknown_acceptance]
    selected = min(feasible, key=lambda row: row["threshold"]) if feasible else None
    best_tradeoff = min(usable, key=lambda row: (row["unknown_acceptance_rate"], -row["known_coverage"])) if usable else None
    return selected, best_tradeoff, frontier


def probabilities(model, preprocessing, paths):
    outputs = []
    with torch.inference_mode():
        for offset in range(0, len(paths), 32):
            images = []
            for path in paths[offset:offset + 32]:
                with Image.open(path) as image:
                    images.append(preprocessing(image))
            outputs.append(model(torch.stack(images)).softmax(1).numpy())
    return np.concatenate(outputs)


def fresh_unknowns(root, development, count, seed):
    used_groups = defaultdict(set)
    index = HashIndex()
    used_pixels = set()
    for record in development:
        path = Path(record["path"])
        if sha256(path) != record["sha256"]:
            raise ValueError(f"Development image changed: {path}")
        match = re.fullmatch(r"([A-Z])(\d+)\.jpg", path.name)
        if not match:
            raise ValueError(f"Unexpected unknown filename: {path}")
        used_groups[match[1]].add((int(match[2]) - 1) // 100)
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            index.add(dhash(image))
            used_pixels.add(hashlib.sha256(str(image.size).encode() + image.tobytes()).digest())
    retained = []
    for letter in sorted(used_groups):
        paths = sorted((root / letter).glob("*.jpg"))
        random.Random(f"{seed}:{letter}").shuffle(paths)
        selected = []
        for path in paths:
            match = re.fullmatch(re.escape(letter) + r"(\d+)\.jpg", path.name)
            if not match or (int(match[1]) - 1) // 100 in used_groups[letter]:
                continue
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                fingerprint = dhash(image)
                if hashlib.sha256(str(image.size).encode() + image.tobytes()).digest() in used_pixels or index.contains_near(fingerprint):
                    continue
            selected.append(dict(path=str(path), sha256=sha256(path), label="UNKNOWN", source_letter=letter,
                                 filename_group=(int(match[1]) - 1) // 100, dhash=fingerprint))
            if len(selected) == count:
                break
        if len(selected) != count:
            raise ValueError(f"Only {len(selected)} fresh examples for {letter}; do not weaken the split automatically")
        retained.extend(selected)
    return retained


def run(args):
    if args.output.exists():
        raise ValueError("Choose a new output directory; existing reports are retained")
    torch.set_num_threads(1)
    model, checkpoint = load_checkpoint(args.checkpoint)
    if checkpoint.get("schema") != 1 or checkpoint.get("classes") != list(CLASSES):
        raise ValueError("This threshold-only study supports the six-class baseline; use adaptation decision metrics for learned OTHER")
    preprocessing = transform(checkpoint["preprocessing"]["size"])
    previous = json.loads(args.development.read_text())
    if previous["checkpoint_sha256"] != sha256(args.checkpoint):
        raise ValueError("Development report belongs to a different checkpoint")
    negatives = [r for r in previous["predictions"] if r["expected"] == "UNKNOWN"]
    if not negatives:
        raise ValueError("No development negatives")
    selected = json.loads((args.checkpoint.parent / "selected.json").read_text())
    config = checkpoint["configuration"]
    manifest_path = Path(config["manifest"])
    if sha256(manifest_path) != config["dataset_manifest_sha256"]:
        raise ValueError("Dataset manifest changed")
    manifest = json.loads(manifest_path.read_text())
    root = Path(manifest["root"])
    expected_val = {r["path"]: r for r in manifest["records"] if r["split"] == "val"}
    validation = selected["val"]
    if len({r["path"] for r in validation}) != len(validation):
        raise ValueError("Repeated validation records")
    for record in validation:
        if expected_val.get(record["path"]) != record or sha256(root / record["path"]) != record["sha256"]:
            raise ValueError("Validation record changed or is not from validation")
    fresh = fresh_unknowns(args.unknown_root, negatives, args.fresh_per_class, args.seed)
    # Recompute development probabilities from the checkpoint; don't trust a
    # manually editable earlier report's scores during calibration.
    known = probabilities(model, preprocessing, [root / r["path"] for r in validation])
    targets = np.array([CLASSES.index(r["label"]) for r in validation])
    unknown = probabilities(model, preprocessing, [Path(r["path"]) for r in negatives])
    selected_point, tradeoff, frontier = choose_threshold(known, targets, unknown, args.min_coverage, args.max_unknown_acceptance)
    # Select using development only, then evaluate fresh unknowns once.
    threshold = selected_point["threshold"] if selected_point else tradeoff["threshold"] if tradeoff else .8
    fresh_probabilities = probabilities(model, preprocessing, [Path(r["path"]) for r in fresh])
    report = dict(schema=1, checkpoint_sha256=sha256(args.checkpoint),
                  development_report_sha256=sha256(args.development),
                  status="development_gate_passed" if selected_point else "blocked_no_feasible_threshold",
                  deployment_approved=False, camera_domain_validated=False,
                  requirements=dict(min_known_coverage=args.min_coverage, max_unknown_acceptance=args.max_unknown_acceptance,
                                    min_known_accepted_accuracy=.95),
                  validation_count=len(validation), development_unknown_count=len(negatives), fresh_unknown_count=len(fresh),
                  selected=selected_point, best_development_tradeoff=tradeoff,
                  baseline=operating_point(known, targets, unknown, .8),
                  fresh_unknown_at_evaluated_threshold=dict(threshold=threshold,
                      rejection_rate=float((fresh_probabilities.max(1) < threshold).mean()),
                      false_letter_rate=float(((fresh_probabilities.max(1) >= threshold) &
                                               (fresh_probabilities.argmax(1) != CLASSES.index("NO_HAND"))).mean())),
                  limitations=["Previously inspected unknowns are development data.",
                               "Fresh unknown filename groups and near-matches are excluded from development overlap, not signer-independent.",
                               "Threshold-only rejection is not a learned OTHER class or camera validation."], frontier=frontier)
    args.output.mkdir(parents=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.output / "fresh_unknowns.json").write_text(json.dumps(fresh, indent=2) + "\n")
    np.savez(args.output / "scores.npz", known=known, targets=targets, development_unknown=unknown,
             fresh_unknown=fresh_probabilities)
    print(json.dumps({key: value for key, value in report.items() if key != "frontier"}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=Path(".work/asl/run-v1/best.pth"))
    parser.add_argument("--development", type=Path, default=Path(".work/asl/unknown-report.json"))
    parser.add_argument("--unknown-root", type=Path, default=Path("asl_alphabet_train/asl_alphabet_train"))
    parser.add_argument("--output", type=Path, default=Path(".work/asl/rejection-v1"))
    parser.add_argument("--fresh-per-class", type=int, default=30)
    parser.add_argument("--seed", type=int, default=2027)
    parser.add_argument("--min-coverage", type=float, default=.9)
    parser.add_argument("--max-unknown-acceptance", type=float, default=.05)
    args = parser.parse_args()
    if args.fresh_per_class < 1 or not 0 < args.min_coverage <= 1 or not 0 <= args.max_unknown_acceptance < 1:
        parser.error("Invalid count or operating requirements")
    try:
        run(args)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
