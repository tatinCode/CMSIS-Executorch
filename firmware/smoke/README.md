# E8-HP standalone NPU smoke target

This target runs the deterministic `output = input + input` model on a fixed float32 `[1, 16]` input. It is **not** a handshape/image classifier and does not initialize the camera or display. It leaves the separate Viewfinder project unchanged.

## Build from the HandGPT root

First require successful smoke export and runtime packaging using the [inference workflow](../../docs/stored-image-inference.md). Then run:

```bash
python3 -m tools.smoke_firmware --jobs 1
```

For a full firmware rebuild:

```bash
python3 -m tools.smoke_firmware --jobs 1 --rebuild
```

The helper never connects to or programs a board. It validates the model/runtime manifests and driver headers, then creates the **separate CMSIS solution** in `.work/inference/firmware/`. All generated RTE configuration, project files, assets, linker script, maps, and binaries remain outside Git. Edit the checked-in sources/generator, not that generated workspace: the helper owns and regenerates its configuration.

The generated `smoke.csolution.yml` / `smoke.cproject.yml` are JSON-form YAML, accepted by CMSIS-Toolbox. Target: `smoke.debug+DevKit-E8-HP`, GCC 13.3.1, Ensemble 2.2.0, CMSIS 6.1.0. Compiler tools are discovered from `PATH`/Arm Environment Manager; pass `--cbuild /path/to/cbuild` or `--toolchain-bin /path/to/bin` if needed.

Outputs:

```text
.work/inference/firmware/out/smoke/DevKit-E8-HP/debug/smoke.elf
.work/inference/firmware/out/smoke/DevKit-E8-HP/debug/smoke.bin
.work/inference/firmware/smoke.map
.work/inference/firmware/manifest.json
```

A successful build prints `BUILD/LAYOUT CHECKS PASSED`. The manifest deliberately remains `hardware_verified: false`.

A clean host build has been exercised with the pinned compiler/runtime. The manifest records binary, source, generated configuration, SDK linker, and generator hashes plus the Viewfinder revision, so the tested artifact can be identified without committing build outputs. This is still not physical-board validation.

## Build-time checks

- Model/runtime use the same pinned ExecuTorch revision and U85-256 target.
- Model bytes, metadata, runtime archives, and driver headers agree with their recorded/source identities.
- Firmware and pinned driver/delegate ELF attributes agree on the **small-enum / hard-float** ARM ABI; the platform port also guards the driver enum widths at compile time.
- CPU code and the embedded model execute/reside in HP application MRAM, beginning at `0x80200000`; no enlargement of the existing 2 MiB application region is needed for this tiny model.
- Both the **4 KiB planned arena** and the **32 KiB temporary pool** are 32-byte-aligned, disjoint, and fully inside **SRAM0**, not CPU-local TCM. The pinned Ethos-U backend allocates its actual NPU scratch/input/output from the temporary allocator; checking only the planned arena is insufficient. The current model needs 96 planned bytes, which does **not** include the backend's scratch requirement. Both pools are checked in the ELF and again at startup, and their addresses/sizes are recorded in the manifest. The CPU-only 128 KiB method pool stays in DTCM.
- IRQ 366, SysTick, fault handler, UART write, and PAL timing implementations are strong symbols. The actual binary's IRQ 366 and SysTick vector entries point at the intended handlers.
- The model has 16-byte alignment. Heap and stack are non-overlapping inside DTCM, with explicit 128 KiB heap and 32 KiB stack reservations. `_sbrk` enforces the heap limit.

The Alif linker puts the stack at the top of DTCM, so the linker's summary reports **100% DTCM region usage** even though a large gap remains between the heap limit and stack limit. Use the checked manifest's `heap_to_stack_gap_bytes` and linker map rather than interpreting that percentage as actual live allocation.

The helper derives RTE/pin configuration from the pinned working Viewfinder baseline and patches a **local copy** of the SDK MRAM linker script. It preserves SDK vectors, constructors, copy/zero tables, moves general runtime code to MRAM to avoid ITCM overflow, and adds the SRAM0 section for both NPU pools. The SDK zero table covers that section at startup. Vendor copyright notices remain in the generated files; `Alif-License.txt` accompanies them. Alif's hardware restrictions still apply. No SDK, Viewfinder, or system-package files are edited.

## Runtime checks (not yet observed on hardware)

The firmware uses the E8 SDK example's U85 base/security settings and **IRQ 366**, without the old runner's duplicate polling paths. It uses the single matching driver packaged with the runtime. The actual interrupt routing, power state, memory/AXI configuration, and cache behavior still require physical-board validation; build success does not prove them.

