"""Predict local images or evaluate a labeled folder, including UNKNOWN examples."""

import argparse
import json
from pathlib import Path
import random

from PIL import Image
import torch

from .data import CLASSES, sha256
from .model import load_checkpoint, transform
from .train import metrics


def evaluate(args):
    if args.output and args.output.exists():
        raise ValueError(f"Refusing to overwrite {args.output}")
    torch.set_num_threads(args.threads)
    model, checkpoint = load_checkpoint(args.checkpoint)
    classes = tuple(checkpoint["classes"])
    preprocess = transform(checkpoint["preprocessing"]["size"])
    samples = []
    for path in args.image:
        samples.append((path, None))
    if args.folder:
        # Only these folders count as labeled known/unknown data; never infer
        # unknown labels from an arbitrary directory typo.
        for label in (*CLASSES, "UNKNOWN", *(("OTHER",) if "OTHER" in classes else ())):
            paths = sorted(p for p in (args.folder / label).glob("*")
                           if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png"))
            random.Random(f"{args.seed}:{label}").shuffle(paths)
            samples.extend((path, label) for path in (paths[:args.limit_per_class] if args.limit_per_class else paths))
    if args.unknown_root:
        # Static letters outside our five-letter vocabulary, for a rejection
        # stress test. Exclude J/Z and space/delete, which are not static classes.
        for letter in "DEFGHIKMNOPQRSTUVWX":
            paths = sorted((args.unknown_root / letter).glob("*.jpg"))
            if not paths:
                raise ValueError(f"Missing unknown-letter folder: {letter}")
            random.Random(f"{args.seed}:{letter}").shuffle(paths)
            samples.extend((path, "UNKNOWN") for path in paths[:args.unknown_per_class])
    if not samples:
        raise ValueError("Supply --image, --folder, or --unknown-root with readable images")
    predictions, known_logits, known_targets, all_logits, all_targets = [], [], [], [], []
    with torch.inference_mode():
        for start in range(0, len(samples), args.batch_size):
            batch = samples[start:start + args.batch_size]
            tensors = []
            for path, _ in batch:
                with Image.open(path) as image:
                    tensors.append(preprocess(image))
            logits = model(torch.stack(tensors))
            probabilities = logits.softmax(1)
            for (path, target), scores, raw in zip(batch, probabilities, logits):
                confidence, index = scores.max(0)
                top = classes[int(index)]
                predicted = top if float(confidence) >= args.threshold and top != "OTHER" else "UNKNOWN"
                predictions.append(dict(path=str(path), sha256=sha256(path), expected=target, prediction=predicted,
                                        top_class=top, confidence=float(confidence),
                                        probabilities=dict(zip(classes, scores.tolist()))))
                if target in CLASSES:
                    known_logits.append(raw)
                    known_targets.append(classes.index(target))
                mapped_target = "OTHER" if target == "UNKNOWN" and "OTHER" in classes else target
                if mapped_target in classes:
                    all_logits.append(raw)
                    all_targets.append(classes.index(mapped_target))
    unknown = [row for row in predictions if row["expected"] in ("UNKNOWN", "OTHER")]
    report = dict(checkpoint_sha256=sha256(args.checkpoint), preprocessing=checkpoint["preprocessing"],
                  threshold=args.threshold, threshold_calibrated=False, predictions=predictions,
                  unknown_count=len(unknown),
                  unknown_rejection_rate=sum(row["prediction"] == "UNKNOWN" for row in unknown) / len(unknown) if unknown else None,
                  unknown_false_letter_rate=sum(row["prediction"] not in ("UNKNOWN", "NO_HAND") for row in unknown) / len(unknown) if unknown else None,
                   known_metrics=metrics(torch.stack(known_logits), torch.tensor(known_targets), args.threshold, classes) if known_logits else None,
                   all_class_metrics=metrics(torch.stack(all_logits), torch.tensor(all_targets), args.threshold, classes) if all_logits else None)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: v for k, v in report.items() if k != "predictions"}, indent=2))
        print(f"Full predictions: {args.output}")
    else:
        print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=Path(".work/asl/run-v1/best.pth"))
    parser.add_argument("--image", type=Path, action="append", default=[])
    parser.add_argument("--folder", type=Path, help="Contains A/B/C/L/Y/NO_HAND/UNKNOWN subfolders")
    parser.add_argument("--unknown-root", type=Path, help="Kaggle training root; evaluate unsupported static letters")
    parser.add_argument("--unknown-per-class", type=int, default=30)
    parser.add_argument("--limit-per-class", type=int, default=0)
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1 or min(args.batch_size, args.threads, args.unknown_per_class) < 1 or args.limit_per_class < 0:
        parser.error("Invalid threshold or count")
    try:
        evaluate(args)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
