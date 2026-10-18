"""Frozen-backbone presenter adaptation with learned OTHER and session validation.

This is a host experiment, not a deployment command. It never uses camera
validation for gradients, opens a serial port, or evaluates a final test set.
"""

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import random
import time

import numpy as np
from PIL import Image, ImageOps
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset, TensorDataset, WeightedRandomSampler
import torchvision

from .camera_data import CAMERA_CLASSES, inventory_session, split_inventory, write_json
from .data import CLASSES, sha256
from .model import ADAPTED_CLASSES, contract, load_checkpoint, transform
from .train import features, metrics, select_records, validate_manifest


LETTERS = CLASSES[:-1]


def reviewed_records(records, review, manifest_hash):
    if (review.get("schema") != 1 or review.get("camera_manifest_sha256") != manifest_hash or
            review.get("status") not in ("reviewed", "reviewed_with_exclusions")):
        raise ValueError("A matching completed label review is required")
    by_path = {r["path"]: r for r in records}
    excluded = review.get("excluded", [])
    if len({r["path"] for r in excluded}) != len(excluded):
        raise ValueError("Repeated label-review exclusion")
    for row in excluded:
        if row["path"] not in by_path or by_path[row["path"]]["sha256"] != row["sha256"] or not row.get("reason"):
            raise ValueError("Label-review exclusion does not match an inventoried image")
    removed = {r["path"] for r in excluded}
    retained = [r for r in records if r["path"] not in removed]
    for split in ("train", "val"):
        missing = [c for c in CAMERA_CLASSES if not any(r["split"] == split and r["label"] == c for r in retained)]
        if missing:
            raise ValueError(f"Missing reviewed {split} classes: {missing}")
    return retained


def load_camera_data(path, review_path):
    manifest = json.loads(path.read_text())
    if (manifest.get("schema") != 1 or manifest.get("kind") != "camera_development_sessions" or
            manifest.get("classes") != list(CAMERA_CLASSES) or manifest.get("preprocessing_size") != 128 or
            manifest.get("final_test_present") is not False or manifest.get("dhash_radius") != 2):
        raise ValueError("Expected the reviewed camera development manifest, not a final test")
    train_sessions, val_sessions = manifest["train_sessions"], manifest["val_sessions"]
    if (not train_sessions or not val_sessions or set(train_sessions) & set(val_sessions) or
            len(set(train_sessions + val_sessions)) != len(train_sessions + val_sessions)):
        raise ValueError("Camera training/validation sessions must be disjoint")
    root = Path(manifest["root"]).resolve(strict=True)
    current = []
    for split, sessions in (("train", train_sessions), ("val", val_sessions)):
        for session in sessions:
            current.extend(inventory_session(root, session, split))
    retained, quarantined = split_inventory(current)
    if retained != manifest["records"] or quarantined != manifest["quarantined"]:
        raise ValueError("Camera files or audit assignments changed; prepare and review a new audit")
    review = json.loads(review_path.read_text())
    return root, reviewed_records(retained, review, sha256(path)), manifest, review


class AdaptImages(Dataset):
    def __init__(self, root, records, augment=False, horizontal_flip=False):
        self.root, self.records = root, records
        self.preprocess = transform(128, augment=augment)
        self.flip = augment and horizontal_flip

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        record = self.records[index]
        with Image.open(self.root / record["path"]) as image:
            if self.flip and random.random() < .5:
                image = ImageOps.mirror(image)
            tensor = self.preprocess(image)
        return tensor, CAMERA_CLASSES.index(record["label"])


def initialize_other_head(model):
    old_head = copy.deepcopy(model.classifier).eval()
    if old_head[1].out_features != len(CLASSES):
        raise ValueError("Adaptation must start from the unchanged six-class baseline")
    model.requires_grad_(False)
    linear = nn.Linear(model.last_channel, len(CAMERA_CLASSES))
    with torch.no_grad():
        linear.weight.zero_()
        linear.bias.fill_(-2.)
        linear.weight[:len(CLASSES)].copy_(old_head[1].weight)
        linear.bias[:len(CLASSES)].copy_(old_head[1].bias)
    model.classifier = nn.Sequential(nn.Dropout(.2), linear)
    model.eval()
    return old_head


