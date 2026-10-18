# Capture real E8 frames for host ASL evaluation

## Current status

The user has captured CRC-verified **v3 480×480** frames and supplied a saved
startup log containing `COEXIST SELFTEST PASS checked=1000`. Host evaluation found
confident L→Y errors and false letters on unsupported poses. These are development
examples, not a held-out accuracy benchmark. No ASL model has been deployed.

The **v4 fast-capture candidate** exports the full 480×480 camera view resized
to **128×128**, not a center window. Its integer antialiased bilinear resizer
matches Pillow 12.1.0's RGB resize byte-for-byte in host tests. The actual C++
exporter was also checked on 16 completed v3 camera captures: resized RGB and
normalized model inputs were identical to the original host path, with zero
logit difference. Evidence: `.work/asl/fast-capture-validation.json`.

The 49,152-byte payload is 14.0625× smaller: approximately **4.3 seconds of
115200-8N1 wire time**, versus 60 seconds for v3. Boot, preview settling, CPU
resize, and transport overhead are additional. **V4 has not been tested on the
board; actual speed, colors and preview recovery still need hardware validation.**
Both versions export **once per boot**, after the stored-image self-test and
two seconds of live preview. The host sends no UART bytes; firmware enables no RX.

The user supplied a complete v2 startup log: `COEXIST SELFTEST PASS`,
`checked=1000`, one completed NPU job/IRQ, and preview progressing to frame 47.
The v1 receive path reported error 209; v2 capture attempts timed out, and a
later standalone read also saw no bytes. These observations do not establish
the precise cause of the silence. V3 removes RX as a dependency, not as a
proven root-cause fix. A full cold-boot acceptance log remains a separate gate.

Preserved v3 fallback image:

- SHA-256 `be159fda5810bf499255783bd1499d4f96c2bb9fa04e428f0f880a5e38c7a02e`
- 4,720,384 bytes, MRAM `[0x80001000, 0x80481700)`, 518,400 bytes headroom.
- SRAM0 inference reservations: 2,931,616 bytes, unchanged.
- SRAM1 camera/display buffers: 2,227,200 bytes, unchanged; **no extra frame buffer**.
- DEVICE/certificates/ATOC: `[0x8057f0d0, 0x80580000)`.
- The original `.work/inference/camera-inference-firmware/` build and
  `.work/inference/camera-inference-programming/` packages/pointer are retained.
  A frozen build/source/helper copy is also under
  `.work/inference/camera-inference-v3-fallback/`. Current source-bound helpers
  intentionally reject stale receipts; rollback requires deliberately restoring
  and validating the matching source/tool inputs, not bypassing the checks.

V4 builds/packages use **separate** `camera-inference-fast-firmware/` and
`camera-inference-fast-programming/` directories under `.work/inference/`.
See the [target README](../firmware/camera_inference/README.md) for the reviewed
candidate identity and build/prepare commands. You can continue using the updated
capture tool with the currently installed v3 firmware without flashing anything.

## 1. Program the fast revision deliberately (optional, separate action)

Only after the v4 candidate has been built, prepared and reviewed:

Close picocom and other serial monitors. Disconnect power before switching SW4
to **SE**, then reconnect. From the repository root:

```bash
python3 -m tools.program_camera_inference program --port /dev/ttyACM0 --write
```

Review the image identity and type `PROGRAM E8-HP CAMERA INFERENCE`. This is the
existing camera-target programmer and its normal explicit write confirmation;
it is not invoked by the capture tool.

After a successful write, disconnect power, switch SW4 to **U4**, and reconnect.
Every boot automatically freezes a frame for the export (about 5 seconds on v4,
about 60 seconds on v3, subject to hardware measurement),
even with no listener attached, then resumes preview. Close picocom before
running the capture tool. It must be listening **before** you press board RESET.

## 2. Capture one frame first

```bash
.work/asl/venv/bin/python -m tools.capture_camera \
  --port /dev/ttyACM0 --session plumbing01 --label A
```

Position your **A handshape** (a closed fist, thumb alongside), then press Enter
at the host prompt. Wait for **`LISTENING`**, then press the board's RESET button
while keeping the pose. Do not change SW4 or unplug USB with the listener open.
After boot/self-test, two seconds of live preview let you settle. Hold until
the screen freezes; you can then relax during the transfer.
Preview should resume afterward. Re-run the tool before resetting for each new
frame. Use this first `plumbing01` session as development data, not final eval.

The tool opens the port **read-only**, waits for `CAPTURE READY v=3` or `v=4`, and receives
one length-delimited binary frame. It sends no request, never programs firmware,
and makes no deliberate reset/modem-line toggle. OS/bridge behavior on open or
board reset remains hardware-dependent. It requires an explicit port, checks
existing users with `fuser`, and takes exclusive access. Readiness has a separate
30-second budget (`--ready-timeout`); transfer gets 100 seconds (`--timeout`).
Failures report the phase, received-byte count, and a bounded escaped UART tail,
including partial lines, rather than inferring silence from a generic timeout.

For a read-only diagnostic listen (no label/session or Pillow required):

```bash
python3 -m tools.capture_camera --port /dev/ttyACM0 --diagnose --seconds 20
```

Start it before pressing RESET. It escapes control/binary bytes, caps displayed
data, and reports observed versions and byte counts; it does not save an image.

