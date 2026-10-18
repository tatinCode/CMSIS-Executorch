# Host ASL training baseline

## Current static-alphabet experiment

The original six/seven-class workflows below remain preserved. Separate
`static_data`, `static_train`, and `static_export` modules support **24 static
letters (no J/Z), NO_HAND and OTHER**. Kaggle `del` and `space` are excluded,
not used as generic negatives. See the [current workflow](../../firmware/asl_live/README.md)
and [audit/limitations](../../docs/asl-static-alphabet.md). The 19 new letters
have no reviewed E8-camera examples; dataset accuracy is not camera accuracy.

## Preserved original baseline

Six classes in a fixed order: **A, B, C, L, Y, NO_HAND**. Kaggle's `nothing`
folder maps to `NO_HAND`. This pipeline trains on images, not hand landmarks.

The first candidate uses **ImageNet-pretrained MobileNetV2 with a new six-class
head**, at 128×128 resolution. Only the head is trained. This is a fast transfer-
learning baseline using an architecture already exercised by the project's
deployment tools; it is not a newly designed tiny CNN. Its exported memory,
quantization accuracy and device latency still need separate checks.

The [first-run results](../../docs/asl-local-baseline.md) show strong selected-class
dataset accuracy but poor unsupported-sign rejection. Initial E8-camera captures
also show a domain gap. A separate presenter-adaptation experiment is described
below; it does not replace the six-class baseline.

To collect actual board images, follow [camera frame capture](../../docs/camera-frame-capture.md).

## Environment

Run commands from the repository root. The existing inference environment and
firmware artifacts are separate from `.work/asl/`. No MediaPipe, OpenCV, training
framework, GPU runtime or new ExecuTorch build is required.

```bash
uv venv .work/asl/venv --python .work/inference/venv/bin/python
uv pip install --python .work/asl/venv/bin/python \
  -r ml/asl/requirements.txt --index-url https://download.pytorch.org/whl/cpu
uv pip check --python .work/asl/venv/bin/python
```

The setup uses the same PyTorch/torchvision/NumPy/Pillow pins as the established
export environment. Transitive versions can be recorded with:

```bash
uv pip freeze --python .work/asl/venv/bin/python > .work/asl/environment.freeze.txt
```

## 1. Prepare and inspect the dataset

Source: <https://www.kaggle.com/datasets/grassknoted/asl-alphabet>.
Keep the ZIP, images and generated artifacts out of Git.

```bash
.work/asl/venv/bin/python -m ml.asl.data
```

Default input: `asl_alphabet_train/asl_alphabet_train/`.
Default output: `.work/asl/dataset-v1/`:

- `manifest.json`: image hashes, dimensions, label mapping, split membership,
  removed duplicates and quarantined near-matches.
- `samples.jpg`: a contact sheet for visual inspection.

Every selected image is decoded. Identical decoded images are deduplicated;
conflicting labels abort preparation. Consecutive filename-number blocks of 100
images stay together, with approximately 70/15/15 train/validation/test allocation
before similarity screening (rounding produces 22/4/4 blocks for 3,000 images).
Images in validation matching training, and images in test matching training or
validation, are quarantined using exact decoded-pixel hashes and 64-bit dHash
distance ≤2. Quarantined images are not recycled into training.

**These are filename groups, not verified signer/session groups.** Perceptual
hashing is a heuristic that can miss similar images and reject distinct ones.
Backgrounds, people and capture conditions can still be shared across splits.
Results are dataset checks, not unseen-signer or real-camera performance claims.
The download's 28-image test folder is not used as the primary test set.

## 2. Train

```bash
.work/asl/venv/bin/python -m ml.asl.train
```

Defaults:

- Local pretrained weights from the established ImageNet pipeline; no download.
- 600 training images per class, sampled only after splitting.
- Up to 200 images per class for validation/test (some have fewer after screening).
- One unchanged and one mildly augmented training view; no horizontal flips.
- Frozen backbone and BatchNorm statistics; cached features in host RAM.
- Adam, learning rate 0.001, batch size 32, 30 epochs, one CPU thread, no loader workers.
- Best checkpoint selected by validation cross-entropy; held-out test evaluated
  only after checkpoint selection. No test-based hyperparameter selection.