def sampling_weights(targets, camera_count, camera_fraction):
    if not 0 < camera_count < len(targets) or not 0 < camera_fraction < 1:
        raise ValueError("Need both camera training and rehearsal samples")
    weights = torch.zeros(len(targets), dtype=torch.float64)
    for begin, end, mass in ((0, camera_count, camera_fraction),
                             (camera_count, len(targets), 1 - camera_fraction)):
        counts = Counter(targets[begin:end].tolist())
        for index in range(begin, end):
            weights[index] = mass / (len(counts) * counts[int(targets[index])])
    return weights


def balanced_loss(logits, targets):
    loss = nn.functional.cross_entropy(logits, targets, reduction="none")
    return float(torch.stack([loss[targets == c].mean() for c in targets.unique()]).mean())


def decisions(logits, targets, classes, threshold):
    if (len(logits) != len(targets) or logits.shape[1] != len(classes) or not len(targets) or
            not torch.isfinite(logits).all() or any(t not in CAMERA_CLASSES for t in targets)):
        raise ValueError("Invalid logits/class/target contract")
    scores = logits.softmax(1)
    confidences, indices = scores.max(1)
    rows = []
    for target, confidence, index, probabilities in zip(targets, confidences.tolist(), indices.tolist(), scores.tolist()):
        top = classes[index]
        prediction = top if confidence >= threshold and top != "OTHER" else "UNKNOWN"
        display = prediction if prediction in LETTERS else "--"
        rows.append(dict(expected=target, top_class=top, confidence=confidence,
                         prediction=prediction, display=display, probabilities=dict(zip(classes, probabilities))))
    letter_rows = [r for r in rows if r["expected"] in LETTERS]
    negative_rows = [r for r in rows if r["expected"] not in LETTERS]
    displayed = [r for r in rows if r["display"] != "--"]
    ratio = lambda a, b: a / b if b else None
    report = dict(count=len(rows), threshold=threshold, threshold_calibrated=False,
                  letter_count=len(letter_rows), negative_count=len(negative_rows),
                  correct_letter_count=sum(r["display"] == r["expected"] for r in letter_rows),
                  letter_coverage=ratio(sum(r["display"] != "--" for r in letter_rows), len(letter_rows)),
                  correct_letter_rate=ratio(sum(r["display"] == r["expected"] for r in letter_rows), len(letter_rows)),
                  wrong_letter_count=sum(r["display"] not in ("--", r["expected"]) for r in letter_rows),
                  suppressed_letter_count=sum(r["display"] == "--" for r in letter_rows),
                  negative_false_letter_count=sum(r["display"] != "--" for r in negative_rows),
                  negative_false_letter_rate=ratio(sum(r["display"] != "--" for r in negative_rows), len(negative_rows)),
                  displayed_letter_accuracy=ratio(sum(r["display"] == r["expected"] for r in displayed), len(displayed)),
                  behavior_accuracy=sum(r["display"] == (r["expected"] if r["expected"] in LETTERS else "--") for r in rows) / len(rows),
                  per_class={})
    for label in CAMERA_CLASSES:
        selected = [r for r in rows if r["expected"] == label]
        report["per_class"][label] = dict(count=len(selected),
            top1_correct_count=sum(r["top_class"] == label for r in selected),
            correct_display_count=sum(r["display"] == (label if label in LETTERS else "--") for r in selected),
            false_letter_count=sum(r["display"] not in ("--", label) for r in selected),
            suppressed_count=sum(r["display"] == "--" for r in selected))
    # For the six-class baseline, OTHER cannot have a raw class target. Keep its
    # negative behavior in the common report above, not in closed-set accuracy.
    known = [i for i, t in enumerate(targets) if t in classes]
    report["raw_class_metrics"] = metrics(logits[known], torch.tensor([classes.index(targets[i]) for i in known]),
                                           threshold, classes) if known else None
    return report, rows


