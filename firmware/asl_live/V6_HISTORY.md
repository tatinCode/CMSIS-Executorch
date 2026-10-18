# Historical v6 milestone — preserved seven-class fallback

This records v6 preparation, not the current build commands. The user later
reported live recognition working (L recognized; Y weak, open/closed hands
sometimes B). That is not a reviewed boot log or cold-boot/accuracy acceptance.
See [current static-alphabet work](README.md).

The user confirmed the upright TEST UI, then installed live firmware v5. Its
first boot **failed the numerical self-test** (error 325, red LED); live startup
was correctly blocked. The revised v6 image below is built and prepared for a
new deliberate user flash, **not flashed by the agent**. The user explicitly
approved this post-hoc gate revision after reviewing the failure, as well as
choosing an experimental trial despite the
accuracy regression, and approved replacing ImageNet with an ASL boot self-test
in this new image if the two models could not fit. Original fallbacks are retained.

`include/camera_input.h` implements the real camera input contract:

- Read the original 480×480 planar RGB after full-frame completion, pipeline Stop
  and cache invalidation. Never read the LCD or rotate/mirror ML input.
- Reuse the v4 full-view, Pillow-equivalent antialiased bilinear 128×128 resize.
- Normalize float32 NCHW with the training ImageNet means/deviations.
- Use one 128-byte row scratch and an existing input allocation, not a new video
  buffer. Host tests compare every element against the training transform.

## First engineering model

Use `.work/asl/adapt-v1/best.pth` (schema 2, A/B/C/L/Y/NO_HAND/OTHER), not the
later fine-tune. At the unchanged provisional 0.80 threshold it displayed 13/30
development letters correctly, versus 11/30 for the fine-tune. Both had zero
observed false letters on 12 reviewed negative scenes. These are weak, small-set
development results, **not deployment approval** or robust rejection evidence.

The new host-only export command runs in the existing isolated inference venv:

```bash
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  .work/inference/venv/bin/python -B -m ml.asl.export \
  --output .work/asl/asl-u85-export-NEW
```

Choose a new output directory each time; failed/incomplete exports are retained.
The completed `manifest.json` is written last. The exporter:

1. Verifies the checkpoint, label review and whole-session audit bindings.
2. Calibrates on exactly the 61 reviewed camera TRAIN and 600 recorded original
   rehearsal TRAIN images. Development validation never updates observers.
3. Scores float and PT2E int8-simulated outputs on the same 42 reviewed
   development-validation images at 0.80, without threshold tuning or final tests.
4. Saves a hash-bound training reference PNG, normalized input and quantized-host
   logits; reloaded graph outputs must reproduce validation logits exactly.
5. Compiles for Ethos-U85-256 / Sram_Only and inspects the serialized PTE, not just
   the delegation text. Require one NPU subgraph; only the established CPU
   quantize/dequantize boundary kernels may remain.
6. Checks arena/scratch against the existing, unreduced runtime reservations and
   reports whether two models alone exceed the MRAM application region.

The predeclared quantization engineering gate requires no reduction in correct
displayed letters and no increase in wrong/negative false letters relative to
the float candidate. Passing it does not establish acceptable recognition.
**Host PT2E simulation is not compiled Vela or board execution.**

## Completed export and firmware checks

- Completed export: `.work/asl/export-v5/manifest.json`. Original export v4 and
  all earlier attempts are retained. Reference input, host validation logits
  and the compiled NPU payload are byte-identical to v4; only the embedded export
  directory in the PTE compile-spec metadata changed, giving a new PTE hash.
- ASL PTE: **2,555,632 bytes**, SHA-256
  `dbfb56207ad23bc9064226b7f32d3e8159d0ca58920c51e72da20c6864da410c`.
  The final graph and serialized PTE output dequantization scale are both
  **0.18568913638591766**; a mismatch fails the export/build.
- One U85 delegate; only CPU quantize/dequantize boundary operators. Planned
  arena **245,760 bytes**, scratch **491,520 bytes**. Reservations stay at
  **752,640 arena / 1,572,864 temporary / 262,144 method** bytes. The existing
  602,112-byte input and 4,000-byte output allocations are also retained.
