# Camera overlay: TEST UI build, not live ASL

This separate target exercises large **A/B/C/L/Y/`--`** glyphs over the existing
camera preview. The screen says **TEST UI / NOT LIVE ASL** and cycles symbols
every 1,500 ms, independently of the camera image. It must not be presented as
gesture recognition.

**Current revision: whole LCD rotated 180°.** The presenter confirmed that both
camera preview and text were upside down on the board, but the saved computer
PNG was already upright. Therefore rotate the **complete composed LCD**, not the
camera source or exported frame. Captures, existing training images and ML
preprocessing stay unchanged; no dataset rotation or retraining is needed for
this display-only fix. The user has now installed it and confirmed the screen
is upright; a fresh recorded capture/self-test and cold-boot acceptance remain
separate checks. This confirmation does not imply live ASL recognition.

```bash
python3 -B -m tools.camera_overlay_firmware \
  --layout hp-only-large --image ml/reference/images/cat.jpg --jobs 1
```

The helper refuses an existing `.work/inference/camera-overlay-screen-firmware/`
workspace or more than one build job. It reuses the current camera build logic
in a private module instance and generates private `main.cpp` / `preview.cpp`
variants there. The baseline camera sources, generator, v3/v4 builds, packages
and installed SETOOLS are not modified.

## Rendering and ownership

- Render only into the **inactive** existing RGB565 LCD buffer, after camera
  conversion and before its cache clean and VSYNC flip.
- Compose the original 480×480 camera view and draw text only in the logical
  bottom 480×320 band. Then reverse all 480×800 RGB565 pixels in place, exactly
  once, before cache clean / VSYNC. This turns both regions and swaps their
  scanout positions: text rows 0..319, camera rows 320..799. Do not also rotate
  the preview conversion, which would rotate the camera twice.
- Keep the full camera-frame ownership sequence, two-scanline flip wait, failure
  checks, stored-image ImageNet self-test, v4 frame export and MRAM GOT unchanged.
- No graphics heap, new video buffer, serial commands or changed reservations.
  The rotation uses one temporary 16-bit pixel and never modifies camera RGB.
- The build still embeds the original ImageNet self-test, **not an ASL model**.

`include/asl_display.h` also supplies tested, not-yet-integrated helpers for future
live output:

- Seven float-logit order: A/B/C/L/Y/NO_HAND/OTHER. Stable softmax, provisional
  0.80 threshold; negative classes, nonfinite inputs and wrong sizes yield `--`.
  Quantized logits must first be dequantized using their actual output contract.
- Three consecutive matching classifications before displaying a letter.
  A negative or different letter clears immediately. Input older than 1,000 ms
  expires, including across the 32-bit millisecond wrap.
- Future callers must call `current(now)` on every display update and submit a
  negative observation on inference failure. Repeated reads of one classification
  must not count as new observations. A stalled application/display cannot clear
  a hardware screen by itself; these helpers do not provide that guarantee.

The TEST UI deliberately draws its sequence directly. It does not exercise
real inference, confidence gating or temporal stabilization on the board.

## Verified host artifacts

The display-orientation revision's single-job build and layout checks passed:

- Manifest: `.work/inference/camera-overlay-screen-firmware/manifest.json`
- Binary: **4,726,624 bytes**, SHA-256
  `36187070d3ce47626a010d7320e0ab8829239d2751f0522a123c0ee08e129e57`
- MRAM: `[0x80001000, 0x80482f60)`, **512,160 bytes** headroom within the
  reviewed firmware region.
- SRAM1 camera/LCD reservations: **2,227,200 bytes**, unchanged. Stored-image
  NPU allocations and vector/startup checks also remain unchanged.
- Host-rendered scanout contact sheet:
  `.work/inference/camera-overlay-screen-review-v1/contact-sheet.png`.
  This is **not a board screenshot**.

Tests cover all glyphs, bounds, old-glyph erasure, exact whole-screen reversal,
decision/rejection logic, temporal clearing/expiry, and the generated preview and
main variants' normal/error paths with mocked drivers. The real E8 build verifies
the existing camera/NPU vectors, startup tables, asset bytes and memory layout.
The mocked preview checks asymmetric camera pixels and the entire text region
against an independently reversed composed frame across repeated buffer flips.
The package validator also requires the original, unmodified v4 frame exporter.
None of this substitutes for physical screen/camera acceptance.

## Prepared package — user-triggered installation

The separate TEST UI package is now prepared and reviewed. **Nothing has been
flashed by the agent.** Its pointer is `.work/inference/camera-overlay-screen-programming/latest.json`;
the v4 capture pointer remains untouched. The overlay's distinct package target
is `camera-overlay-screen-test-ui.debug+DevKit-E8-HP`, and its image name is
`handgpt-camera-overlay-screen-test-ui.bin`. The compiled CMSIS project retains the
`camera-inference` name; the overlay validator requires its generated UI sources,
exact transformations, hashes and compilation entries before accepting it.

