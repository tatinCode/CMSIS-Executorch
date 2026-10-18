# Continuous 24-letter data collector

Separate HP-only image: **COLLECT / NOT ASL**. It temporarily replaces live
recognition; the v7 live package and all earlier capture/TEST UI packages remain
unchanged. No automatic flashing, model training, or on-device learning.

**Offline build/package ready:** 429 tests pass, caption checks pass ASan/UBSan,
and layout/ABI/embedded-reference/package reviews pass. Binary **4,726,528 bytes**,
MRAM headroom **512,256 bytes**; measured reservations unchanged. Separate stage:
`camera-collect-programming-v1/camera-inference-nfdtti7q`. All **6,779 protected
source/artifact files** remain unchanged, and live v7/original capture reviews
still pass. Nothing flashed or captured on hardware by the agent. Integrity
evidence: `.work/inference/continuous-collector-integrity-v1.json`.

## What it does

- Keeps the original ImageNet stored-image self-test and IRQ 366 completion.
  Collection starts only after `COEXIST SELFTEST PASS checked=1000` and display
  scanout checks. Camera preview remains paused during that self-test.
- Repeats **2.5 seconds of moving preview**, then freezes the completed frame
  while transmitting its CRC-checked full-view 128x128 planar RGB pixels.
  Wire time is about **4.3 seconds at 115200 baud**; measured board speed is
  still pending. This is not a video-rate recorder.
- Uses the unchanged v4 resizer/exporter and all measured camera/LCD/NPU pools.
  No extra frame-sized buffers, graphics heap, UART RX or host requests.
- Labels and signer IDs live on the host, not on the board. The LCD shows a
  collection sequence number, **not a predicted sign**. Whole LCD rotated 180
  degrees; saved camera pixels stay unrotated and unchanged.
- Per-cycle wrapper `HGCONT BEGIN 5 <cycle>` / `HGCONT END 5 <cycle> <frame>`
  surrounds the existing `HGFRAME 2` packet. Sequence/frame/CRC errors stop the
  host session; no silent resync or relabeling after a reset/disconnect.
- Receiver drains continuously while the user answers prompts. Every label arm
  discards a complete cycle, then snapshots the label at a subsequent BEGIN.
  No queued frame is reassigned to a new label. This guard does not prove exact
  camera-to-host timing under arbitrary buffering; visual review is mandatory.

## 1. Deliberately install the prepared collector

First close any serial monitor/observer. Power off, **SW4 -> SE**, reconnect:

```bash
python3 -B -m tools.program_camera_collect review
python3 -B -m tools.program_camera_collect program --port /dev/ttyACM0 --write
```

Type exactly **`PROGRAM E8-HP CONTINUOUS COLLECTOR`**. After writer success,
power off, **SW4 -> U4**, reconnect. Do not change switches while powered.

## 2. First test: three images of A, without repeated resets

```bash
.work/asl/venv/bin/python -B -m tools.collect_camera \
  --port /dev/ttyACM0 --person p01 --session pilot01 --hand right \
  --labels A --per-label 3
```

1. Wait for **READ ONLY**, then RESET **once**. The host verifies the boot
   profile/self-test and keeps draining frames while you position the hand.
2. At the A prompt, form A and press Enter. A whole cycle is discarded.
3. Hold the sign until each `CRC OK` message. Change position/distance modestly
   during moving preview, then hold steady. The frozen LCD image is the camera
   frame being exported; inspect that image, not just the text label.
4. The command stops after three saved images. No further reset is required
   between images/labels within the same invocation.

Expected files: `.work/asl/continuous-camera/p01/pilot01/A/`, with PNG, original
planar `.rgb`, protocol `.log`, and metadata `.json` for each image. Metadata
records person/session/hand, connection/cycle/frame, CRC and raw/PNG SHA-256.
`session.json` is immutable; completion/interruption summaries record saved
paths. Ctrl-C retains completed groups; use a **new session name** to resume.