Use `--train-per-class 0 --eval-per-class 0` for all retained images. Use a new
`--output` directory for every experiment; preparation, training and evaluation
refuse to overwrite existing results. Epochs do not re-extract features, so the
single augmented view is fixed throughout a run. Fine-tuning the backbone can be
a later experiment if this baseline is insufficient.

Outputs under `.work/asl/run-v1/`:

| File | Purpose |
|---|---|
| `best.pth` | Full model state, class order, preprocessing contract and configuration |
| `config.json` | Versions, seed, image counts, dataset/pretrained hashes and parameters |
| `selected.json` | Exact images selected for each split |
| `history.json` | Per-epoch training loss and validation metrics |
| `metrics.json` | Test/validation metrics, confusion matrices, checkpoint identity and limitations |
| `test_predictions.npz` | Per-image test logits, labels and source paths |

Confusion matrices have **actual classes in rows, predicted classes in columns**.
Per-class recall and balanced accuracy matter because similarity screening can
make evaluation sets imbalanced. A missing `metrics.json` means the run did not
complete; a checkpoint alone is not a completed evaluation.

## 3. Predict and evaluate additional images

```bash
.work/asl/venv/bin/python -m ml.asl.evaluate \
  --image asl_alphabet_test/asl_alphabet_test/A_test.jpg
```

For real camera evaluation, arrange independently captured images in
`.work/asl/camera-eval/{A,B,C,L,Y,NO_HAND,UNKNOWN}/` and run:

```bash
.work/asl/venv/bin/python -m ml.asl.evaluate \
  --folder .work/asl/camera-eval --output .work/asl/camera-eval-report.json
```

Do not reuse training captures here. Separate capture sessions, people and
backgrounds where possible. Unsupported handshapes go in `UNKNOWN`; empty scenes
go in `NO_HAND`. No camera capture or board access is performed by this command.

An additional stress test uses held-out-vocabulary static letters from Kaggle:

```bash
.work/asl/venv/bin/python -m ml.asl.evaluate \
  --unknown-root asl_alphabet_train/asl_alphabet_train \
  --output .work/asl/unknown-report.json
```

It samples 30 images from each unsupported static letter, excluding J/Z and the
space/delete folders. This is an out-of-vocabulary test on the same dataset
domain, not representative unknown-sign validation.

**Rejection is provisional:** maximum softmax confidence below 0.8 returns
`UNKNOWN`. The threshold is not calibrated, and neural networks can be highly
confident about unsupported gestures. `NO_HAND` does not solve this. Report both
known-class accuracy/coverage and unknown false-letter rate. Do not claim robust
rejection without independent negative examples and validation.

The follow-up `python -m ml.asl.rejection` command (using this environment)
measures threshold tradeoffs using known validation images and the previously
inspected unknown report as development data. It selects no threshold from the
known test set and checks additional unknown images from separate numbered
blocks. Its first run found no tested threshold meeting the coverage/rejection
requirements; it leaves the classifier unchanged. See the results link above.

## Preprocessing contract

EXIF orientation correction → RGB → centered square crop (integer floor for the
left/top offsets) → PIL bilinear resize to 128×128 → float32 /255 → ImageNet
mean `[0.485, 0.456, 0.406]`, standard deviation `[0.229, 0.224, 0.225]` → NCHW.
Training adds mild affine/color augmentation only to its extra training view.
Evaluation and prediction share the exact deterministic preprocessing function.

This is **different from the stored-image ImageNet self-test's resize/crop**.
Future firmware must reproduce this contract and compare the exact same camera
bytes against the host. The RGB565 preview conversion is not inference input.

The v4 capture target now implements a full-view 480→128 resize matching Pillow
12.1.0's antialiased bilinear RGB bytes in host tests. Its 128×128 PNG undergoes
an identity resize here. Camera capture is working on hardware, but ASL inference
still runs on the host. Normalization, int8 export and live glyph display remain
separate deployment gates.

## 4. Presenter-specific adaptation with learned OTHER

Use separate whole camera sessions for training and development validation.
The corrected `presenter-dev02` batch is development validation, **not a final
test**. `UNKNOWN` capture folders map to a new learned `OTHER` output. NO_HAND
remains a separate class. Neither is displayed as a letter.