def train(args):
    if args.output.exists():
        raise ValueError("Choose a new adaptation output directory; existing runs are retained")
    start = time.monotonic()
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)
    torch.use_deterministic_algorithms(True)
    root, records, camera_manifest, review = load_camera_data(args.camera_manifest, args.label_review)
    baseline, checkpoint = load_checkpoint(args.checkpoint)
    if checkpoint["schema"] != 1 or checkpoint["preprocessing"] != contract(128):
        raise ValueError("Use the original six-class 128x128 checkpoint")
    baseline_hash = sha256(args.checkpoint)
    source_hash = sha256(args.source_manifest)
    if source_hash != checkpoint["configuration"]["dataset_manifest_sha256"]:
        raise ValueError("Rehearsal manifest differs from the original baseline's dataset")
    source_manifest = json.loads(args.source_manifest.read_text())
    validate_manifest(source_manifest)
    source_root = Path(source_manifest["root"]).resolve(strict=True)
    rehearsal = select_records(source_manifest, "train", args.rehearsal_per_class, args.seed)
    if any(r["split"] != "train" or sha256(source_root / r["path"]) != r["sha256"] for r in rehearsal):
        raise ValueError("Rehearsal must be unchanged original training data only")
    training = [r for r in records if r["split"] == "train"]
    validation = [r for r in records if r["split"] == "val"]
    baseline_head = initialize_other_head(baseline)
    original_features = {k: v.detach().clone() for k, v in baseline.features.state_dict().items()}
    args.output.mkdir(parents=True)
    configuration = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    source_files = ("adapt.py", "camera_data.py", "model.py", "train.py", "data.py")
    configuration.update(checkpoint_sha256=baseline_hash, camera_manifest_sha256=sha256(args.camera_manifest),
                         label_review_sha256=sha256(args.label_review), source_manifest_sha256=source_hash,
                         classes=list(CAMERA_CLASSES), preprocessing=contract(128),
                         torch=str(torch.__version__), torchvision=str(torchvision.__version__),
                         source_sha256={n: sha256(Path(__file__).parent / n) for n in source_files},
                         frozen_backbone=True, camera_train_count=len(training), camera_validation_count=len(validation),
                         rehearsal_count=len(rehearsal), final_test_present=False, hardware_verified=False,
                         selection="minimum class-balanced cross-entropy on camera development validation only",
                         augmentation="one base + fixed mild affine/color views of camera TRAINING only; optional mirror")
    write_json(args.output / "config.json", configuration)
    write_json(args.output / "selected.json", dict(camera_train=training, camera_val=validation,
               rehearsal_train=rehearsal, label_exclusions=review.get("excluded", []),
               near_match_quarantine=camera_manifest["quarantined"]))
    base_x, base_y = features(baseline, AdaptImages(root, training), args.batch_size, "camera/train/base")
    camera_x, camera_y = [base_x], [base_y]
    for view in range(args.camera_augmented_views):
        x, y = features(baseline, AdaptImages(root, training, augment=True, horizontal_flip=args.horizontal_flip),
                        args.batch_size, f"camera/train/augmented-{view + 1}")
        camera_x.append(x)
        camera_y.append(y)
    train_x, train_y = torch.cat(camera_x), torch.cat(camera_y)
    camera_count = len(train_y)
    replay_x, replay_y = features(baseline, AdaptImages(source_root, rehearsal), args.batch_size, "rehearsal/TRAIN-only")
    train_x, train_y = torch.cat((train_x, replay_x)), torch.cat((train_y, replay_y))
    val_x, val_y = features(baseline, AdaptImages(root, validation), args.batch_size, "camera/development-validation")
    with torch.inference_mode():
        baseline_val, baseline_rows = decisions(baseline_head(val_x), [r["label"] for r in validation], CLASSES, args.threshold)
    write_json(args.output / "baseline-validation.json", dict(metrics=baseline_val, predictions=[
        dict(path=r["path"], sha256=r["sha256"], **p) for r, p in zip(validation, baseline_rows)]))
    sampler = WeightedRandomSampler(sampling_weights(train_y, camera_count, args.camera_fraction),
                                    num_samples=args.samples_per_epoch, replacement=True,
                                    generator=torch.Generator().manual_seed(args.seed))
    loader = DataLoader(TensorDataset(train_x, train_y), batch_size=args.batch_size, sampler=sampler, num_workers=0)
    optimizer = torch.optim.Adam(baseline.classifier.parameters(), lr=args.learning_rate, weight_decay=1e-4)
    best_state, best_loss, best_epoch, history = None, float("inf"), None, []
    for epoch in range(1, args.epochs + 1):
        baseline.classifier.train()
        total_loss, seen = 0., 0
        for inputs, targets in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = nn.functional.cross_entropy(baseline.classifier(inputs), targets)
            loss.backward()
            optimizer.step()
            total_loss += float(loss.detach()) * len(targets)
            seen += len(targets)
        baseline.eval()
        with torch.inference_mode():
            logits = baseline.classifier(val_x)
            selection_loss = balanced_loss(logits, val_y)
            validation_metrics, _ = decisions(logits, [r["label"] for r in validation], CAMERA_CLASSES, args.threshold)
        if selection_loss < best_loss:
            best_loss, best_epoch = selection_loss, epoch
            best_state = copy.deepcopy(baseline.classifier.state_dict())
        history.append(dict(epoch=epoch, training_loss=total_loss / seen, validation_balanced_loss=selection_loss,
                            validation=validation_metrics))
        print(f"epoch={epoch:02d} val_balanced_loss={selection_loss:.4f} "
              f"correct_letters={validation_metrics['correct_letter_count']}/{validation_metrics['letter_count']} "
              f"false_letters_on_negatives={validation_metrics['negative_false_letter_count']}/{validation_metrics['negative_count']}", flush=True)
    baseline.classifier.load_state_dict(best_state)
    baseline.eval()
    if not all(torch.equal(v, baseline.features.state_dict()[k]) for k, v in original_features.items()):
        raise ValueError("Frozen backbone or BatchNorm state changed")
    with torch.inference_mode():
        val_logits = baseline.classifier(val_x)
        adapted_val, adapted_rows = decisions(val_logits, [r["label"] for r in validation], CAMERA_CLASSES, args.threshold)
        adapted_train, _ = decisions(baseline.classifier(base_x), [r["label"] for r in training], CAMERA_CLASSES, args.threshold)
        rehearsal_before, _ = decisions(baseline_head(replay_x), [r["label"] for r in rehearsal], CLASSES, args.threshold)
        rehearsal_after, _ = decisions(baseline.classifier(replay_x), [r["label"] for r in rehearsal], CAMERA_CLASSES, args.threshold)
    checkpoint_path = args.output / "best.pth"
    torch.save(dict(schema=2, architecture="mobilenet_v2", classes=list(ADAPTED_CLASSES), preprocessing=contract(128),
                    state_dict=baseline.state_dict(), best_epoch=best_epoch, configuration=configuration), checkpoint_path)
    reloaded, _ = load_checkpoint(checkpoint_path)
    direct_logits = []
    with torch.inference_mode():
        for images, _ in DataLoader(AdaptImages(root, validation), batch_size=args.batch_size, num_workers=0):
            direct_logits.append(reloaded(images))
    direct_logits = torch.cat(direct_logits)
    delta = float((direct_logits - val_logits).abs().max())
    direct_report, direct_rows = decisions(direct_logits, [r["label"] for r in validation], CAMERA_CLASSES, args.threshold)
    if delta > 1e-4 or any(a["display"] != b["display"] or a["top_class"] != b["top_class"] for a, b in zip(adapted_rows, direct_rows)):
        raise ValueError("Reloaded full-model predictions do not reproduce cached-feature evaluation")
    if (sha256(args.checkpoint) != baseline_hash or sha256(args.source_manifest) != source_hash or
            sha256(args.camera_manifest) != configuration["camera_manifest_sha256"] or
            sha256(args.label_review) != configuration["label_review_sha256"] or
            any(sha256(Path(__file__).parent / n) != digest for n, digest in configuration["source_sha256"].items())):
        raise ValueError("Experiment inputs changed during fitting")
    # Recheck source files/sidecars, not only the manifest's bytes.
    load_camera_data(args.camera_manifest, args.label_review)
    if any(sha256(source_root / r["path"]) != r["sha256"] for r in rehearsal):
        raise ValueError("Rehearsal files changed during fitting")
    write_json(args.output / "history.json", history)
    write_json(args.output / "validation-predictions.json", dict(metrics=direct_report, predictions=[
        dict(path=r["path"], sha256=r["sha256"], **p) for r, p in zip(validation, direct_rows)]))
    report = dict(schema=1, status="host_candidate_requires_fresh_final_evaluation", best_epoch=best_epoch,
                  best_validation_balanced_loss=best_loss, baseline_validation=baseline_val, adapted_validation=direct_report,
                  adapted_camera_training=adapted_train,
                  rehearsal_training_only=dict(before=rehearsal_before, after=rehearsal_after),
                  checkpoint_sha256=sha256(checkpoint_path), frozen_backbone_verified=True,
                  full_model_reload_max_logit_difference=delta, elapsed_seconds=time.monotonic() - start,
                  hardware_verified=False, deployment_approved=False, final_test_evaluated=False,
                  limitations=["One presenter and a small development-validation session, not a final accuracy benchmark.",
                               "Checkpoint selection uses this validation session; a fresh final session is still required.",
                               "Only the head is adapted; source training rehearsal is not held-out retention evidence.",
                               "OTHER is learned from a small set, not guaranteed rejection of all unsupported poses.",
                               "No quantized export, on-device inference, flash, or serial access."])
    write_json(args.output / "metrics.json", report)
    print(json.dumps(dict(best_epoch=best_epoch, baseline_validation=baseline_val,
                         adapted_validation=direct_report, checkpoint=str(checkpoint_path),
                         reload_max_logit_difference=delta, deployment_approved=False), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--camera-manifest", type=Path, default=Path(".work/asl/camera-audit-v1/manifest.json"))
    parser.add_argument("--label-review", type=Path, default=Path(".work/asl/camera-audit-v1/label-review.json"))
    parser.add_argument("--source-manifest", type=Path, default=Path(".work/asl/dataset-v1/manifest.json"))
    parser.add_argument("--checkpoint", type=Path, default=Path(".work/asl/run-v1/best.pth"))
    parser.add_argument("--output", type=Path, default=Path(".work/asl/adapt-v1"))
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--rehearsal-per-class", type=int, default=100)
    parser.add_argument("--camera-augmented-views", type=int, default=3)
    parser.add_argument("--horizontal-flip", action="store_true", help="Explicit experiment; off by default")
    parser.add_argument("--camera-fraction", type=float, default=.75)
    parser.add_argument("--samples-per-epoch", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=.001)
    parser.add_argument("--threshold", type=float, default=.8)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if (min(args.epochs, args.rehearsal_per_class, args.samples_per_epoch, args.batch_size, args.threads) < 1 or
            args.camera_augmented_views < 0 or not 0 < args.camera_fraction < 1 or
            not 0 < args.learning_rate < float("inf") or not 0 <= args.threshold <= 1):
        parser.error("Invalid counts, domain fraction, learning rate or threshold")
    try:
        train(args)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
