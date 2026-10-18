# HandGPT

**Offline ASL handshape recognition on the Alif Ensemble E8.**

HandGPT takes camera frames, runs a quantized MobileNetV2 through ExecuTorch on
the Cortex-M55 HP and Ethos-U85 NPU, and shows individual letter predictions on
the attached display. Inference runs entirely on the board, without internet.

The frozen experimental model has **24 static letters** (A–Y except J/Z), plus
`NO_HAND` and `OTHER`. Uncertain predictions display `--`. It does not assemble
words or translate continuous ASL. The current display has no EXPERIMENTAL footer;
the documented model limitations and boot checks still apply.

> **Best-effort prototype, not a reliable ASL translator.** Some letters remain
> weak: old camera development results include A 2/6, L 3/7 and Y 3/6 correct
> displays. The latest package passed offline review, but its reported flash
> attempt failed with “Target did not respond”; successful installation and
> live acceptance are not yet confirmed.

## Hardware

- Alif Ensemble E8 DevKit, Cortex-M55 **HP-only** configuration.
- MT9M114 camera and attached display.
- Linux host with the project's local tools and dependencies installed.

## Run the frozen demo

Run these commands from the repository root. They use the **already prepared
local package**; models, toolchains and build outputs under `.work/` are not
included in Git. For a fresh checkout, start with the
[setup and build guide](docs/stored-image-inference.md) and
[frozen-demo notes](docs/frozen-demo.md).

### 1. Review without accessing the board

```bash
python3 -B -m tools.demo_asl review
```

### 2. Flash deliberately

Close serial monitors and the collector. **Disconnect power/USB**, set
**SW4 to SE**, then reconnect. Check that `/dev/ttyACM0` is your board's port:

```bash
python3 -B -m tools.demo_asl program --port /dev/ttyACM0 --write
```

Type exactly **`PROGRAM E8-HP BEST EFFORT ASL`** when prompted. This replaces the
current HP application. Do not switch modes until programming succeeds.

If the target does not respond, stop, close competing serial tools, and check
power, the USB data connection, port and SE switch position. MRAM may be partially
written after a failed attempt; do not assume the old application is intact or
override device checks.

### 3. Start and verify

After successful programming: **disconnect power, set SW4 to U4, reconnect**.
Start the read-only observer:

```bash
.work/asl/venv/bin/python -B -m tools.observe_asl_live \
  --port /dev/ttyACM0 --seconds 30 \
  --export .work/asl/demo-export-v1 \
  --output .work/inference/asl-live-acceptance/demo-boot01.log
```

After **READ ONLY**, press RESET once. Require `ASL SELFTEST PASS`,
`LIVE ASL START`, increasing predictions and `fault=0`. Use a fresh output filename
for later runs. Numerical self-test success does **not** establish sign accuracy.

Show one steady sign at a time, keep the hand visible, and release between signs.
Rehearse the letters you intend to present and repeat from a cold boot.

## More information

- [Frozen model, known limitations and build commands](docs/frozen-demo.md)
- [Camera data collection](firmware/camera_collect/README.md)
- [Training and evaluation](ml/asl/README.md)
- [Live firmware and acceptance checks](firmware/asl_live/README.md)
- [Hardware baseline](docs/camera-baseline.md)

Training and export run on the host. Existing datasets, checkpoints and fallback
packages are kept separate; collecting images does not automatically retrain or
flash the board.