Saved under `.work/asl/camera/plumbing01/A/`:

- `.rgb`: R plane then G plane then B plane; v3 is the original 691,200-byte
  allocation, v4 is the 49,152-byte resized image (not a full-resolution archive).
- `.png`: lossless interleaved RGB image, converted from those exact planes.
- `.json`: manually supplied label/session, dimensions, CRC32, raw/PNG SHA-256,
  capture time, serial-device metadata, wire/capture versions, source dimensions,
  crop and resize description. This is not a firmware readback hash.
- `.log`: application output seen while waiting for the export.

Incorrect dimensions/version, truncation, bad CRC, wrong trailer or timeout fail
the capture. No image is saved from such a failed packet. Metadata is written
last so incomplete disk writes are distinguishable. Do not interrupt a transfer;
if interrupted, allow it to finish, or rearm the listener before resetting.

Inspect the first PNG: confirm correct colors/orientation, complete fingers,
and agreement with the frozen LCD picture. The LCD's RGB565 image has lower color
precision; inference evaluation uses RGB888 bytes, not a screen photo. V4 keeps
the full view but cannot recover full-resolution detail for later reframing or a
larger-input model; use the preserved full-resolution path when that is required.

## 3. Collect a small diagnostic session

Repeat with labels `A`, `B`, `C`, `L`, `Y`, `NO_HAND`, and `UNKNOWN`.

- Start with one or two images per selected letter to find framing/domain errors.
- `NO_HAND`: empty scenes under different lighting/backgrounds.
- `UNKNOWN`: unsupported handshapes, partial hands and transitions—not an empty scene.
- Change pose/position/background rather than saving identical held poses.
- Use a different session ID for a different person or collection session.
- These initial diagnostic images become development data once inspected. Collect
  a separate later session for final evaluation; never randomly split adjacent
  camera frames between training and test.

This initial small set detects obvious failures; it is not an accuracy benchmark.

## 4. Run the existing host classifier

```bash
.work/asl/venv/bin/python -m ml.asl.evaluate \
  --folder .work/asl/camera/eval01 \
  --output .work/asl/camera-eval01-report.json
```

The image loader uses the checkpoint's existing centered-square, bilinear
128×128 RGB/ImageNet-normalization contract. Reports include every prediction,
confidence, per-class support/recall, confusion matrix, coverage and unsupported-
sign false-letter rate. The default 0.8 rejection threshold remains provisional.
Choose a new output filename for each run; reports are not overwritten.
`accuracy`, class recall and the confusion matrix describe **raw top-1**, before
thresholding; `coverage` and `accepted_accuracy` describe the thresholded subset.
A rejected known letter is not a successful letter recognition. For v4's
already-resized 128×128 PNG, the evaluator's resize is an identity operation.

Next collection/adaptation milestones: [presenter demo plan](presenter-demo-plan.md).

## Ownership, transport and checks

The main loop requests no new camera frame while exporting. It exports only
after a successful `preview_frame()`, whose full MI-frame detection, pipeline
stop and cache invalidation establish CPU ownership. LCD scanout uses separate
buffers. V4 computes resized output in a **1,024-byte stack scratch**, first for
CRC and then again for TX; the original camera allocation is never modified.
The fixed 4,608-byte filter table resides in MRAM (checked by the layout verifier).
There is no heap allocation or extra frame buffer. Each write is ≤1024 bytes
through the unchanged bounded TX implementation; SRAM reservations are unchanged.
RX/setup errors 209 and fatal export error 210 are removed in v3. Export failure
prints `CAPTURE EXPORT LOST` best-effort and resumes preview, without clearing
the recorded timeout count. Successful export prints `CAPTURE DONE v=4`.
`CAPTURE IDLE v=4` then repeats with preview progress (v3 uses `v=3`); another export requires
reset. Startup self-test/UART checks remain strict. The private generated port
keeps the original smoke TX callback/initialization; no UART RX is enabled.

V4 uses wire version 2:

```text
HGFRAME 2 128 128 RGB888P 49152 <frame_counter> <crc32_hex>\n
<exactly 49152 binary bytes>
\nHGFRAME END <frame_counter> <same_crc32_hex>\n
```

The receiver also accepts the original v3 packet (wire version 1):

```text
HGFRAME 1 480 480 RGB888P 691200 <frame_counter> <crc32_hex>\n
<exactly 691200 binary bytes>
\nHGFRAME END <frame_counter> <same_crc32_hex>\n
```

CRC32 uses the standard reflected polynomial `0xedb88320`, initial/final XOR
`0xffffffff`, matching Python `zlib.crc32`. It detects accidental transport
corruption, not image validity or authenticity. Binary payload must not pass
through picocom newline mappings or a text clipboard.
Only these two exact version/dimension/length combinations are accepted. Boot
v3 must carry wire v1; boot v4 must carry wire v2. A profile mismatch is rejected.

Host tests compare the actual C++ export byte stream against the Python parser,
check CRC/truncation/trailer failures, emulate fragmented UART delivery, assert
zero host writes and RX-free generation, exercise phase-aware timeouts and a
complete pseudo-terminal capture, and verify RGB plane order/lossless saving. These tests
and the build do not replace physical-board validation.
