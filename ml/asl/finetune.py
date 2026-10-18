"""Limited last-block adaptation; frozen BatchNorm, whole-session development validation.

Cache only the frozen prefix's output, not the trainable block's output. Never
touch a board, overwrite previous runs, or use a final test for model selection.
"""

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
import torchvision

from .adapt import AdaptImages, balanced_loss, decisions, load_camera_data, sampling_weights
from .camera_data import CAMERA_CLASSES, write_json
from .data import sha256
from .model import contract, load_checkpoint
from .train import select_records, validate_manifest


def configure_trainable(model):
    if len(model.features) != 19 or model.classifier[1].out_features != 7:
        raise ValueError("Expected seven-class MobileNetV2 with 19 feature blocks")
    model.eval().requires_grad_(False)
    model.features[18].requires_grad_(True)
    model.classifier.requires_grad_(True)
    # Keep BN affine weights AND running statistics fixed. No model.train().
    for module in model.modules():
        if isinstance(module, nn.modules.batchnorm._BatchNorm):
            module.eval().requires_grad_(False)
    return [p for p in model.features[18].parameters() if p.requires_grad]


def tail_logits(model, prefix_values):
    return model.classifier(model.features[18](prefix_values).mean((2, 3)))


def cache_prefix(model, dataset, batch_size):
    outputs, targets = [], []
    prefix = model.features[:18].eval()
    with torch.no_grad():
        for images, labels in DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0):
            outputs.append(prefix(images).detach())
            targets.append(labels)
    if not outputs:
        raise ValueError("Empty feature-cache dataset")
    return torch.cat(outputs), torch.cat(targets)


def evaluate_tail(model, values, records, batch_size, threshold):
    model.eval()
    with torch.no_grad():
        logits = torch.cat([tail_logits(model, x) for x in values.split(batch_size)])
    report, rows = decisions(logits, [r["label"] for r in records], CAMERA_CLASSES, threshold)
    return logits, report, rows


def verify_frozen(model, initial, trainable_names):
    state = model.state_dict()
    changed_frozen = [n for n, v in initial.items() if n not in trainable_names and not torch.equal(v, state[n])]
    if changed_frozen:
        raise ValueError(f"Frozen parameter/BatchNorm state changed: {changed_frozen}")
    return [n for n in trainable_names if not torch.equal(initial[n], state[n])]


