# Presenter-specific ASL demo: next milestones

## Scope and evidence

The user expects to present the demo. Target a documented **single-presenter,
controlled-placement static handshape demo**, not a general ASL translator.
Recognize A/B/C/L/Y, retain NO_HAND, and suppress the letter for OTHER or uncertain
inputs (`--`). Do not confuse confidence with accuracy or claim generalization to
unseen people, arbitrary backgrounds, or all unsupported signs.

The inspected `plumbing01` captures are development data. They establish usable
camera export, some successful A/B/C/Y predictions, L/Y instability and confident
false letters on unsupported poses. They do not prove the cause of those errors.
Brightness summaries cannot rule lighting in or out, and a mirror experiment
cannot establish that mirroring the input fixes the model.

## 1. Faster collection — v4 capture and preview recovery observed

- Keep working Viewfinder, smoke, stored-image and v3 build/package artifacts.
- Export the full view at 128×128 using deterministic antialiased bilinear
  preprocessing; no crop, baud change, RX or additional frame allocation.
- Require byte-for-byte host agreement, CRC/framing/error tests, a single-job
  HP-only build and unchanged measured SRAM reservations.
- Prepare/review offline; **never flash automatically**.
- After the user's deliberate write, check one image, actual transfer time,
  preview resumption, self-test PASS and a cold-boot repeat before collecting a set.
- Keep the v3 path for full-resolution diagnostics; fast captures cannot support
  arbitrary later crops or a higher-resolution model.

See [capture instructions](camera-frame-capture.md). No hardware gate is implied
by a successful host test or build.
The user has now supplied a successful v4 CRC-checked 128×128 capture with a
self-test PASS log and confirmed preview resumed. A full cold-boot acceptance
record remains separate.

## 2. Presenter development data — collected, audited and session-separated

There are 105 captured examples. After two semantic annotation exclusions (no
source files changed), the first experiment uses 61 training and 42 development-
validation images, with all seven classes represented. `fastcheck01` and
`plumbing01` train; the user-confirmed separate `presenter-dev02` validates.
See the [audit and adaptation results](asl-presenter-adaptation.md).

- Fix the camera position and hand-placement guide. Prefer stable front lighting
  resembling the presentation setup. This is guidance, **not a hand detector**.
- Start with roughly 10–20 varied development examples each for A/B/C/L/Y,
  NO_HAND and UNKNOWN. Collect in multiple sessions, emphasizing L and Y.
- UNKNOWN includes unsupported shapes held **inside** the placement area,
  transitions, partial hands and relaxed/off-region hands. NO_HAND means no hand.
- Keep raw/PNG hashes and manual session/label provenance. Review labels without
  deleting hard examples merely because the existing model gets them wrong.
- Assign whole sessions to training/development validation. Never split adjacent
  captures randomly or augment across split boundaries. `plumbing01` is not a
  final test; it may support training only after explicit split/label review.

## 3. Camera-domain adaptation — initial head-only experiments complete, blocked

A subsequent limited final-convolution fine-tune also completed: 11/30 correct
displayed letters and 0/12 negative false letters at 0.8. It is not deployment-
approved. The user installed the separate TEST UI overlay and confirmed the
whole-screen orientation fix is upright. It still has no ASL inference. See
[fine-tuning and overlay status](asl-finetune-and-overlay.md).

The no-flip and flip candidates improve negative behavior but suppress too many
known letters; neither is deployment-approved. At threshold 0.8 the no-flip run
displays 13/30 validation letters correctly (baseline 15/30) and produces no false
letters on the 12 reviewed negatives (baseline 8/12). Do not flash it as a solved
ASL demo. See the linked results for counts, exclusions and limitations.

- Preserve `run-v1/best.pth`; use a new run, checkpoint schema/class contract and
  output directory for any seven-class model with explicit OTHER.
- Keep MobileNetV2 and the PyTorch/ExecuTorch/Ethos pipeline. Mix camera training
  data with the existing training split, beginning with frozen-backbone/head
  adaptation; unfreeze selectively only if development validation supports it.
- Treat horizontal-flip augmentation and class weighting as measured experiments,
  not assumed fixes. Preserve existing Kaggle validation/test boundaries.
- Use development validation to select model/threshold. Measure thresholded
  correct letters, L→Y/Y→L errors, NO_HAND behavior and false letters on negatives,
  as well as raw top-1 accuracy, coverage and confusion matrices.
- A learned OTHER class is not guaranteed rejection of every unseen pose.
  Cropping/ROI alone cannot establish hand presence or eliminate false letters.

## 4. Untouched final session — after development choices are frozen

Capture a new session without tuning against it. Include all five letters,
empty scenes, unseen unsupported poses and release/transitions. Report counts
alongside percentages, separate results for the presenter, and retain failures.
If the model changes after inspecting this session, it becomes development data
and a new final session is needed.

## 5. On-device letters — v6 user-reported working; static alphabet expansion

The user reports v6 live predictions working (L recognized; Y and negative
rejection weak). No reviewed three-repeat/cold-boot log or independent accuracy
claim follows from that report. The user now requests **all 24 static letters**
(exclude J/Z/delete/space), individual-letter display only. A separate static-26
model/build/package retains v6 as a fallback. **19 letters lack reviewed camera
examples**; adding outputs does not fix domain shift or guarantee rejection.
See the [static alphabet audit](asl-static-alphabet.md) and
[current live workflow](../firmware/asl_live/README.md).

### Preserved first-live-build history

The engineering candidate is the preserved head-only seven-class model,
not the weaker displayed-letter fine-tune. Training-only int8 export completed;
PT2E behavior is 12/30 correct letters, 2 wrong, and 0/12 negative false letters.
The user explicitly chose an experimental live build despite the quantization
regression and approved a replacement ASL boot self-test in the separate image.
The user flashed v5; its first inference failed the predeclared 0.5 numerical
gate at 0.557068 (cosine 0.999673, matching top-1), so live startup was blocked
with error 325. The user approved a **post-hoc** revision: at most eight final
output quantization steps, cosine ≥0.999, and matching top-1 and displayed
decision at 0.80. Revision 2 retains all three-repeat/IRQ/input/allocator checks;
the original failure log and package remain preserved. The separate v6 image
adds an on-screen self-test failure code. It still needs a deliberate user flash
and fresh boot02/cold-boot acceptance; no successful live recognition is claimed. See
[live integration status](../firmware/asl_live/README.md).

- Export an isolated int8 ASL `.pte`; check delegation, model memory, quantized
  accuracy and host/device agreement on identical inputs before camera hookup.
- Reuse the verified full-view preprocessing and unchanged IRQ-driven NPU path.
- Add glyphs during the CPU RGB565 copy without a graphics heap. Show letters
  only when accepted; show `--` for OTHER/NO_HAND/uncertainty.
- Add brief temporal stabilization and measure latency/flicker and false displayed
  letters. Smoothing does not fix a consistently wrong class.
- Preserve the stored-image fallback package. The new live image uses the
  explicitly approved three-repeat exact-input ASL self-test because both models
  cannot coexist in MRAM. Require a deliberate flash and hardware acceptance.
