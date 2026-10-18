# Solo-presenter camera adaptation trial

## Scope

Try the existing reviewed camera examples together with the new p01 pilot
captures and Kaggle rehearsal. Keep all 24 static letters, NO_HAND and OTHER,
threshold **0.80**, existing memory reservations and revision-2 boot gate.
This is individual-letter recognition, not word accumulation or online learning.
No automatic flash or serial access. Current live v7 and the collector remain
separate fallbacks with unchanged package pointers.

## Data review and split

- Frozen completed collection sessions: p01/pilot01 through pilot06, **33**
  A–G captures. All protocol logs/CRC/hashes/PNG-versus-raw pixels checked.
- Visual engineering review of every contact-sheet image, plus enlarged D.
  This is **not certified sign-language annotation**. Exclude three new A
  images (thumb placement unclear; use old reviewed A) and the foreshortened
  first D. Original files/labels remain untouched; exclusions bind PNG hashes.
- Retain **29 new TRAIN examples**: B/C/E/F/G five each, D four. These are
  **not** held-out new-letter validation. They are added to the old **61 TRAIN**
  images. Keep the original **42 development-validation** images unchanged.
- New images are checked against old validation by exact normalized pixels and
  dHash radius 2; quarantine the NEW example rather than move/delete validation.
  No matches found in this snapshot. Source hashes/CRC checked again on reload.
- Combined train counts: A 8, B 11, C 13, D 4, E 5, F 5, G 5, L 14, Y 6,
  NO_HAND 7, OTHER 12 (**90 total**). Camera development validation still covers
  only A/B/C/L/Y and negatives. **19 letters have no held-out camera examples;
  15 have no camera training examples.**
- Old captures are treated as the same presenter based on the user's account,
  not an independently verified identity. No unseen-person accuracy is claimed.

Review: `.work/asl/personal-review-v1/label-review.json`. Training inventory:
`.work/asl/personal-camera-v3/manifest.json`. Earlier manifest attempts are
retained. Later/newly active sessions are **not** silently included in this run.

### Audit repairs

An original log may contain bytes drained before RESET, including an older
instance of the same cycle number. The auditor now validates the final complete
boot/READY context and the exact canonical saved packet suffix, leaving the raw
log untouched. CRC, frame IDs, log framing and PNG/raw checks are not relaxed.

An additional capture session appeared when the first training launch began;
the strict full-root auditor stopped the run **before fitting/output creation**.
The personal loader now inventories exactly the completed sessions in its frozen
audit, while requiring all those sessions to remain present/unchanged. New active
sessions can continue independently. The general whole-root audit stays strict.

## Training and selection

- Start from the preserved static-26 float checkpoint used for v7, not a random
  head. Freeze backbone and BatchNorm; fit only the existing 26-output head.
- Camera TRAIN: 90 base images and three mild augmented views; no mirroring.
  Kaggle: the same selected 600 TRAIN images/class plus one augmented view.
- Class-balanced sampling inside each domain, 50% camera / 50% Kaggle; 8,192
  samples/epoch, Adam 0.0003, 20 epochs, seed 2026, two CPU threads.
- Select minimum mean camera/Kaggle development class-balanced cross-entropy
  only among epochs with at least the initial correct camera displays, no more
  wrong camera letters and no more negative false letters. Unchanged epoch 0 is
  an explicit fallback; if it wins, do not package an alleged adaptation.
- New-image training-fit diagnostics are clearly marked **NOT VALIDATION**.
  No new final camera session or further dataset-test predictions are evaluated.
  Old validation has already guided previous models; it remains development
  evidence, not an untouched final result.

### Explicit trial selection after the conservative run

`personal-train-v1` selected epoch **0**, the unchanged v7 float model: no
trained epoch beat its combined loss while satisfying the camera display-count
limits. It is **not an adaptation** and is deliberately rejected by the personal
exporter. Its metrics/checkpoint and exact training-source snapshot are retained.

For a new-camera hardware experiment, `personal-train-v2 --adapted-trial` uses
the same data/seed/hyperparameters and the same camera display-count limits,
but selects the best **trained** eligible epoch even if combined loss is worse.
This choice was made **after observing v1**, so it is development tuning, not a
pre-registered improvement result. Report Kaggle and loss regressions explicitly.
This does not change threshold 0.80, boot tolerance, or validation-to-training
boundaries. If no trained epoch meets camera-count limits, export still refuses.

## Isolated workflow

```bash
.work/asl/venv/bin/python -B -m ml.asl.personal_data
OMP_NUM_THREADS=2 .work/asl/venv/bin/python -B -m ml.asl.personal_train \
  --adapted-trial --output .work/asl/personal-train-v2
OMP_NUM_THREADS=1 .work/inference/venv/bin/python -B -m ml.asl.personal_export
python3 -B -m tools.personal_asl_firmware --experimental --layout hp-only-large --jobs 1
python3 -B -m tools.prepare_personal_asl prepare \
  --setools-root <installed-SETOOLS> --experimental --hp-only-board
python3 -B -m tools.program_personal_asl review
```

Existing outputs are never overwritten. Export and builder/package engines are
private instances of established helpers; original model/build/package pointers
and capture globals are not mutated. New TRAIN images can calibrate PT2E; old
validation and dataset validation/test cannot update observers or gradients.
Float/PT2E/reload diagnostics on new TRAIN are not an independent accuracy test.

## Deliberate hardware trial (only after package review)

Close the collector/serial tools. Power off, SW4 -> SE, reconnect:

```bash
python3 -B -m tools.program_personal_asl program --port /dev/ttyACM0 --write
```

Type `PROGRAM E8-HP PERSONAL ASL`. After success, power off, SW4 -> U4, reconnect.
Arm the read-only observer, then RESET once:

```bash
.work/asl/venv/bin/python -B -m tools.observe_asl_live \
  --port /dev/ttyACM0 --seconds 30 --export .work/asl/personal-export-v1 \
  --output .work/inference/asl-live-acceptance/personal-boot01.log
```

Require three 26-logit self-test repeats, `ASL SELFTEST PASS checked=26 repeats=3`,
`LIVE ASL START`, increasing frames/predictions and `fault=0`. Never widen limits
after a failure. Then test fresh A/B/C/D/E/F/G/L/Y poses, no-hand, release and
unsupported poses. Record failures and repeat from cold power-on. Numerical
PASS is not gesture accuracy; new poses/placement must be tested live, not just
the training screenshots. Continue collecting in a fresh session afterward.

## Completed float trial

Selected trained epoch **3**. Checkpoint SHA-256:
`ae7b22a8ff7c1ea0ad6abb5b7646d8222cdeec6c740004b90c98787808b2b297`.
Backbone/BatchNorm unchanged; full-model reload logit delta **0.0**.

| Evidence | Starting v7 float | Personal trial float |
|---|---:|---:|
| Old camera correct displays / 30 | 12 | **12** |
| Old camera wrong letters | 3 | **1** |
| Old camera negative false letters / 12 | 0 | **0** |
| Kaggle validation top-1 / 2,359 | 87.16% | **85.08%** |
| New 29-image **TRAINING FIT ONLY** | 8 correct, 10 wrong | **29 correct, 0 wrong** |
| Combined development loss | 1.064258 | **1.136249 (worse)** |

New training-fit counts: B 5/5, C 5/5, D 4/4, E/F/G 5/5 each. These are the
images used for gradients, **not proof of fresh-pose recognition**. Old camera
development A remains **0/6**, B 3/6, C 1/5, L 5/7, Y 3/6. Reusing old A data
does not establish that A now works at the present camera placement.

Camera display-count nonregression passes; combined-loss nonregression fails
and Kaggle performance regresses. This is a presenter trial, not an overall
accuracy improvement. **473 host tests pass**, including pre-reset log handling,
frozen-session inventory, no validation-to-training leakage, private engine and
package selection, and inherited writer/confirmation/tamper checks.

## Completed int8 export

`.work/asl/personal-export-v1/manifest.json`: **2,586,528-byte** PTE, SHA-256
`75ff349d26e1b6735af148dbbf5a603753e9a887eebbabe13e861515f5bc0d4a`.
One U85 delegate, only existing CPU quantize/dequantize boundary operations.
All measured runtime pools still fit unchanged. Calibration is **90 camera
TRAIN + 250 Kaggle TRAIN**, never the 42 old camera validation images.

| Evidence | Personal float | Personal PT2E int8 simulation |
|---|---:|---:|
| Old camera correct displays / 30 | 12 | **10** |
| Old camera wrong letters | 1 | **1** |
| Old camera negative false letters / 12 | 0 | **0** |
| Kaggle validation top-1 / 2,359 | 85.08% | **84.57%** |
| New 29-image **TRAINING FIT ONLY** | 29 correct | **29 correct** |

Both quantization nonregression gates **fail**. Compared with original v7 PT2E
(11 correct / 2 wrong), this trial has one fewer correct old-camera display and
one fewer wrong letter. It is not an overall recognition improvement. Personal
PT2E old-camera per-class displays: A 0/6, B 3/6, C 1/5, L 3/7, Y 3/6.

Quantized reload logit delta **0.0**, including the new TRAIN diagnostic images.
No held-out D/E/F/G camera examples exist. **29/29 proves fitting those training
images, not live recognition or robustness.** Fresh poses and placement must be
checked after the user's deliberate flash.

Output scale **0.21669653058052063**; the unchanged eight-step gate gives max
absolute error **1.733572244644165**, cosine >=0.999, matching top-1 and 0.80
display decision, three repeats/input integrity/IRQ/job/allocator stability.
No threshold/tolerance widening is used to compensate for failed accuracy gates.

## Completed build and offline package review

- Workspace: `.work/inference/asl-personal-firmware-v1/manifest.json`.
- Binary **3,480,656 bytes**, SHA-256
  `e5ff47db9bb6c186ef0afebeec57286325b5c2d4aa97fe90e67f101607ed2175`.
- MRAM `[0x80001000, 0x80352c50)`, **1,758,128 bytes** headroom. Original
  SRAM1, arena, temporary, method, input/output, heap and stack reservations
  unchanged. Actual serialized PTE, ARM ABI, source/generation identities,
  vectors/GOT/layout and embedded model/reference bytes revalidated.
- Separate package stage `.work/inference/asl-personal-programming-v1/`
  `camera-inference-xecng54x`; binary TOC/map/CRC and HP-only DEVICE + USER_APP
  checks pass. Private stage 0700 and preparation log 0600. Writer review passes.
- **473 tests pass**. V7 live and continuous collector package reviews still
  pass. `git diff --check` and Viewfinder status clean.
- Integrity evidence `.work/inference/personal-asl-integrity-v1.json`: **2,774
  protected files unchanged**, with only the intentional collection-audit fix
  and regression-test changes among the protected existing files. New files
  implement the isolated trial. Original captures/checkpoints/builds/packages/
  pointers unchanged; all **312 backbone/BatchNorm tensors** independently
  compared equal. Only classifier weight and bias tensors changed.

**Nothing flashed; no hardware serial port opened.** The personal package is
ready only for a deliberate user trial. Stop the collector with Ctrl-C (ideally
at its next prompt) and close serial tools before writing. Require the boot,
fresh-pose, negative/release and cold-boot checks above. A remains weak and both
quantization nonregression gates fail; keep v7/collector fallbacks available.