def run(args):
    if args.output.exists():
        raise ValueError("Choose a new fine-tuning output directory")
    started = time.monotonic()
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.use_deterministic_algorithms(True)
    root, records, camera_manifest, review = load_camera_data(args.camera_manifest, args.label_review)
    model, checkpoint = load_checkpoint(args.checkpoint)
    completed = json.loads(args.checkpoint.with_name("metrics.json").read_text())
    if (checkpoint["schema"] != 2 or checkpoint["preprocessing"] != contract(128) or
            completed.get("checkpoint_sha256") != sha256(args.checkpoint) or
            completed.get("final_test_evaluated") is not False):
        raise ValueError("Start from a completed seven-class development run")
    previous = checkpoint["configuration"]
    if (previous.get("camera_manifest_sha256") != sha256(args.camera_manifest) or
            previous.get("label_review_sha256") != sha256(args.label_review) or
            previous.get("source_manifest_sha256") != sha256(args.source_manifest)):
        raise ValueError("Use the same reviewed split and original dataset as the starting run")
    source = json.loads(args.source_manifest.read_text())
    validate_manifest(source)
    source_root = Path(source["root"]).resolve(strict=True)
    rehearsal = select_records(source, "train", args.rehearsal_per_class, args.seed)
    if any(r["split"] != "train" or sha256(source_root / r["path"]) != r["sha256"] for r in rehearsal):
        raise ValueError("Rehearsal must be unchanged original training data")
    train = [r for r in records if r["split"] == "train"]
    val = [r for r in records if r["split"] == "val"]
    block_parameters = configure_trainable(model)
    names = {n for n, p in model.named_parameters() if p.requires_grad}
    initial = copy.deepcopy(model.state_dict())
    sources = ("finetune.py", "adapt.py", "camera_data.py", "model.py", "train.py", "data.py")
    input_paths = (args.checkpoint, args.checkpoint.with_name("metrics.json"), args.camera_manifest,
                   args.label_review, args.source_manifest)
    identities = {str(p): sha256(p) for p in input_paths}
    identities.update({str(Path(__file__).parent / n): sha256(Path(__file__).parent / n) for n in sources})
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    config.update(input_sha256=identities, trainable_parameters=sorted(names),
                  batchnorm_frozen=True, frozen_prefix_blocks=list(range(18)), trainable_feature_block=18,
                  preprocessing=contract(128), classes=list(CAMERA_CLASSES),
                  torch=str(torch.__version__), torchvision=str(torchvision.__version__), threads=1,
                  selection="minimum class-balanced camera development-validation cross-entropy, including epoch 0",
                  final_test_present=False, hardware_verified=False)
    args.output.mkdir(parents=True)
    write_json(args.output / "config.json", config)
    write_json(args.output / "selected.json", dict(camera_train=train, camera_val=val, rehearsal_train=rehearsal,
               label_exclusions=review.get("excluded", []), near_match_quarantine=camera_manifest["quarantined"]))
    base_x, base_y = cache_prefix(model, AdaptImages(root, train), args.batch_size)
    xs, ys = [base_x], [base_y]
    for _ in range(args.camera_augmented_views):
        x, y = cache_prefix(model, AdaptImages(root, train, augment=True), args.batch_size)
        xs.append(x)
        ys.append(y)
    camera_x, camera_y = torch.cat(xs), torch.cat(ys)
    replay_x, replay_y = cache_prefix(model, AdaptImages(source_root, rehearsal), args.batch_size)
    val_x, val_y = cache_prefix(model, AdaptImages(root, val), args.batch_size)
    all_x, all_y = torch.cat((camera_x, replay_x)), torch.cat((camera_y, replay_y))
    _, before_replay, _ = evaluate_tail(model, replay_x, rehearsal, args.batch_size, args.threshold)
    logits, before, before_rows = evaluate_tail(model, val_x, val, args.batch_size, args.threshold)
    best_loss, best_epoch, best_state = balanced_loss(logits, val_y), 0, copy.deepcopy(model.state_dict())
    history = [dict(epoch=0, training_loss=None, validation_balanced_loss=best_loss, validation=before)]
    write_json(args.output / "before-validation.json", dict(metrics=before, predictions=[
        dict(path=r["path"], sha256=r["sha256"], **p) for r, p in zip(val, before_rows)]))
    sampler = WeightedRandomSampler(sampling_weights(all_y, len(camera_y), args.camera_fraction),
                                    args.samples_per_epoch, replacement=True,
                                    generator=torch.Generator().manual_seed(args.seed))
    loader = DataLoader(TensorDataset(all_x, all_y), batch_size=args.batch_size, sampler=sampler, num_workers=0)
    optimizer = torch.optim.Adam([
        dict(params=block_parameters, lr=args.backbone_learning_rate),
        dict(params=model.classifier.parameters(), lr=args.head_learning_rate)], weight_decay=1e-4)
    for epoch in range(1, args.epochs + 1):
        # Enable head dropout only, never BatchNorm updates or prefix training.
        model.eval()
        model.classifier.train()
        loss_sum, count = 0., 0
        for values, labels in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = nn.functional.cross_entropy(tail_logits(model, values), labels)
            loss.backward()
            optimizer.step()
            loss_sum += float(loss.detach()) * len(labels)
            count += len(labels)
        logits, report, _ = evaluate_tail(model, val_x, val, args.batch_size, args.threshold)
        selection = balanced_loss(logits, val_y)
        if not torch.isfinite(logits).all() or not np.isfinite(selection):
            raise ValueError("Nonfinite training/validation output")
        if selection < best_loss:
            best_loss, best_epoch, best_state = selection, epoch, copy.deepcopy(model.state_dict())
        history.append(dict(epoch=epoch, training_loss=loss_sum/count, validation_balanced_loss=selection, validation=report))
        print(f"epoch={epoch:02d} val_loss={selection:.4f} correct_letters={report['correct_letter_count']}/"
              f"{report['letter_count']} negative_false_letters={report['negative_false_letter_count']}/"
              f"{report['negative_count']}", flush=True)
    verify_frozen(model, initial, names)
    model.load_state_dict(best_state)
    model.eval()
    changed = verify_frozen(model, initial, names)
    selected_logits, after, rows = evaluate_tail(model, val_x, val, args.batch_size, args.threshold)
    _, train_report, _ = evaluate_tail(model, base_x, train, args.batch_size, args.threshold)
    _, after_replay, _ = evaluate_tail(model, replay_x, rehearsal, args.batch_size, args.threshold)
    output = args.output / "best.pth"
    torch.save(dict(schema=2, architecture="mobilenet_v2", classes=list(CAMERA_CLASSES), preprocessing=contract(128),
                    state_dict=model.state_dict(), best_epoch=best_epoch, configuration=config), output)
    reloaded, _ = load_checkpoint(output)
    with torch.no_grad():
        direct = torch.cat([reloaded(images) for images, _ in DataLoader(AdaptImages(root, val),
                           batch_size=args.batch_size, num_workers=0)])
    delta = float((direct-selected_logits).abs().max())
    direct_report, direct_rows = decisions(direct, [r["label"] for r in val], CAMERA_CLASSES, args.threshold)
    if delta > 1e-4 or any(a["display"] != b["display"] or a["top_class"] != b["top_class"] for a, b in zip(rows, direct_rows)):
        raise ValueError("Full-model reload did not reproduce prefix-cache evaluation")
    if any(sha256(p) != digest for p, digest in identities.items()):
        raise ValueError("Experiment inputs changed during training")
    load_camera_data(args.camera_manifest, args.label_review)
    if any(sha256(source_root/r["path"]) != r["sha256"] for r in rehearsal):
        raise ValueError("Rehearsal images changed during training")
    write_json(args.output / "history.json", history)
    write_json(args.output / "validation-predictions.json", dict(metrics=direct_report, predictions=[
        dict(path=r["path"], sha256=r["sha256"], **p) for r, p in zip(val, direct_rows)]))
    result = dict(schema=1, best_epoch=best_epoch, best_validation_balanced_loss=best_loss,
                  before_validation=before, adapted_validation=direct_report, adapted_camera_training=train_report,
                  rehearsal_training_only=dict(before=before_replay, after=after_replay),
                  checkpoint_sha256=sha256(output), changed_trainable_parameters=changed,
                  frozen_prefix_and_batchnorm_verified=True, full_model_reload_max_logit_difference=delta,
                  elapsed_seconds=time.monotonic()-started, final_test_evaluated=False, hardware_verified=False,
                  deployment_approved=False, threshold_calibrated=False,
                  limitations=["Small single-presenter development session; not a final benchmark.",
                               "Epoch selection uses development validation, not final test.",
                               "Rehearsal retention is measured on original TRAIN samples only.",
                               "No quantization, device inference, programming, or robust rejection claim."])
    write_json(args.output / "metrics.json", result)
    print(json.dumps(dict(best_epoch=best_epoch, before=before, after=direct_report, reload_delta=delta), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--checkpoint", type=Path, default=Path(".work/asl/adapt-v1/best.pth"))
    parser.add_argument("--camera-manifest", type=Path, default=Path(".work/asl/camera-audit-v1/manifest.json"))
    parser.add_argument("--label-review", type=Path, default=Path(".work/asl/camera-audit-v1/label-review.json"))
    parser.add_argument("--source-manifest", type=Path, default=Path(".work/asl/dataset-v1/manifest.json"))
    parser.add_argument("--output", type=Path, default=Path(".work/asl/finetune-v1"))
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--rehearsal-per-class", type=int, default=100)
    parser.add_argument("--camera-augmented-views", type=int, default=3)
    parser.add_argument("--samples-per-epoch", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--backbone-learning-rate", type=float, default=1e-5)
    parser.add_argument("--head-learning-rate", type=float, default=1e-4)
    parser.add_argument("--camera-fraction", type=float, default=.75)
    parser.add_argument("--threshold", type=float, default=.8)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    if (min(args.epochs, args.rehearsal_per_class, args.samples_per_epoch, args.batch_size) < 1 or
            args.camera_augmented_views < 0 or not 0 < args.camera_fraction < 1 or not 0 <= args.threshold <= 1 or
            not 0 < args.backbone_learning_rate < float("inf") or not 0 < args.head_learning_rate < float("inf")):
        parser.error("Invalid experiment settings")
    try:
        run(args)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
