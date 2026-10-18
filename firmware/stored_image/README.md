# E8-HP stored-image MobileNetV2 target

**Standalone hardware milestone accepted.** This separate CMSIS project runs a preprocessed stored image through the ImageNet MobileNetV2 export on U85. User-supplied UART logs show PASS on reset and a cold-boot repeat: 19 ms, all 1,000 logits checked, one completed NPU job, no errors/timeouts. See the [acceptance record](../../docs/stored-image-hardware-acceptance.md). It is not an ASL classifier and does not initialise the camera/display. It leaves the smoke and Viewfinder projects unchanged and reuses the tested smoke UART/NPU/cache platform source without editing it.

## Deliberately opt in to the larger layout

The target requires the audited **`hp-only-large`** profile:

- `[0x80000000, 0x80001000)`: first 4 KiB reserved, no image writes.
- `[0x80001000, 0x80500000)`: standalone HP image, 5,238,784 bytes.
- `[0x80500000, 0x80580000)`: reserved user/device/boot-table tail.
- No HE application allocation; no system-package changes.

This profile **reclaims default HE application space and part of the user area**. Selecting/building it is not permission to overwrite that range. Before any later programming action, confirm no HE/A32 firmware or other data there must be preserved. See the [layout audit](../../docs/hp-mram-layout-audit.md).

## Export and build

From the HandGPT root, with the pinned host environment and embedded runtime already installed:

```bash
# Explicit regeneration is needed once for older exports without hash bindings.
# This does NOT rebuild or overwrite the smoke model.
python3 tools/inference.py export --model mv2 --image ml/reference/images/cat.jpg --jobs 1

# No device access. Add --rebuild for a clean firmware build.
python3 -m tools.stored_image_firmware --layout hp-only-large --image ml/reference/images/cat.jpg --jobs 1
```

The export was regenerated and the clean target build exercised during implementation. Its PTE hash remains `5a856b85afbd490654f93982484190b7e18974f1ae5511975c332d80aeb3c033`; new reference/metadata bindings are now present. You do not need to export again merely to repeat the firmware build with the same artifacts.

VS Code, opened at the HandGPT root, also offers **HandGPT: Build stored image (HP-only layout, no device access)**. It has no automatic export or programming dependency. The existing smoke programming tasks remain smoke-only and reject this image.

The builder owns `.work/inference/stored-image-firmware/`; all RTE files, assets, CMSIS JSON-form YAML, and linker copies are generated there. Edit checked-in sources/generator instead of generated files. Output paths:

```text
.work/inference/stored-image-firmware/stored-image.csolution.yml
.work/inference/stored-image-firmware/out/stored-image/DevKit-E8-HP/debug/stored-image.elf
.work/inference/stored-image-firmware/out/stored-image/DevKit-E8-HP/debug/stored-image.bin
.work/inference/stored-image-firmware/stored-image.map
.work/inference/stored-image-firmware/preflight.json
.work/inference/stored-image-firmware/manifest.json
```

Success prints `STORED-IMAGE BUILD/LAYOUT CHECKS PASSED`. A failed build invalidates the success manifest. `hardware_verified` and `programming_authorized` remain **false**.

## What is checked

- Fresh PTE inspection, exact preprocessing/reference hashes, pinned runtime/driver archive identities, matching headers/compiler, and hard-float/small-enum ABI.
- Default preflight remains limited to the old 2 MiB profile. This builder explicitly selects `hp-only-large`; there is no arbitrary capacity override.
- HP base/size, MRAM user boundary/size, HE allocation, stack/heap, and MPU lower bound are changed only in this target's generated configuration. `APP_MRAM_HE_BASE` stays `0x80000000` because the SDK MPU uses it as the read-only MRAM lower bound.
- Actual ELF/bin extents: embedded PTE/input/golden logits in MRAM, no object overlaps, complete binary below `0x80500000`, correct vectors/reset/MSP/IRQ 366, bounded DTCM method pool/heap/stack, SRAM0 I/O/planned/temporary pools, and correct startup copy/zero table coverage.
- Source, generator, generated project/configuration/assets, model/reference, runtime, and output identities are recorded in the manifest.

The implementation clean build uses approximately **4.65 MB of the 5,238,784-byte image region**, leaving roughly **585 KB** before the tail. Use the generated manifest for exact sizes/hashes; documentation numbers are not acceptance criteria for later builds. The SDK's top-of-DTCM stack reservation still makes the linker's region summary show 100% DTCM; the manifest reports the actual heap-to-stack gap.

## Runtime memory and diagnostics

Reservations are explicit and bounded:

| Allocation | Bytes | Placement |
|---|---:|---|
| Planned arena | 752,640 | SRAM0 |
| Temporary allocator, including actual NPU scratch | 1,572,864 | SRAM0 |
| Input copy | 602,112 | SRAM0 |
| Preserved output logits | 4,000 | SRAM0 |
| Method allocator | 262,144 | DTCM |
| Newlib heap | 131,072 | DTCM |
| Stack | 32,768 | DTCM |

The backend requests 1,505,280 scratch bytes from the **temporary allocator**, not the planned arena. Temporary/method allocations are padded/aligned to at least 32-byte cache lines; this prevents NPU cache invalidation touching a neighbouring CPU temporary allocation. Runtime logs report both allocator high-water marks, retaining the maximum across resets of the temporary allocator. The user observed temporary high-water **1,505,280 bytes**, method high-water **864 bytes**, and method execution **19 ms** on both reported hardware runs. These do not justify automatically shrinking reservations for another target.

The input is copied from MRAM before inference and checked unchanged afterward. `g_image_output[1000]` retains the board logits for debugger inspection if a debugger becomes available. The shared platform record is still named **`g_smoke_status`**, with its existing 60-byte ABI, but belongs to this new image when it is running; its address is in the stored-image manifest. Do not use a symbol/address from an older ELF to decode it.

Logs identify the image/layout/model/input hashes, memory pointers, NPU initialization, method-load result, allocator use, execute status, comparison statistics, and board top-5 indices/logits. Human-readable names are provided for indices appearing in the host top-5; other results are explicitly labelled as not in that set. The host's top label for this image is **carton**, not an assertion that the photo lacks a cat.

## Numerical acceptance is explicit, not a production accuracy claim

`IMAGE PASS` requires:

- Successful runtime execution and nonzero NPU job/IRQ evidence, with completed jobs matching started jobs.
- No semaphore/UART timeout, unchanged input, and 1,000 finite output logits with the expected tensor contract.
- All 1,000 logits compared against the host float32 reference; **maximum absolute error <= 1.0**, **cosine similarity >= 0.98**, and **the same top-1 class index** by default.

These are predeclared, configurable **bring-up gates**, not calibrated error bounds or ASL accuracy targets. The host model is float32 while the NPU export is quantized using one image. Legitimate quantization differences can fail the gates; inspect outputs/calibration rather than automatically relaxing thresholds or calling successful execution a numerical PASS. Defaults are printed at startup and recorded before programming; no automatic tolerance adjustment exists.