The initial on-board run reached the delegate and received one NPU interrupt, but failed with `status=0x00001004` (SRAM-interface input-read bus abort) and `SMOKE FAIL error=19`. Inspection found that the original temporary pool was incorrectly in CPU-local DTCM even though the backend passes an allocation from it to the NPU. The corrected target places this pool in SRAM0 and logs `temporary_pool=... bytes=32768` before loading the method. A successful numerical result with the corrected firmware is still required; the memory-placement correction is not itself hardware acceptance.

- All 16 finite output values must match `2 × input` within absolute tolerance **0.04**. This tolerance allows int8 quantization error; it is not a classifier accuracy metric.
- The input must remain unchanged.
- At least one driver inference-begin callback and NPU IRQ must occur during execution; completed-job count must match started-job count.
- Semaphore waits are capped at **5 seconds**, assuming the configured SysTick interrupt is operating. UART transmission waits are capped at 1 second. These are not a hardware watchdog.
- UART writes wait for the CMSIS **SEND_COMPLETE callback**, not `GetTxCount()`. Alif counts bytes entering the transmit FIFO before clearing its busy flag; using that count caused subsequent writes to fail with `BUSY` and hid diagnostics. Completion is cleared before starting each transfer (including synchronous-completion safety); timed-out transfers are aborted before returning. ISR/interrupt-masked writes are rejected. A failed abort disables further console writes and powers the UART off.
- Driver cache hooks maintain aligned ranges; completion invalidation is restricted to the driver's NPU-owned scratch ranges, not all CPU tensors in the arena.

Success prints `SMOKE PASS ...`, checks 16 values, and blinks the green LED. Failure prints `SMOKE FAIL ...` when UART is usable and sets red. A CPU fault records `Fault` plus CFSR/HFSR and stops; early failures may precede usable LEDs/UART.

NPU initialization prints a starting marker, the driver's error return or a MAC-configuration mismatch, and a final `SMOKE FAIL error=2 ...` if it fails normally. The common result path includes `uart_timeouts` and makes one best-effort console-error recovery before reporting; a real UART failure is still returned as an error. Fault handlers do not attempt UART/stdio from interrupt context.

Millisecond inference timing can legitimately be zero for this tiny model. It is not a cycle-level benchmark. Do not substitute Vela's estimated performance numbers for hardware measurements.

## Output and diagnostic record

The application uses the HP board UART selection: on this configuration **UARTB / UART4**, **115200 baud, 8-N-1**, no flow control. Baseline pin configuration selects **P12_2 TX** and **P12_1 RX**. These are SoC signal names, not verified connector pin numbers or a wiring recommendation. Consult the board schematic and confirm voltage levels before attaching any adapter.

Application UART output has now been observed on `/dev/ttyACM0` with **SW4 set to U4**: resetting printed the smoke banner. That establishes startup/UART operation, **not inference success**. SW4 in **SE** routes the same USB serial port to SETOOLS instead. No RTT library is included, and no working J-Link core attachment is assumed. The screen will not show the camera preview while this standalone image runs.

The green indicator beside the on-board J-Link chip is its own debug indicator, **not** this application's PASS LED. The application controls the E8 RGB LEDs near/below-right of RESET. See the [DK-E8 User Guide, pages 7–8](https://alifsemi.com/download/AUGD0023) for SW4 routing and LED locations.

To capture logs, close the serial monitor and disconnect board power before selecting SW4 **U4**, then reconnect and run:

```bash
picocom --baud 115200 --databits 8 --parity n --stopbits 1 --flow n --imap lfcrlf /dev/ttyACM0
```

Press RESET after `Terminal ready`. `--imap lfcrlf` displays the firmware's LF-only output on separate terminal lines without changing transmitted data. Exit with **Ctrl+A**, release, then **Ctrl+X**. Before programming again, close the monitor and return SW4 to **SE** with board power disconnected.

`g_smoke_status` is a 60-byte debugger-readable record. Its current address is recorded in the generated manifest, not hard-coded in these instructions. It includes stage/error, elapsed milliseconds, NPU IRQ/job counts, timeout counters, detected MAC count, checked-value count, numerical error in millionths, and fault registers.

Stages: `0 Boot`, `1 BoardReady`, `2 RuntimeReady`, `3 NpuReady`, `4 Loading`, `5 Executing`, `6 Pass`, `7 Fail`, `8 Fault`. Numeric stage values are labels, not a monotonic progress counter. Magic is `0x48534750`, record version 1.

