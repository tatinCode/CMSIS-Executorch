# First host ASL classifier: results and limits

## Result

The first six-class **host-only** MobileNetV2 transfer-learning run completed.
It distinguishes the selected classes well on the prepared Kaggle split, but
**unsupported-sign rejection fails the intended behavior**. Initial E8-camera
development captures now show L/Y confusion and confident false letters on
unsupported poses; there is no independent camera accuracy benchmark yet.
These results do not establish an embedded ASL demo.

| Measurement | Result |
|---|---:|
| Classes | A, B, C, L, Y, NO_HAND |
| Input | RGB, centered square, 128×128, ImageNet normalization |
| Training images | 3,600 (600/class), plus one augmented view each |
| Validation images | 938 |
| Held-out test images | 1,057 |
| Selected epoch | 30, lowest validation cross-entropy |
| Validation accuracy | 98.40% |
| Validation balanced accuracy | 98.60% |
| Held-out test accuracy | 99.91% (1,056/1,057) |
| Test coverage at provisional confidence ≥0.8 | 98.86% (1,045/1,057) |
| Accuracy on accepted test images | 100% (1,045/1,045) |
| Unsupported static-letter stress test | 570 images, 30 from each of 19 other static letters |
| Unsupported images rejected at confidence <0.8 | 29.82% (170/570) |
| Unsupported images incorrectly accepted as a letter | **70.18% (400/570)** |

The only closed-set test error was Y → A. The larger validation errors were
10 L → A, 3 Y → A, 1 Y → B, and 1 B → A. High accepted accuracy on known classes
must not be confused with reliable rejection of unsupported signs.

## Data and leakage checks

- Decoded all 18,000 selected-class images; 3,000/class.
- No exact decoded-pixel duplicates found among those classes.
- Grouped consecutive filename numbers in blocks of 100, then assigned whole
  groups to splits using seed 2026.
- Quarantined 2,444 proposed evaluation images with exact/near matches to earlier
  splits (64-bit dHash distance ≤2). No quarantined image was added to training.
- Retained counts before training's sampling caps:

| Split | A | B | C | L | Y | NO_HAND |
|---|---:|---:|---:|---:|---:|---:|
| Train | 2,200 | 2,200 | 2,200 | 2,200 | 2,200 | 2,200 |
| Validation | 179 | 136 | 261 | 176 | 264 | 47 |
| Test | 267 | 207 | 202 | 232 | 328 | 57 |

The sample sheet shows repeated backgrounds/capture conditions. Filename blocks
are not verified signer/session labels, and perceptual hashes cannot prove the
absence of leakage. **The very high test accuracy is not a prediction of
unseen-person or E8-camera accuracy.** The small supplied 28-image test folder
was not used to select the checkpoint or compute the primary metrics.

## Reproducibility and artifacts

Commands and component explanations: [`ml/asl/README.md`](../ml/asl/README.md).
One CPU thread, zero loader workers; PyTorch 2.10.0+cpu, torchvision 0.25.0+cpu,
NumPy 2.2.6 and Pillow 12.1.0 in `.work/asl/venv`. Adam trains only the new head;
the pretrained backbone and BatchNorm statistics remain frozen. Two fixed views
per training image are used, not fresh augmentations every epoch.

- Dataset manifest: `.work/asl/dataset-v1/manifest.json`
  - SHA-256: `50acf537c69e2b8903a379478d1b6cc1e7c3e0a98e22da6bbb7ad67b2d9309c1`
- Checkpoint: `.work/asl/run-v1/best.pth`
  - SHA-256: `e1d34bc84c3d252119031e72b1fb095658f573d1f2eb9ae2db00e1ae5d1e73dd`
  - 2,231,558 model parameters; serialized FP32 checkpoint is 9,151,051 bytes.
  - This file size is not SRAM use or the size of a future quantized PTE.
- Training history, selected-image list and metrics: `.work/asl/run-v1/`.
- Unknown-sign evaluation: `.work/asl/unknown-report.json`.
- Resolved environment: `.work/asl/environment.freeze.txt`.

An independent check reloaded `best.pth` and ran the complete network directly
on all 1,057 test images. Every predicted class matched cached-feature evaluation;
maximum absolute logit difference was `1.9073486328125e-06`.

The full host test suite passed with 207 tests, including seven new checks for
group isolation, near-match quarantine, deterministic splitting, preprocessing,
checkpoint label order/round-trip, manifest leakage and rejection metrics.

## Next gates

1. Evaluate independently captured E8 images under expected demo conditions,
   including empty scenes and unsupported handshapes. A fixed hand-placement
   region keeps the first demo's scope manageable.
2. Improve rejection using a separate development set of negative gestures and
   camera-domain data; consider an explicit OTHER class if needed. The tested
   unknown images have now been inspected, so retain a fresh final test set for
   subsequent rejection changes. Do not silently tune thresholds on final tests.
3. Only after local validation, export/quantize this candidate separately and
   measure delegation, MRAM/SRAM/TCM use and numerical agreement.
4. Validate stored inputs on the E8 before connecting live-camera classification.

No ASL export, firmware build, serial access or board programming was performed
as part of this host training work. Existing inference reservations remain intact.

## Follow-up: threshold rejection study

`ml.asl.rejection` now treats the previously inspected 570 unsupported-letter
images as **development data**. It recomputes their predictions, uses only the
938-image known validation split to select an operating point, then evaluates
570 additional unsupported-letter images. Those additional images come from
different filename-number blocks and have exact/dHash-near matches to development
removed. This remains a same-dataset test, not unseen-signer/camera validation.

The tested threshold grid spans 0.5–1.0 in steps of 0.0025. None met all three
development requirements: ≥90% known-image coverage, ≥95% accuracy on accepted
known images, and ≤5% acceptance of unsupported signs.

The best tested tradeoff retaining ≥90% known coverage was threshold **0.98**:

- Known validation coverage: **90.30%**; accepted accuracy: **99.65%**.
- Development unknown acceptance: **38.07%**, down from 70.18% at 0.8.
- Additional unknown-image test: **38.07% false-letter rate** (217/570).

The study records `blocked_no_feasible_threshold` for this grid. No threshold
was approved or installed into the classifier; its default remains the explicitly
provisional 0.8. A confidence increase alone did not solve rejection. The next
model experiment should learn negative/OTHER examples using development data,
including real camera scenes, and retain a new final evaluation session.

Report: `.work/asl/rejection-v1/report.json`; selected additional image identities:
`.work/asl/rejection-v1/fresh_unknowns.json`. Reproduce in a new output directory:

```bash
.work/asl/venv/bin/python -m ml.asl.rejection --output .work/asl/rejection-repeat
```

Physical image collection is working through the user-programmed v3
[camera capture revision](camera-frame-capture.md). A faster v4 candidate matches
host preprocessing on the inspected v3 frames but still needs its own hardware
test. See the [presenter demo plan](presenter-demo-plan.md) for collection,
adaptation, negative-class and independent-evaluation boundaries.
