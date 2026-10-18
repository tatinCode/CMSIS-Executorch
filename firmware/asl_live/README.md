# Experimental static ASL alphabet

Individual-letter recognition, **not words or name accumulation**. Output order:

```text
A B C D E F G H I K L M N O P Q R S T U V W X Y NO_HAND OTHER
```

J/Z require motion and are unsupported. Kaggle `J`, `Z`, `del`, and `space`
folders are excluded entirely, not relabeled OTHER. `nothing` maps to NO_HAND;
OTHER uses the existing reviewed camera negatives. Excluded gestures are **not
guaranteed** to produce `--`. This is not a robust sign-language interpreter.

**19 new letters have no reviewed E8-camera examples.** Only A/B/C/L/Y and
negative scenes have camera development data. Dataset performance cannot
establish that ANGELA or any name can be signed reliably on the board.

## Preserved baseline

The user reported v6 working, including L, but Y and rejection remained weak.
[V6 history](V6_HISTORY.md) records that model and its post-hoc gate revision.
V5/v6 firmware, exports, packages, old latest pointer and logs remain unchanged.
New workspace: `.work/inference/asl-live-firmware-v7/`. New package pointer:
`.work/inference/asl-static-programming-v1/`. Matching v6 source is preserved in
commit `279be0e` and `.work/inference/static-asl-v1-baseline/source.tar`.
Current helpers reject stale build identities; old packages must be used with
their matching source revision, not by bypassing checks.

## Host workflow (no device access)

Use existing isolated environments; no global installs. Never overwrite an
existing output directory; choose a new version when retrying.

```bash
OMP_NUM_THREADS=1 .work/asl/venv/bin/python -B -m ml.asl.static_data
OMP_NUM_THREADS=2 .work/asl/venv/bin/python -B -m ml.asl.static_train
OMP_NUM_THREADS=2 .work/asl/venv/bin/python -B -m ml.asl.static_train \
  --output .work/asl/static-train-v2 --camera-fraction 0.25 --samples-per-epoch 16384
OMP_NUM_THREADS=1 .work/inference/venv/bin/python -B -m ml.asl.static_export \
  --checkpoint .work/asl/static-train-v2/best.pth
python3 -B -m tools.asl_live_firmware --export .work/asl/static-export-v1 \
  --experimental --layout hp-only-large --jobs 1
python3 -B -m tools.prepare_asl_live prepare --setools-root <installed-SETOOLS> \
  --export .work/asl/static-export-v1 --experimental --hp-only-board
python3 -B -m tools.program_asl_live review
```

### Training and validation

- Inventory all 75,000 allowed images, decode/hash each, split filename blocks
  together and quarantine near-duplicate evaluation images. Filename blocks
  are **not verified signer/session identities**; dHash is heuristic.
- Defaults: 600 Kaggle training images/class plus an augmented view; up to 100
  evaluation images/class. All 61 reviewed camera TRAIN images plus three mild
  augmented views; no horizontal flips. Existing camera audit is unchanged.
- Copy preserved classifier rows **by name**, initialize new rows, freeze
  MobileNetV2 backbone and BatchNorm. Fit the head only, class-balanced within
  each domain, 50% camera / 50% Kaggle sampling mass.
- Select on mean class-balanced camera/Kaggle development-validation loss.
  Keep domain/per-class metrics separate. Dataset test is evaluated once after
  selection; no untouched final camera test is evaluated.
- Two development mixes were compared. The selected v2 mix uses 25% camera
  sampling and 16,384 samples/epoch, rather than v1's 50% / 8,192. Both use 40
  epochs; best epoch is selected on development loss, not the dataset test.
- TRAIN-only PT2E calibration: every camera training image plus ten selected
  Kaggle training images/class. Require all 26 classes represented. Compare
  float and PT2E separately on camera and Kaggle development data. Failed
  nonregression gates stay recorded, not silently excused.

### Contract and firmware safeguards

- `tools/asl_contract.py` owns exact legacy-7 and static-26 orders. Generated
  and checked-in C++ headers, reference, PTE inspector, UART banner and observer
  must agree. Wrong order/count fails closed.
- Keep all measured backing allocations: 1,000 outputs and 224x224 inputs,
  zero-padded reference assets; use only 26 logits / 128x128 inputs. No arena,
  method, temporary, camera or LCD reservation is reduced.
- Three boot repeats: finite logits, unchanged input, IRQ/job completion,
  stable allocator high-water marks, identical top-1 and 0.80 display decision,
  cosine >=0.999 and max error <=8 times the **new output scale**, extracted
  from graph and PTE. This retains the previously approved **post-hoc** gate;
  it is not a pre-registered accuracy result and is not widened again.
- Keep MRAM GOT, interrupt-driven NPU IRQ 366, CPU RGB565 rendering, whole-LCD
  180-degree rotation and **unrotated** camera/ML input. No live boot frame export.
- Threshold **0.80**, uncalibrated. Require three consecutive matching
  predictions; clear on a different/negative observation or 1,000ms staleness.
  NO_HAND/OTHER/uncertainty -> `--`. Clean letter panel without a footer; no cycling.
- Boot failure attempts `SELFTEST FAIL <code>` in a known-inactive LCD buffer.
  An ambiguous display flip cannot safely be redrawn. Startup stays blocked.

## Deliberate install and acceptance

Only flash after package review. Training/build/preparation/review/observer never
write hardware. HP-only board; preserve private SETOOLS material under `.work/`.

1. Power off, SW4 -> SE, reconnect:

   ```bash
   python3 -B -m tools.program_asl_live program --port /dev/ttyACM0 --write
   ```

   Type exactly `PROGRAM E8-HP EXPERIMENTAL LIVE ASL`. After writer success,
   power off, SW4 -> U4, reconnect.

2. Arm the read-only observer, then RESET once:

   ```bash
   .work/asl/venv/bin/python -B -m tools.observe_asl_live \
     --port /dev/ttyACM0 --seconds 30 --export .work/asl/static-export-v1 \
     --output .work/inference/asl-live-acceptance/static-boot01.log
   ```

   Fresh log name each time. Require exact model/input/class/gate identity,
   78 ordered logit records (26 per repeat), `ASL SELFTEST PASS checked=26
   repeats=3`, `LIVE ASL START`, and increasing frames/predictions with `fault=0`.
   Keep failure logs; never widen limits to force a pass.

3. Check upright preview/panel. Sign A/N/G/E/L/A separately, then the other
   letters, empty scene and unsupported poses. Record errors and suppressions;
   don't quietly disable weak classes. No word completion or ANGELA shortcut.
4. Repeat from cold power-on. Passing numerical self-test is not gesture
   accuracy. New-letter camera training/validation and an untouched final
   session remain future work.

Results and build identity: [static alphabet audit](../../docs/asl-static-alphabet.md).
The v7 image is built and packaged: **3,480,640 bytes**, **381 host tests pass**,
offline package/layout/ABI/embedded-byte checks pass. Nothing was flashed.
**Both quantization nonregression gates failed**: camera development PT2E gives
11/30 correct letter displays, two wrong, 0/12 negative false letters; A 0/6.
No static-26 hardware run or new-letter camera accuracy is claimed. This is an
expanded-alphabet trial, not a demonstrated accuracy improvement over v6.