- Float development behavior: **13/30 correct letters, 1 wrong**. PT2E int8:
  **12/30 correct, 2 wrong**. Both: **0/12 false letters on reviewed negatives**.
  The predeclared quantization nonregression gate **failed**; nothing is marked
  deployment-approved, and neither result proves robust unknown rejection.
- Quantized graph reload logit delta: **0.0**. Training-only exact reference
  input SHA-256: `370e58b2c53f95f23bf10932e6b70616caf36ed3841eae53f98b6dbf6856058c`.
- Two models alone occupy **6,391,152 bytes**, exceeding the **5,238,784-byte**
  application MRAM region. Only this separate live image replaces ImageNet.
- Firmware: `.work/inference/asl-live-firmware-v6/manifest.json`, **3,449,424
  bytes**, SHA-256
  `656d0ab4db15071fe3e70e49f40e14bbb7e0f390cd977330890ca2b989cf2442`.
  MRAM `[0x80001000, 0x8034b250)`, **1,789,360 bytes** headroom. Camera/LCD
  reservations remain **2,227,200 bytes** in SRAM1. Vectors, IRQ 366, startup
  tables, MRAM GOT, runtime/SDK/ABI and exact embedded model/reference bytes pass
  fresh build/package review.
- **371 host tests passed** including pixel/tensor agreement, experimental
  footer/bounds, camera ownership, repeated buffer flips, safe fatal-error
  blanking/failure-code rendering, startup gates, C++/Python comparison agreement,
  and inherited package/writer tamper/confirmation checks.

Build (one job, separate workspace; refuses overwrite):

```bash
python3 -B -m tools.asl_live_firmware --export .work/asl/export-v5 \
  --experimental --layout hp-only-large --jobs 1
```

## Runtime behavior and limitations

The complete camera frame is stopped/invalidated before preprocessing and NPU
execution. The persistent method is loaded once. A fresh prediction is observed
once per completed inference, not on repeated reads of an old result. Three
matching accepted predictions are needed to display a letter; negative,
different, uncertain, nonfinite or stale observations clear it. Inference faults
latch until reset and clear the glyph while preview continues.

The screen says **ASL / EXPERIMENTAL**, retains whole-LCD 180° rotation, and has
**no clock-based symbol sequence**. Original camera input is not rotated or
mirrored. No extra camera/display/NPU pools are allocated or reduced.

## First hardware failure and explicitly approved gate revision

Preserved evidence: `.work/inference/asl-live-acceptance/boot01.log`.
The original v5 binary (`364df250…`) ran three preview frames, initialized the
U85 and completed its first inference. It reported **max error 0.557068**, cosine
**0.999673**, matching top-1 A, method high-water **864 bytes**, and temporary
high-water **491,520 bytes**. The **predeclared 0.5 bound failed**; error 325
stopped startup before repeats 2/3 or live predictions. The LED became red and
the fatal handler attempted a blank `--` redraw. This is not successful live
hardware acceptance or evidence of three-repeat allocator stability.

The discrepancy is approximately **three final output quantization steps**.
That is consistent with quantized execution differences, but grid-aligned
outputs alone do not prove the cause. The earlier claim that it definitively
proved harmless rounding was too strong.

The user explicitly approved revision 2 **after observing that failure**:

- seven finite output logits;
- max absolute error **≤8 × output scale = 1.4855130910873413**;
- cosine **≥0.999**;
- identical top-1 **and** identical displayed decision at the unchanged **0.80**
  recognition threshold, including letter versus `--`;
- all previous three-repeat, unchanged-input, IRQ/job completion and no method/
  temporary high-water growth checks remain.

This is an **engineering tolerance changed post-hoc**, weaker evidence than a
pre-registered test. Eight steps is an approved trial bound, not an independently
established guarantee of acceptable numerical error. No further widening is
authorized. Firmware prints `ASL GATE revision=2 ... post_hoc=1` and each repeat's
`output_steps`. The generated header and both export manifests bind the policy
to the actual output scale; the observer rejects missing/stale policies and
independently checks all three logged seven-logit comparisons and allocator
high-water stability. C++ and Python tests cover exact/3/8/9-step cases, wrong
top-1, changed display decision, cosine failure and invalid scales.