Package tools reuse private instances of the established package/TOC and writer
checks. The original capture helpers and selection globals are not modified.
Review binds the overlay and base helper identities, sources, SDK/runtime,
generated files, ELF/binary, memory/vectors, exact self-test assets, staged tools,
DEVICE configuration, package map and binary TOC/CRC. Capture/overlay receipts
and image/certificate names cannot be interchanged.

Revalidate locally without opening a serial port:

```bash
python3 -B -m tools.program_camera_overlay review
```

To regenerate a private package if needed (still no device access):

```bash
python3 -B -m tools.prepare_camera_overlay prepare \
  --setools-root /home/tatin/alif/app-release-exec-linux \
  --image ml/reference/images/cat.jpg --hp-only-board
```

The current prepared stage is `camera-inference-8gqqei7o` under the separate screen-overlay
programming directory. It retains the build SHA-256 above and programs only:

- HP application `[0x80001000, 0x80482f60)` — 4,726,624 bytes.
- DEVICE/certificates/ATOC `[0x8057f0d0, 0x80580000)` — 3,888 bytes.
- Boot entries DEVICE + USER_APP, **M55_HP only**.

Preparation verification: **309 tests passed**; original ML/capture sources and
data are unchanged, and the existing v4 programming package passed a fresh
review. The overlay stage is mode 0700; its preparation log is 0600. No serial
port was opened and no programming attempt exists for this revised package.
Audit: `.work/inference/camera-overlay-screen-integrity-v1.json`.

The first TEST UI binary (`d70fd4d0…`) and package remain retained. The user
installed that version and confirmed moving preview, glyph cycling and recovery,
but reported inverted screen orientation; cold-power-cycle acceptance was not
recorded. An intermediate, unflashed v5 capture-rotation prototype is superseded:
**do not use it**. The current entry point selects only the screen-rotation
package above, whose boot export remains v4.

### Deliberate flash and screen check

1. Close all serial monitors/listeners. Disconnect board power/USB **before**
   changing SW4 to **SE**, then reconnect. Confirm `/dev/ttyACM0` is this board.
2. Run the following yourself in an interactive terminal:

   ```bash
   python3 -B -m tools.program_camera_overlay program --port /dev/ttyACM0 --write
   ```

   Review the displayed TEST UI identity, then type exactly
   **`PROGRAM E8-HP TEST UI`**. The helper revalidates the build/package and serial
   device identity after confirmation. Without `--write` it only reviews; piped
   confirmations and other targets' phrases are rejected. No full erase,
   provisioning, system-package update or automatic retry is requested.
3. After a successful write, disconnect power before switching SW4 to **U4**;
   reconnect. Expect the moving camera view above **TEST UI / NOT LIVE ASL** and
   the sequence `--`, A, B, C, L, Y. During the one-time boot export, the image and
   glyph temporarily freeze; both must resume afterward. Gestures do not control
   this sequence. The first visible symbol depends on boot timing.
4. Record a CRC-verified reset capture and the boot self-test log, outside the ML
   training/development directories. With no hand in view:

    ```bash
    .work/asl/venv/bin/python -B -m tools.capture_camera \
      --port /dev/ttyACM0 --session overlay-screen01 --label NO_HAND \
      --output .work/inference/camera-overlay-screen-acceptance/captures
   ```

   **Enter → LISTENING → board RESET.** Check the saved `.log` for
   `COEXIST SELFTEST PASS`, the saved PNG for normal camera colors/orientation,
    and the panel for upright text and all six readable symbols without tearing/
    stale glyphs. Use a clearly oriented object (e.g. printed text) to confirm
    both the LCD view and the saved PNG are upright in your normal board position.
   The PNG is camera pixels only; it must not contain the LCD overlay.
5. Once capture completes and preview resumes, this read-only diagnostic can
   confirm continued `PREVIEW` progress without another reset:

   ```bash
   .work/asl/venv/bin/python -B -m tools.capture_camera \
     --port /dev/ttyACM0 --diagnose --seconds 10
   ```

   Record any `COEXIST FAIL`. Repeat visual acceptance from a full cold power-on.
   A reset test alone is not cold-boot acceptance. Share the capture/log path and
   what the screen showed; hardware verification remains pending until observed.

The old `tools.program_camera_inference` command still selects the preserved v4
capture-only package. That separately revalidated package is the fallback using
its own confirmation phrase. Never rename this binary into an existing package
or bypass source/receipt checks. Private SETOOLS stages contain signing material;
keep them under ignored `.work/` and do not publish their files or raw logs.

Live ASL additionally needs an adapted int8 `.pte`, calibration from training
only, quantized validation, delegation/memory checks, exact-input host/device
comparison, camera normalization, and actual inference-to-overlay wiring.
See [fine-tuning and overlay status](../../docs/asl-finetune-and-overlay.md).
