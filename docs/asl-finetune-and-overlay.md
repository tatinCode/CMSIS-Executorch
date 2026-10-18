# Limited fine-tuning and screen-overlay increment

## Outcome

Limited final-block fine-tuning completed, and an isolated screen-glyph TEST UI
build passed host tests and the E8 cross-build/layout checks. **Live ASL
recognition has not passed hardware acceptance.** The user installed the first TEST UI and confirmed
moving preview, symbol cycling and recovery, but found both preview and text
upside down. Saved PNGs were confirmed upright. The revised package rotates the
whole LCD 180° after composition, leaving v4 export and ML inputs unchanged;
the user installed it and confirmed upright orientation. No agent-run hardware
access occurred. The user subsequently installed the experimental live image;
its first boot failed the numerical self-test (error 325), blocking live startup.
A separately rebuilt [live revision](../firmware/asl_live/README.md) with an
explicitly approved post-hoc tolerance and visible failure code awaits a new
deliberate user flash. The original TEST UI remains a fallback.
See the [screen-test instructions](../firmware/camera_overlay/README.md#prepared-package--user-triggered-installation).

The fine-tuned candidate is **not a deployment improvement** at the unchanged
0.80 threshold:

| Reviewed development-validation behavior | Six-class baseline | Seven-class head-only | Final-block fine-tune |
|---|---:|---:|---:|
| Correct displayed letters / 30 | 15 | 13 | 11 |
| Wrong displayed letters on those 30 | 8 | 1 | 0 |
| Suppressed known letters / 30 | 7 | 16 | 19 |
| False letters / 12 negative scenes | 8 | 0 | 0 |

Fine-tuned correct letters: A **1/6**, B **3/6**, C **1/5**, L **4/7**, Y **2/6**.
Although raw seven-class top-1 rose from **28/42 to 31/42**, that does **not** mean
more letters were successfully displayed. This distinction is why raw accuracy
alone is not the acceptance criterion. Zero false letters among these few
development negatives is not evidence of universal unknown rejection.

Supplementary scoring of the **entire 43-image development session**, including
the ambiguous NO_HAND annotation, gives 11/30 correct letters and 0/13 negative
false letters. This did not select the checkpoint or threshold. The report is
`.work/asl/finetune-validation-sensitivity-v1.json`.

## Experiment boundaries

- Start from the preserved no-flip `.work/asl/adapt-v1/best.pth` candidate.
- Update only `features.18.0.weight` (the final 320→1280 pointwise convolution)
  and the classification head. All earlier feature blocks, BatchNorm affine
  weights, running statistics and counters remain fixed and were checked.
  This does **not** unfreeze the preceding inverted-residual block.
- Backbone learning rate **1e-5**, head **1e-4**; Adam weight decay **1e-4**.
- Same audited, hash-bound split: **61** camera training images from
  `fastcheck01`/`plumbing01`; **42** development-validation images from
  `presenter-dev02`. The two semantic annotation exclusions remain unchanged.
- Same original-training rehearsal: **600** images, 100/class. No original
  validation/test samples used. Camera/rehearsal sampling mass stays 75%/25%,
  class-balanced within each domain.
- Cache the frozen prefix's outputs for a base plus three fixed mild augmented
  camera-training views. Do **not** cache the trainable block's output. No flips.
- One CPU thread, zero loader workers, 30 epochs, 1,024 samples/epoch, batch 32,
  seed 2026. Include epoch 0 in selection so a uniformly worse run can keep the
  starting model. Select minimum class-balanced development-validation
  cross-entropy; this run selected **epoch 1**.
- No threshold tuning, final-test evaluation, quantization or board access.

Run:

```bash
.work/asl/venv/bin/python -B -m ml.asl.finetune
```

The new `.work/asl/finetune-v1/` directory contains configuration/input/source
hashes, exact selected records, epoch-zero predictions, history, saved checkpoint,
reviewed-validation predictions and final metrics. Existing output directories
are refused. A run without `metrics.json` is incomplete.

Checkpoint SHA-256:
`ac819ea8bef774ea9a7ff5d16754fb1cf61fafbdaab5840919436bda7d827f99`.
Reloaded full-model logits matched prefix-cache evaluation exactly on this run
(max absolute difference **0.0**). Frozen-state verification passed, and all three
allowed trainable tensors changed. Original-training rehearsal raw accuracy went
from 600/600 to 599/600; this is **training-only retention**, not held-out accuracy.

## Screen work

The separate [`camera_overlay` target](../firmware/camera_overlay/README.md)
cycles A/B/C/L/Y/`--` below the camera view and prominently says
**TEST UI / NOT LIVE ASL**. It validates the renderer without passing ImageNet
self-test results off as camera classification. The existing inactive-buffer
ownership, cache clean, VSYNC flip and measured memory allocations are retained.

The renderer, seven-class logit gate and temporal-state helper have host tests.
The generated preview/main variants also pass the existing mocked-driver error
and acceptance scenarios. The target's actual E8 build/layout checks passed with
no new video buffers or graphics heap. The first version's screen behavior was
observed by the user; the whole-screen rotation is now confirmed upright, while
recorded self-test/capture and cold-power-cycle acceptance remain separate checks.
`tools.program_camera_overlay` now selects only the separately prepared screen-
rotation package. The ordinary capture writer still selects the preserved v4
capture-only package.

## Next decision

Do not deploy the fine-tuned model as a solved demo or lower the threshold merely
to increase displayed letters. The current training collection is small and the
cross-session gap remains. The next model iteration should target reviewed
training diversity and clearly defined presentation conditions, especially the
failed A/C/Y examples, rather than repeatedly optimizing against the same small
validation session. A fresh final session is still required after choices freeze.

For an immediate screen check, the **separately validated TEST UI programming
package is ready** for the user's deliberate flash. It remains visibly a UI test.
Live recognition still needs int8 export,
quantized validation, memory/delegation checks and exact-input device agreement
before connecting actual camera predictions to the overlay.