Errors: `1 board/UART setup`, `2 NPU init/config`, `10 memory placement`, `11–18 program/method/input setup`, `19 execution`, `20 missing IRQ/job evidence or timeout`, `21–22 output contract`, `23 numerical mismatch`, `24 mutated input`, `100 PAL abort`, `101 C runtime exit`.

## Prepare and deliberately program the smoke image

**Do not use Viewfinder's programming task with Viewfinder still selected**: it selects Viewfinder's binary, not this one. Open the **HandGPT root folder** in VS Code to use the separate tasks in [`.vscode/tasks.json`](../../.vscode/tasks.json). No task runs automatically or programs as a dependency of building/preparing.

1. Run **HandGPT: Prepare smoke programming (no device access)** and supply the installed SETOOLS directory and `/dev/ttyACM0` (or its stable `/dev/serial/by-id/...` path).
2. Run **HandGPT: Review prepared smoke (no device access)**. Verify **M55_HP**, address **0x80200000**, the serial port, image SHA-256, generated JSON, and package map.
3. Close all serial monitors. With board power disconnected, set SW4 to **SE**, then reconnect. Confirm the intended E8 Rev A0 is connected and that the working Viewfinder can be restored using its original project/task.
4. Only when ready to replace the camera application, run **HandGPT: Program prepared smoke (WRITES BOARD)**. Type exactly `PROGRAM E8-HP SMOKE` at its confirmation prompt. Do not disconnect power/cable while programming. If the tool reports failure, retain `program.log` and inspect it rather than proceeding as though programming succeeded.

Equivalent commands from the HandGPT root:

```bash
# Host-only preparation: does not open the serial port or write the board.
python3 -m tools.program_smoke prepare --setools-root ~/alif/app-release-exec-linux --port /dev/ttyACM0
python3 -m tools.program_smoke review

# DEVICE WRITE: run this yourself only after reviewing the prepared package.
python3 -m tools.program_smoke program --write
```

`program` **without** `--write` is review-only. With `--write`, the helper still requires the typed confirmation. After confirmation it rechecks the package and invokes only `app-write-mram -p -c <selected-port>` from the **private** toolkit directory. The installed SETOOLS must already select the E8 Rev A0 and ISP serial interface; the helper does not reconfigure it or run discovery.

Preparation copies SETOOLS 1.112.00 into a new private `.work/inference/programming/smoke-*/setools/` directory and generates a fresh package there. It validates the firmware's build/source/configuration hashes and boot vectors, changes only the baseline HP TOC image name to `handgpt-smoke.bin`, then validates the generated map: one user-managed image at `0x80200000`, boot entries `DEVICE`/`USER_APP`, and the application TOC below the system region at `0x80580000`. Package/tool hashes are rechecked before writing; rebuild/re-prepare if anything has changed.

**Programming writes more than the raw firmware:** it replaces the HP camera application, its boot table, and the baseline `DEVICE` configuration processed by the installed toolkit for E8. It does not request a full erase, system-package update, provisioning, or debug-stub installation. The original Viewfinder source/build configuration and installed toolkit package are not overwritten during preparation.

The generated receipt, JSON, package map, preparation log, and (after a write attempt) `program.log` live under that private directory; `.work/inference/programming/latest.json` points to the current receipt. The toolkit copy includes local signing material, so the staging directory is owner-only and ignored by Git. **Do not commit or share this directory**, particularly its `utils/key` contents. Each preparation makes a new copy; there is no automatic cleanup.

Programming success is **not** inference success. The writer may reset the board as part of its normal operation. After it finishes, disconnect board power, select SW4 **U4**, reconnect, open the application-UART monitor at 115200 8-N-1, and reset the board to capture startup logs. Save the observed application LED behavior and complete output for diagnosis. This standalone image does not show camera preview.

Host regressions run with `python3 -m unittest discover -s tests -v`. Placement regressions reject a temporary pool in DTCM (including the original bad address), missing/misaligned/out-of-region pools, and overlapping NPU pools. Native C++ checks exercise the startup SRAM0 range validator, including region boundaries and overflowing lengths. Native UART tests use a fake driver that reports all bytes queued while still busy, including consecutive writes, stale events, completion during `send()`, rejected sends, missing callbacks, abort failures, recovery after timeout, and clock rollover. A separate native test runs the actual `main.cpp` against board/NPU/model stubs and checks final reports for initialization failure, execution failure, and success. These tests do not establish physical NPU execution.

Physical acceptance is still outstanding: observe the complete output/result, verify nonzero IRQ/jobs and numerical comparison, then repeat after a cold power cycle. Nothing in this target marks hardware verification complete automatically.
