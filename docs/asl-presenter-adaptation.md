# First presenter-adaptation experiments: improved rejection, not demo-ready

## Outcome

Two **host-only**, frozen-backbone MobileNetV2 head-adaptation runs completed.
Both learned useful negative behavior on this small development-validation
session, but **neither recognizes enough letters to deploy**. No model was
exported/quantized, no firmware or memory reservation changed, and no board was
accessed. The original six-class checkpoint is retained.

At the unchanged, provisional 0.80 threshold:

| Reviewed validation behavior | Original baseline | Adaptation, no flips | Adaptation, flips |
|---|---:|---:|---:|
| Correct displayed letters / 30 | 15 | 13 | 10 |
| Wrong displayed letters on those 30 | 8 | 1 | 0 |
| Suppressed known letters / 30 | 7 | 16 | 20 |
| False letters on 12 reviewed negative images | 8 | 0 | 0 |
| Correct displayed L / 7 | 3 | 5 | 4 |
| Correct displayed C / 5 | 0 | 0 | 0 |

The no-flip run's per-letter correct outputs were A **3/6**, B **3/6**, C **0/5**,
L **5/7**, Y **2/6**. Its raw top-1 L was 6/7, but one was below threshold;
do not report that as six successful displayed letters. Flip augmentation
lowered the selected balanced validation loss but reduced displayed-letter
coverage further. Neither experiment is approved for deployment.

**Zero observed false letters is not proof of robust rejection.** There are only
12 reviewed negative validation examples, from one presenter and one validation
session. The learned OTHER class does not solve every unsupported pose.

## Data audit and label decisions

- CRC/hash/pixel-verified inventory: **105** captures, with no exact normalized
  duplicates and no validation-to-training dHash≤2 matches.
- Whole sessions: `fastcheck01` (45) + `plumbing01` (17) for training;
  `presenter-dev02` (43) for development validation. The latter batch was moved
  after the user confirmed it was a separate collection accidentally saved under
  `fastcheck01`. Its independence is user-reported, not signer independence.
- Contact sheets for all 105 images were inspected before fitting/validation
  scoring. Two NO_HAND annotations contain visibly present hands:
  - Training: `fastcheck01/NO_HAND/20261018T153110Z-ceafa5124f1a.png`, hand touching head.
  - Validation: `presenter-dev02/NO_HAND/20261018T155828Z-083d1342807d.png`, hand reaching toward lens.
- Those two were excluded from fitting/primary validation pending annotation
  confirmation, **not deleted or relabeled**. Hard/shadowed/sideways examples were
  otherwise retained. Labels are presenter-supplied, not expert ASL annotations.
- Reviewed totals: **61 training**, **42 validation**; all seven classes present.
- Audit and hash-bound decisions: `.work/asl/camera-audit-v1/manifest.json` and
  `label-review.json`. Captures and sidecars were not modified by adaptation.

To avoid hiding the ambiguous validation negative, the completed checkpoints
were also scored on **all 43** validation images. The original baseline produced
false letters on **8/13 negatives**; both adapted candidates produced **0/13**.
The excluded training image was checked separately and suppressed by both
candidates; it is not held-out evidence. See
`.work/asl/adaptation-comparison-v1.json`. The primary fitting/selection set was
not changed after viewing these supplementary results.

## Training and reproducibility

Commands and tests are documented in [`ml/asl/README.md`](../ml/asl/README.md).
Both experiments start from the same `run-v1/best.pth`:

`e1d34bc84c3d252119031e72b1fb095658f573d1f2eb9ae2db00e1ae5d1e73dd`.

- Seven-class schema 2: A/B/C/L/Y/NO_HAND/OTHER. Capture UNKNOWN maps to OTHER.
  Schema-1 loading and the original class order remain supported.
- Copy the existing six linear-head rows and initialize an additional OTHER row.
  Backbone and BatchNorm state remain frozen and are checked after training.
- Camera training: one unchanged plus three fixed mild affine/color views.
  The paired experiment changes only the optional camera-training mirror flag.
- Rehearsal: 100/class, **600 original training images only**. Neither original
  validation nor test is used for rehearsal or checkpoint selection.
- Camera/rehearsal sampling probability mass 75%/25%, class-balanced within
  each domain. Resampling/augmentation does not increase independent sample size.
- One CPU thread, zero loader workers, 40 epochs, 1,024 samples/epoch, batch 32,
  Adam 0.001, seed 2026. Select minimum class-balanced cross-entropy on the camera
  development-validation session. Threshold stays at 0.80; no calibration claim.
- No-flip selection: epoch **4**; mirrored selection: epoch **3**.
- Reloaded complete-network evaluation reproduced cached-feature top classes and
  display decisions. Max absolute logit differences: **4.77e-7** and **9.54e-7**.
- Original-training rehearsal raw accuracy remained 100% on the checked samples;
  this is a training-retention check, **not a held-out generalization result**.

Artifacts are isolated, and output directories are never overwritten:

| Run | Directory | Checkpoint SHA-256 |
|---|---|---|
| No-flip | `.work/asl/adapt-v1/` | `59ed60e64c407911368983ca86316f8114fa016aaa56dea19fdd9bdc03a34c01` |
| Flip | `.work/asl/adapt-v1-flip/` | `a1de5ce906a0928761f4b70784d68acb2c81240a382ea7fda4247a21c9e68b1c` |

Each contains exact source/input/configuration hashes, selected paths/splits,
per-epoch history, baseline/adapted predictions and `metrics.json`. A checkpoint
without final metrics is not a completed run. The original model, manifests,
captures and firmware/package artifacts are fingerprinted in
`.work/asl/adaptation-protected-state-v1.json`.

## Next gate

Update: the [limited final-block fine-tune and isolated screen-overlay increment](asl-finetune-and-overlay.md)
have now completed. Fine-tuning did not improve displayed-letter coverage. The
TEST UI builds successfully but is not live inference and has not been installed.

The no-flip model correctly displays 39/42 training letters but only 13/30
validation letters, so training fit is not the milestone. The next useful
experiment is limited backbone adaptation and/or targeted training diversity for
the failed classes and presentation conditions—not lowering the gate to make a
few examples pass, and not flashing this candidate now.

Continue treating `presenter-dev02` as development data. Collect a fresh final
session **after** model/threshold choices are frozen, then separately establish
int8 accuracy, delegation/memory fit and exact-input host/device agreement before
the deliberate on-device letter-overlay update.
