# Old-camera replay + new captures

This candidate addresses the reported A/L/Y regression without moving old
validation into training or lowering the 0.80 display threshold.

- Camera TRAIN: original **61** reviewed frames plus **90** usable continuous
  captures from p01/pilot01–pilot13 (**151 total**).
- The same four ambiguous new A/D frames remain excluded. Latest 61 H/I/K/M/N/O/P
  frames passed protocol/CRC/hash/pixel checks and visual engineering review.
  Thumb details in K/M/N are not independently certified ASL annotations.
- Original **42 camera development-validation** frames stay out of gradients and
  calibration. There is no fresh held-out camera set for the newly added letters.
- Rehearsal: 600 Kaggle TRAIN/class, base + augmented view. No J/Z/del/space.
- Frozen MobileNetV2 backbone/BatchNorm, 26-output head fitting, 90% class-balanced
  camera sampling / 10% Kaggle; 60 epochs, 8,192 samples/epoch, learning rate 0.001.
- Selection requires aggregate camera nonregression plus separate correct-display
  floors **A >=3/6, L >=5/7, Y >=3/6**. These are development-count safeguards,
  not claims that live recognition is solved. Int8 must meet the same floors,
  zero negative false letters and at most one wrong letter before packaging.
- All new capture scores after fitting are **TRAINING FIT ONLY**, not accuracy.
- Revision-2 numerical boot gate, output threshold, camera orientation, LCD
  rotation, IRQ, MRAM GOT and measured runtime reservations remain unchanged.

## Separate artifacts

- Audit/review: `.work/asl/personal-review-v2/`.
- Merged manifest: `.work/asl/personal-camera-v4/manifest.json`.
- Training: `.work/asl/replay-train-v1/` (no eligible trained epoch; rejected),
  then `.work/asl/replay-train-v2/` (A restored but no eligible epoch),
  then `.work/asl/replay-train-v3/` (A restored, A/L/Y rows fixed and original
  legacy TRAIN logits preserved by an additional MSE loss, if checks pass).
- Export: `.work/asl/replay-export-v1/` (only after successful training).
- Firmware: `.work/inference/asl-replay-firmware-v1/`.
- Package pointer: `.work/inference/asl-replay-programming-v1/latest.json`.
- Protection snapshot: `.work/inference/replay-protected-v1.json`.

Previous v7, personal-trial and collector artifacts/pointers are not overwritten.
The replay helper refuses to package an int8 candidate that fails A/L/Y checks.

## Offline preparation

```bash
OMP_NUM_THREADS=2 .work/asl/venv/bin/python -B -m ml.asl.replay_train
OMP_NUM_THREADS=2 .work/asl/venv/bin/python -B -m ml.asl.replay_fit
OMP_NUM_THREADS=1 .work/inference/venv/bin/python -B -m ml.asl.replay_export
python3 -B -m tools.replay_asl build --experimental --layout hp-only-large --jobs 1
python3 -B -m tools.replay_asl prepare --setools-root <installed-SETOOLS> \
  --experimental --hp-only-board
python3 -B -m tools.replay_asl review
```

Do not flash until the new package exists and review passes. No serial device is
opened by training, export, build, package preparation or package review.

## Deliberate user flash, after review passes

Stop the collector/serial monitors. Power off, set SW4 to SE, reconnect:

```bash
python3 -B -m tools.replay_asl program --port /dev/ttyACM0 --write
```

Type exactly **`PROGRAM E8-HP REPLAY ASL`**. After tool success, power off,
set SW4 to U4 and reconnect. Start the read-only observer:

```bash
.work/asl/venv/bin/python -B -m tools.observe_asl_live \
  --port /dev/ttyACM0 --seconds 30 --export .work/asl/replay-export-v1 \
  --output .work/inference/asl-live-acceptance/replay-boot01.log
```

After READ ONLY, RESET once. Require three self-test repeats,
`ASL SELFTEST PASS checked=26 repeats=3`, `LIVE ASL START`, increasing frames and
predictions and `fault=0`. Do not widen limits after a failure. Test fresh A/L/Y
first, then the other collected letters, no-hand and releasing poses. Repeat
from a cold boot. Numerical self-test PASS is not gesture accuracy.

## Completed float selection

The first replay run never recovered A. Restoring the old A row and simply
training other rows also failed the A/L/Y requirements; neither run is released.
The selected third run restores the A weight/bias row **by label** from
`adapt-v1/best.pth`, keeps A/L/Y head rows fixed, and preserves pre-fit logits on
the **61 original TRAIN examples only** with MSE weight 2.0. This is a learning
loss, not firmware smoothing or a runtime override. Other outputs fit normally.
Learning rate 0.0003; same 60 epochs/90% camera sampling and frozen backbone.
Features are reused from the first run, with dependency hashes and label checks;
full-model camera validation is checked against the cached features afterward.

Selected epoch **5**, old camera development **14/30 correct displays**, one
wrong letter, **0/12 negative false letters**. Per-class displays: A **3/6**,
B **2/6**, C **1/5**, L **5/7**, Y **3/6**. This passes the declared floors,
not perfect recognition. A remains only 3/6; fresh poses must be tested live.

All-camera **TRAINING FIT ONLY**: A 7/8, other training classes all fitted,
including all **90/90 continuous training frames**. No held-out newly added
letters exist. Kaggle validation top-1 **82.83%**, below the previous personal
float 85.08%; this is a camera-focused presenter experiment, not an overall
benchmark improvement. Original data/checkpoints/packages remain unchanged.

## Final result: rejected for release

Int8 simulation gives **A 2/6, L 3/7, Y 3/6**, below the unchanged A/L/Y floors.
Overall old development: **13/30 correct**, one wrong, zero negative false
letters. Kaggle top-1 **81.18%**. All-camera training fit **130/151**, not
held-out accuracy. Both domain quantization nonregression checks fail.

**No replay firmware/package was built or released. No serial port opened and
no flash attempted.** Do not use the previous personal image as this update;
it does not contain the latest captures or solve the reported A/L/Y issue.

The export wrapper also hit an exclusive-create error while adding final
metadata to its already-created manifest. The raw failed attempt is retained,
with its exact wrapper source snapshot at
`.work/asl/replay-export-v1-source.py`. The wrapper has since been fixed to
finalize its own manifest atomically and regression-tested. This does not make
the rejected int8 model pass; no release was fabricated by editing metrics or
lowering thresholds. Further model work is required before a valid flash image.