```bash
.work/asl/venv/bin/python -m ml.asl.camera_data \
  --train-session fastcheck01 --train-session plumbing01 \
  --val-session presenter-dev02 --output .work/asl/camera-audit-v1
```

The audit verifies raw/PNG hashes, CRC, pixel agreement, label/session sidecars and
complete file groups. It creates contact sheets and a manifest without changing
captures. Validation exact/dHash≤2 matches to camera training are quarantined,
never recycled into training; a missing class blocks fitting. dHash is only a
heuristic. Review the contact sheets and supply a hash-bound `label-review.json`
with `schema: 1`, `camera_manifest_sha256`, `status: "reviewed"` (or
`"reviewed_with_exclusions"`), and an `excluded` list. Each exclusion records its
relative PNG `path`, `sha256`, and semantic annotation `reason`. Do not exclude
images because the current model gets them wrong.

```bash
.work/asl/venv/bin/python -m ml.asl.adapt \
  --camera-manifest .work/asl/camera-audit-v1/manifest.json \
  --label-review .work/asl/camera-audit-v1/label-review.json \
  --output .work/asl/adapt-v1
```

Defaults and boundaries:

- Start from `run-v1/best.pth`; copy its six head rows unchanged and append OTHER.
- Keep the backbone/BatchNorm frozen; only train a seven-class classification head.
- Use 100 rehearsal examples per original class from **training only**. No original
  validation/test examples are used for rehearsal or checkpoint selection.
- Cache one base and three mild augmented views of camera **training only**.
  Horizontal flips are an explicit `--horizontal-flip` experiment, off by default.
- Sampling assigns 75% probability mass to camera training and 25% to rehearsal,
  balanced over the classes present within each domain. This intentionally
  oversamples camera examples; repetitions are not independent data.
- Train for 40 epochs, 1,024 sampled examples/epoch, batch size 32, Adam 0.001,
  one CPU thread and zero loader workers. Select the lowest class-balanced camera
  development-validation cross-entropy; leave the provisional threshold at 0.8.
- Refuse existing output directories and changed audit/source/checkpoint inputs.
  Recheck the frozen backbone and reload the full saved model to reproduce scores.

Outputs: `config.json`, exact `selected.json`, `baseline-validation.json`,
`history.json`, `best.pth`, `validation-predictions.json` and final `metrics.json`.
A missing `metrics.json` means the experiment did not complete. Reports separate
raw top-1 from **display behavior**: correct letters, rejected known letters,
wrong letters and false letters on negative scenes. Rehearsal retention metrics
are explicitly training-only, not held-out accuracy.

The new checkpoint is **schema 2**, with fixed order
`A/B/C/L/Y/NO_HAND/OTHER`; schema 1 remains six-class and backwards compatible.
The ordinary evaluator supports both schemas:

```bash
.work/asl/venv/bin/python -m ml.asl.evaluate \
  --checkpoint .work/asl/adapt-v1/best.pth \
  --folder .work/asl/camera/presenter-dev02
```

This folder command evaluates every file, including any audit exclusions. Use
the run's `metrics.json` for its exact reviewed-validation subset. In evaluator
output, `top_class: OTHER` maps to `prediction: UNKNOWN` even at high confidence.
`known_metrics` covers A/B/C/L/Y/NO_HAND; `all_class_metrics` also maps labeled
UNKNOWN/OTHER examples to the learned OTHER class for seven-class checkpoints.
Both are **raw** class metrics before the display decision. The older
`ml.asl.rejection` threshold-only study deliberately rejects schema-2 checkpoints.

See [presenter demo plan](../../docs/presenter-demo-plan.md). A fresh final camera
session, quantized accuracy, delegation/memory fit, exact-input host/device checks
and a deliberate firmware update are still required. A learned OTHER class does
not establish reliable rejection of every unseen unsupported pose.

## Checks and next gate

```bash
.work/asl/venv/bin/python -m unittest discover -s tests -p 'test_asl*.py' -v
python3 -m unittest discover -s tests -q
```

Before deployment: inspect per-class results, test real E8-camera images and
unsupported gestures, then separately quantify INT8 accuracy, delegate coverage,
linked/runtime memory and exact-input device agreement. No ASL firmware is
generated or programmed by these commands.