The boot test still uses the exact exported training input, never a device-
generated golden result. Self-test predictions are never used as live
observations. Live startup stays blocked on any failed check. **Revision 2 has
not been run on hardware yet**; passing it will not validate camera recognition
accuracy, which remains weak and unchanged.

On boot self-test failure v6 attempts **SELFTEST / FAIL / numeric error code**
in the upright panel, retaining the EXPERIMENTAL footer. On other fatal camera/
runtime errors the code attempts a `--` redraw using only a
known-inactive LCD buffer. After an ambiguous/failed display flip it touches
neither buffer: failed display hardware or a stopped CPU cannot be guaranteed
to blank itself. The 1,000 ms stale limit is enforced on application updates,
not by an independent hardware watchdog. A very slow pipeline may therefore
show no stable letters; measure rather than weaken the policy.

This live image intentionally has **no boot frame export**, to avoid freezing a
live letter during a several-second transfer. `tools.capture_camera` is for the
preserved capture/UI packages, not this image. UART `latency_ms` measures
preprocessing plus model execution, not total camera-to-LCD latency.

## Deliberate installation and acceptance

Current package pointer: `.work/inference/asl-live-programming-v1/latest.json`.
Stage: `camera-inference-hhatrmoz` (v6/revision 2). The earlier v5 stage
`camera-inference-8k36dt9v`, its writer receipt and boot01 log remain historical
evidence; the writer selects only the new stage. Target:
`asl-live-experimental.debug+DevKit-E8-HP`; image:
`handgpt-asl-live-experimental.bin`. HP application plus the same DEVICE/
certificates/ATOC tail `[0x8057f0d0, 0x80580000)` (**3,888 bytes**), DEVICE +
USER_APP, **M55_HP only**. Stage is 0700 and preparation logs are 0600; do not
publish private SETOOLS files or logs containing signing material.

Read-only offline package revalidation:

```bash
python3 -B -m tools.program_asl_live review
```

To install yourself:

1. Close serial listeners. **Power off before changing SW4 to SE**, then reconnect.
2. Confirm the intended board/port, then run in an interactive terminal:

   ```bash
   python3 -B -m tools.program_asl_live program --port /dev/ttyACM0 --write
   ```

   Review the experimental warning and type exactly:
   **`PROGRAM E8-HP EXPERIMENTAL LIVE ASL`**. Without `--write` it only reviews.
   No automatic flash, retry, full erase or provisioning occurs.
3. After writer success, **power off before SW4 → U4**, then reconnect.
4. Capture a full boot log using the new read-only observer (not the v4 capture
   tool). It sends nothing; press RESET **once after it says READ ONLY**:

   ```bash
   .work/asl/venv/bin/python -B -m tools.observe_asl_live \
      --port /dev/ttyACM0 --seconds 20 --export .work/asl/export-v5 \
      --output .work/inference/asl-live-acceptance/boot02.log
   ```

   Keep boot01 unchanged; choose a fresh log name each time. Require gate
   revision 2 and all three seven-logit comparisons,
   `ASL SELFTEST PASS`, then `LIVE ASL START`, and increasing frames/predictions
   with `fault=0`, IRQs and matched started/finished jobs. The observer checks
   model/reference identities, numerical self-test and progress; it does **not**
   certify gesture accuracy or a cold boot. Share the log on any failure.
5. Observe upright **ASL / EXPERIMENTAL**, moving preview, and pose-dependent
   letters rather than cycling. Try A/B/C/L/Y with controlled placement, then
   release the hand and try unsupported poses. Record mistakes/suppressions;
   do not lower 0.80 merely to force letters. Repeat from a full cold power-on.

The preserved `tools.program_camera_overlay` and `tools.program_camera_inference`
select the original upright TEST UI and v4 capture packages, respectively, with
their own confirmation phrases. They remain fallbacks, not live recognition.