An unexpected reset, lost byte, UART timeout or CRC failure ends the session.
Do not use incomplete groups or simply rename a folder to fix labels.

## 3. Review the pilot, then collect the alphabet

```bash
.work/asl/venv/bin/python -B -m ml.asl.collection_data \
  --output .work/asl/collection-review-pilot01
```

Open the JPEGs under `collection-review-pilot01/review/`. Check the handshape,
upright image orientation, colors, whole hand visibility, focus and person ID.
The auditor rechecks CRC, hashes, protocol logs, frame order and every PNG/raw
pixel. It writes a **pending** review template; it never auto-approves labels.
The original image files are not changed. A fresh output directory is required
for each audit.

For all supported letters and negatives (default label order):

```bash
.work/asl/venv/bin/python -B -m tools.collect_camera \
  --port /dev/ttyACM0 --person p01 --session day01 --hand right --per-label 5
```

Supported labels: A B C D E F G H I K L M N O P Q R S T U V W X Y NO_HAND OTHER.
No J, Z, delete, or space labels. NO_HAND means an empty scene. OTHER means
unsupported/partial/transitional poses: **do not label valid letters as OTHER**.
To collect only ANGELA's unique letters, use `--labels A N G E L`.

Five images for each of 26 labels is a **small pilot**, roughly 18 minutes plus
prompts/boot. It is not enough to claim generalization. Start with manageable
sessions and review quality before collecting thousands of near-duplicates.

## People, sessions and splitting

- Use pseudonyms (`p01`, `p02`), not names; keep the same person ID across all
  sessions of that signer. Use a new session for different lighting or hand.
- Include multiple people, realistic backgrounds, distances and orientations.
  Record real left/right hands deliberately if both should work; do not assume
  indiscriminate mirroring is label-preserving.
- Keep validation/test **people** out of training if testing unseen hands.
  All sessions of a person stay in the same split. With five people, one possible
  allocation is three train, one development validation, one untouched test:

  ```bash
  .work/asl/venv/bin/python -B -m ml.asl.collection_data \
    --output .work/asl/collection-review-multisigner01 \
    --train-person p01 p02 p03 --val-person p04 --test-person p05
  ```

  Assign every captured person exactly once, or omit assignments for inspection
  only. The audit quarantines conflicting exact labels and cross-split dHash
  near-duplicates (heuristic, not proof of independence). It reports zero-support
  classes rather than silently pretending every letter has validation data.
- Do not tune using the test person's images. New data remains **unassigned and
  label-unreviewed** at capture time. Audit outputs are **not** drop-in inputs for
  the old seven-class camera trainer. Review/integration, retraining, int8 export,
  and a deliberate recognition-firmware reflash are separate follow-up steps.

## Return to live recognition

Close collection/serial tools. Power off, SW4 -> SE, reconnect:

```bash
python3 -B -m tools.program_asl_live program --port /dev/ttyACM0 --write
```

Confirm `PROGRAM E8-HP EXPERIMENTAL LIVE ASL`, then power off, SW4 -> U4,
reconnect. The separate live package pointer was not changed by collection.
Repeat its self-test/live/cold-boot acceptance; collecting images alone does not
change the installed model's weights or accuracy.

## Reproduce offline

Existing output directories are retained, never rebuilt in place. Existing
isolated environments and private installed SETOOLS only; one build job:

```bash
python3 -B -m tools.camera_collect_firmware --layout hp-only-large --jobs 1
python3 -B -m tools.prepare_camera_collect prepare \
  --setools-root <installed-SETOOLS> --hp-only-board
```

Artifacts: `.work/inference/camera-collect-firmware-v1/` and separate
`.work/inference/camera-collect-programming-v1/`. Read-only reviews revalidate
source/tool/SDK identities, ABI, compiled source selection, generated transforms,
layout/vectors/GOT and embedded reference bytes, then map/TOC/CRC/private package
identities. **Hardware capture throughput, label/image correspondence, recovery
and cold boot still require user verification.**