Override thresholds deliberately at build time with `--max-logit-error` and `--min-cosine`. Add `--dump-logits` to print all 1,000 board/reference pairs as `LOGIT index actual reference` in addition to the top-5/summary. A normal failure reports `IMAGE FAIL error=...`; errors retain the [smoke target's numbering](../smoke/README.md). Error 23 is a numerical/top-1 mismatch, not necessarily an NPU hardware failure. Method execution timing includes runtime/CPU work and cache maintenance and is only millisecond resolution, not an NPU cycle benchmark.

## Prepare and review without programming

The user has confirmed this board is dedicated to **HandGPT, HP only**, with no HE/A32 application or reclaimed MRAM data to preserve. This resolves the preservation question, **not write authorization**. For this board only:

```bash
python3 -m tools.prepare_stored_image prepare \
  --setools-root /home/tatin/alif/app-release-exec-linux \
  --image ml/reference/images/cat.jpg --hp-only-board

python3 -m tools.prepare_stored_image review
```

The helper has **only `prepare` and `review` commands**: no `program`, `--write`, serial-port selection, device enquiry, or erase operation. It uses a separate mode-0700 `.work/inference/stored-image-programming/` directory and its own `latest.json`; the verified smoke package/pointer remain untouched. The original installed toolkit is not executed or modified. Private signing material is copied into the ignored workspace; do not commit/share that directory, receipts, toolkit copies, or full toolkit logs.

Preparation/review recheck the actual ELF/bin vectors/startup/pools, source/config/runtime identities, fresh model/reference preflight, and exact embedded model/input/golden bytes. A private SETOOLS 1.112.00 copy generates the new configuration and package using **only `app-gen-toc`**. Review checks its complete file inventory/hashes, sole unsigned HP image placement, every contiguous device/certificate/ATOC map extent inside the reserved tail, binary TOC device/CPU/flags/object entries, and TOC CRC. These are integrity/layout checks, not independent cryptographic certificate verification or hardware acceptance.

The prepared implementation package contains:

| Object | Range (end exclusive) | Bytes |
|---|---|---:|
| HP firmware/model/input image | `[0x80001000, 0x804711d0)` | 4,653,520 |
| DEVICE/certificates/ATOC | `[0x8057f0d0, 0x80580000)` | 3,888 |

The only boot entries are **DEVICE + USER_APP**, with USER_APP selecting **M55_HP**, `flags: ["boot"]`, unsigned XIP, and `mramAddress: "0x80001000"`. The baseline DEVICE configuration is retained. Both ranges avoid the reserved first 4 KiB and system-package area; all device/certificate/ATOC objects fit in the reserved final 512 KiB. Later builds/packages must pass fresh checks, not rely on these recorded numbers. The receipt explicitly records `hp_only_board_confirmed: true`, `programming_authorized: false`, and `hardware_programmed: false`.

## Deliberate programming — separate, confirmation-gated command

[`tools/program_stored_image.py`](../../tools/program_stored_image.py) is the separate stored-image programmer. The preparation helper remains host-only. The implementation was tested with mocked writes and a real review-only run; the user subsequently supplied the standalone board PASS/cold-boot logs recorded above. **The assistant has not flashed the board.** Neither implementation nor prior acceptance authorizes an automatic future write.

Do not copy this binary into the old smoke/Viewfinder task, patch the smoke programmer's guards, or run a manual erase to make it fit. There is no new VS Code writing task: run the explicit command in an interactive terminal. A write replaces the on-board smoke/camera application and much of the default HE range. Source/build/package fallbacks remain separate; restoring one requires programming its matching image **and** boot table. The reserved first 4 KiB and system-package area are outside the checked write ranges.

First review the package and proposed command. This does not need the board connected and never opens the port:

```bash
python3 -m tools.program_stored_image program --port /dev/ttyACM0
```

For an intentionally authorized board write:

1. Close picocom and all other serial monitors/debug sessions. Verify this is the intended E8 Rev A0 dedicated to HandGPT **HP only**.
2. Disconnect board power/USB **before** changing SW4 to **SE**, then reconnect. Check that `/dev/ttyACM0` is still the intended board's serial port; no port is auto-selected.
3. Run the following **device-writing** command yourself:

   ```bash
   python3 -m tools.program_stored_image program --port /dev/ttyACM0 --write
   ```

4. Read the displayed image SHA-256, receipt, and image/boot-package ranges. Only when ready, type exactly:

   ```text
   PROGRAM E8-HP STORED IMAGE
   ```

The writer requires **all three**: explicit `--write`, an interactive terminal, and that typed confirmation. Piped/redirected confirmations are rejected. It verifies the full build/model/reference/package/tool identities before confirmation and again afterward. Changed receipts/packages/builds or a changed/disappearing serial-device identity abort without launching the writer. Accepted port names are explicit `/dev/ttyACM<N>`, `/dev/ttyUSB<N>`, or `/dev/serial/by-id/...` / `by-path/...` aliases resolving to one of those character devices. These metadata checks cannot identify a board's serial protocol or replace the user's physical-board check; do not unplug/swap boards during confirmation.

Only the staged, hash-checked `app-write-mram -p -c <port>` runs in the reviewed toolkit directory. No discovery, erase, skip-image, no-reset, provisioning, debug-stub, or system-package-update options are supplied. `-p` is SETOOLS' 16-byte binary padding option; both accepted artifacts already satisfy that alignment. SETOOLS normally resets the board into maintenance mode before writing and resets it after completion. **Additional toolkit prompts cannot be answered**: child stdin is disabled, so a part/revision mismatch must be investigated, not overridden. If SETOOLS needs another prompt, treat that attempt as failed rather than weakening the guard.

Each confirmed attempt gets its own private `program-*/program.log` and `attempt.json` under the prepared package directory. The attempt records the package identity, port, command, and tool outcome; it does not alter the preparation receipt or mark hardware inference verified. A failed/interrupted write may leave partial MRAM contents. There are no wrapper retries or extra reset/erase/recovery actions. Inspect the log before deciding on a separate deliberate retry; do not assume a failure preserved the previous application. An attempt interrupted by a crash/power loss may remain `writer_requested`, which is not success.

After successful tool exit and log inspection: close SETOOLS, disconnect power before selecting SW4 **U4**, reconnect, and open the application UART:

```bash
picocom --baud 115200 --databits 8 --parity n --stopbits 1 --flow n --imap lfcrlf /dev/ttyACM0
```

Reset to capture the complete startup and `IMAGE PASS`/`FAIL` output. Exit picocom with **Ctrl+A, Ctrl+X**, repeat from a full power cycle, and retain both logs. A successful programming-tool exit is **not** successful inference. The screen is expected to stay black: this target initializes no camera/display.
