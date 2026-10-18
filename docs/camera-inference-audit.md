# HP camera/display and inference coexistence audit

## Result and scope

The separate `camera-inference.debug+DevKit-E8-HP` target clean-builds offline. The user confirmed visible preview after the dequeue fix, supplied v2 self-test/preview logs, and subsequently captured CRC-verified v3 images with a saved `COEXIST SELFTEST PASS checked=1000` startup log. The exact cause of earlier v2 capture silence remains unproven. The latest [v4 fast full-view export](camera-frame-capture.md) retains the RX-free boot flow, adds PIL-equivalent 480→128 resizing, and is built/prepared offline but **not hardware-tested**. It retains the same gate: show camera frames, pause acquisition, run the unchanged stored-image self-test while display scans, then resume preview. It does **not** classify camera pixels or prove simultaneous camera DMA/NPU traffic. Full cold-boot acceptance remains pending.

The working Viewfinder submodule (`5e5568cb4d17ae88e9e7262409414a2d1c812cec`), smoke firmware, stored-image firmware/model, and existing programmers/packages are not edited or repurposed. The user's two supplied stored-image PASS logs establish the prerequisite hardware milestone; see [acceptance record](stored-image-hardware-acceptance.md). Their `hardware_verified` build-manifest flags are not hand-edited.

## Evidence and memory decision

The existing Viewfinder ELF, inspected with GCC 13.3.1 `nm`, contains:

| Allocation | Bytes | Linked baseline address |
|---|---:|---|
| `d0_heap` | 3,014,656 | `0x02000000` (SRAM0) |
| `isp_buf` | 691,200 | `0x02400000` (SRAM1) |
| `lcd_buffer_2` | 768,000 | `0x024a8c00` (SRAM1) |
| `lcd_buffer_1` | 768,000 | `0x02564400` (SRAM1) |

The baseline binary is 233,920 bytes. The sensor/ISP configuration selects MT9M114, one 480×480 planar RGB888 ISP buffer (format 39), CPI AXI output disabled, and a 480×800 RGB565 ILI9806E display. The original render path dynamically allocates RGB565 intermediates from the graphics heap. That heap plus the verified 2,931,616-byte inference reservations would exceed SRAM0 by **1,751,968 bytes**. Neither pointers nor reusing one occupied address makes those live allocations coexist.

For the first integration gate, **omit D/AVE2D/AIPL graphics allocation entirely**. A bounded-size CPU loop converts planar RGB888 directly into the inactive RGB565 display buffer, with black below the 480×480 picture. This is a separate bring-up rendering path, not an assertion of identical Viewfinder performance. It intentionally omits the logo/overlays. No NPU pool or method/heap/stack reservation is shrunk based on the observed 864-byte method high-water mark.

The new link reports:

| Region | Reservation/use | Remaining capacity |
|---|---:|---:|
| HP MRAM `[0x80001000, 0x80500000)` | 4,725,360 bytes (v4) | 513,424 bytes |
| SRAM0 — existing NPU arena, temporary pool, CPU I/O tensors | 2,931,616 bytes | 1,262,688 bytes |
| SRAM1 — camera RGB planes + two LCD buffers | 2,227,200 bytes | 1,967,104 bytes |
| DTCM — heap-to-stack free interval | — | 557,056 bytes |

Camera buffer: 691,200 bytes. Two LCD buffers: 1,536,000 bytes. All are dedicated, 32-byte-aligned allocations. Model, input, and golden logits are compared byte-for-byte against the accepted stored-image binary after linking. The first 4 KiB and final 512 KiB MRAM reservations remain unchanged. The builder does not generate ATOC/device packages; the separate [target-specific preparation/review helper](../firmware/camera_inference/README.md#prepare-and-review-without-programming) now handles that host-only step.

These sizes describe the implementation link, not a promise of future capacity. The generated manifest records exact extents, identities, IRQ checks, and the SDK ISP archive/header/source provenance for every accepted build.

## Peripheral and ABI ownership

- One private generated platform port owns application UART4, SysTick, newlib syscalls, fault/status handling, NPU cache hooks, and IRQ 366. It derives from the verified smoke port, inserting a power-profile request **before** UART/tick initialization. The original source is unchanged.
- Do not link Viewfinder's UART trace/retarget, fault handler, clock initialization, LEDs, main loop, or D/AVE2D allocator. The builder rejects known competing owners/graphics-heap symbols.
- Reuse the pinned Viewfinder MT9M114 calibration sources/header without editing them; retain vendor notices/license. Only MT9M114 is selected, not an additional sensor.
- The power request starts from `SERVICES_get_run_cfg`, preserves existing resource bits, and explicitly requests SRAM0/SRAM1, camera/CSI/DSI/CDC, and **HP NPU**. It retains the vendor run voltage/PLL/400 MHz settings. No off-profile or deep-sleep support is added; in particular, do not reuse the old hard-coded off-profile VTOR at `0x80200000`.
- GCC application/runtime/driver and SDK `libisp_gcc.a` are checked for matching **small-enum / hard-float** ABI. The integration's SDK/calibration C sources also use `-fshort-enums`.
- Actual vector entries must resolve to strong linked handlers: CDC scanline **333**, DSI **343**, CSI **344**, CPI **345**, NPU **366**, ISP **367**, and ISP MI **368**, alongside the existing SysTick/reset/fault checks. SDK camera/display handler names are not assumed to be `IRQ<n>_Handler`; their actual named symbols are checked.

## Arm learning-path cross-check: GOT and NPU completion

Arm's [E8 image-classification learning path](https://learn.arm.com/learning-paths/embedded-and-microcontrollers/alif-image-classification/create-project/) is a reference for the same ExecuTorch/U85 approach, not a replacement project adopted here. Two of its workarounds differ from our current configuration:

### GOT remains in MRAM; no RAM copy is required for this link

The learning path's [memory configuration](https://learn.arm.com/learning-paths/embedded-and-microcontrollers/alif-image-classification/memory-configuration/) puts GOT inputs into the startup-copied DTCM data section. Our Ensemble 2.2.0 linker instead explicitly collects `.got`, `.got*`, `.got.plt`, and `.data.rel.ro*` into a `.got` output section in MRAM. This is deliberate placement, not an unhandled orphan section.

For the originally reviewed coexistence binary SHA-256 `c3b80be7f636d0451a588e0107303d9bc1852f4653ab0a1bee3921287e03497e`, the map and ELF section headers agree: `.got` occupies **0xd0 bytes at `0x80001890`**, with **identical load and runtime addresses**. It is loaded as part of the MRAM image and read in place; it does not need a startup RAM-copy entry. This output section also contains read-only C++ vtables, so its entire size is not exclusively GOT pointer entries. The standalone stored-image hardware PASS supports that baseline's initialization path, but does not hardware-verify the coexistence image.

On any SDK, runtime-library, or linker upgrade, recheck the actual ELF load/runtime addresses, map, image contents, and startup copy table. If GOT/vtable data moves to RAM with a separate MRAM load address, require the appropriate startup copy coverage before running C++ code. PIC alone does not imply a RAM-resident GOT. This note records the reviewed link; it does not add a new automatic GOT-placement regression check or change the linker.

### Polling is a documented fallback candidate, not an enabled path

The learning path's [application code](https://learn.arm.com/learning-paths/embedded-and-microcontrollers/alif-image-classification/application-code/) describes polling `NPU_HG` status and calling `ethosu_irq_handler()` from its semaphore wait, plus backup polling from SysTick, because it reports no routed HP completion interrupt. Our platform instead uses **IRQ 366**: `IRQ366_Handler()` counts the interrupt and calls `ethosu_irq_handler()`, while SysTick only advances time. The user's standalone stored-image runs reported **`IRQs=1 jobs=1/1`**, with zero timeouts. Keep that working interrupt-driven path unchanged. The reason for the discrepancy with the tutorial is **not established**; a pack-version difference alone does not prove the cause.

If completion interrupts stall in a future configuration, first investigate the vector, NVIC enable/security/priority, interrupt masks, power/clock state, and NPU status. Status polling is a possible separately reviewed diagnostic/fallback, **not implemented or automatically enabled here**. Do not paste the tutorial's unbounded wait: any proposed fallback must retain the existing bounded semaphore timeout/error reporting, validate the status register for the pinned U85 driver, and serialize completion handling against the IRQ path to prevent duplicate/racing driver calls. Require dedicated tests and new hardware acceptance before adopting it.

## Buffer ownership and cache protocol

1. **Power first.** SRAM1 buffers are NOLOAD and excluded from the startup zero table, because the SE run-profile request occurs after startup. `preview_init()` explicitly zeros both allocations after that request, before configuring DMA. The builder rejects a startup zero-table entry touching SRAM1. Startup copy-table checks also prohibit a load image targeting that bank.
2. **Capture.** Clean/invalidate the complete isolated camera allocation, queue its three plane addresses, configure CPI events, and start a snapshot. The dummy CPI address is used only because AXI output is disabled, matching the baseline.
3. **Completion.** Accept only the full ISP MI frame-end event, not a Y-plane fill/wrap or CPI-stopped event. Treat callback events as a bitmask, including simultaneous errors. Process the ISP frame end, then require a successful stop of sensor/CSI/ISP/CPI and no latched camera error before invalidating the known single allocation for CPU pixel reads. **Do not use `DQBUF` on this pinned CMSIS path:** its MI ISR does not populate the completed VB queue (see the bring-up finding below). No failed buffer is rendered or passed to inference. Each successful stop resets the library VB lists before the next single-buffer `QBUF`.
4. **Exposure.** Preserve the baseline AE cached-value feedback to sensor exposure/gain, using a 64-bit intermediate and rejecting gain overflow.
5. **Display.** CPU conversion writes only the inactive LCD buffer. Clean that full allocation, request a VSYNC framebuffer reload, and wait for two subsequent scanline events before considering the former buffer reusable. Display error callbacks are latched; timeout/failure never proceeds to the next reuse.
6. **Inference.** After three successfully displayed frames, the camera remains stopped. The LCD continues scanning its separate buffer while the unchanged stored-image runner loads/executes and checks all 1,000 logits. Require display events during this self-test and two successful preview frames afterward before printing `COEXIST SELFTEST PASS`.
7. **Export.** Once per boot, after successful self-test and two seconds of settling preview, the foreground reads the existing RGB allocation with capture still stopped. V4 computes full-view PIL-equivalent 128×128 output, using a 1,024-byte stack scratch and a 4,608-byte MRAM filter table; the camera source is never changed. CRC and TX recompute the same output in separate passes. No extra frame buffer is allocated; LCD scanout continues. The unchanged TX callback/initialization enables no UART RX; the host opens read-only and sends no request. Export failure is best-effort/nonfatal to preview; measured timeout counts remain intact. The host verifies exact length, version, dimensions, CRC, trailer and boot/wire profile consistency before saving a labeled raw/PNG pair.

Application capture-event waits are bounded at 5 seconds and display-flip waits at 1 second, using the existing millisecond clock with wrap-safe subtraction. The NPU/UART bounds remain inherited from the verified port. **These are not global watchdog guarantees:** opaque SDK sensor/ISP/DSI/SE calls can have their own synchronous waits, and host mocks do not validate the real ISP queue/stop/restart behavior. That behavior, image colors/orientation, visual preview, actual power/clock changes, and repeatability must be checked on hardware.

## Validation and remaining gates

Host tests cover RGB plane order/packing/borders/dimension overflow; timestamp wrap; DMA-to-CPU ownership order; full-frame versus partial/stopped notifications; simultaneous callback bits; queue/statistics/stop errors; errors arriving during stop; exact queued plane addresses/size; repeated queue/start/stop with empty completed-VB queue; display errors/timeouts; power resource preservation/service errors; and main-loop refusal to PASS without pre/post preview and display events during the self-test. Link tests reject NPU/video overlap, wrong-bank/oversized buffers, weak/mismatched IRQs, competing ports, and premature SRAM1 zeroing.

### First board run: unsupported dequeue assumption corrected

The user supplied:

```text
COEXIST preview configured: RGB888p 480x480 -> RGB565 480x800; CPU copy, no GPU/graphics heap
COEXIST driver failure: ISP DQBUF result=-1
COEXIST FAIL error=202 frames=0 scanlines=5 uart_timeouts=0
```

This establishes startup/display-event activity and failure before a rendered preview frame or NPU initialization. It does not establish valid camera pixels, visual preview, or combined inference operation. No combined PASS is recorded.

The integration incorrectly assumed that the public `DQBUF` operation could supply a completed descriptor after `ISP_PROCESS_FRAME_END`. Inspection of Ensemble 2.2.0 `Driver_ISP.c` and `libs/isp/inc/isp.h` shows `ISP_MI_ISRHandler` only reads/clears MI status and reports callback bits. The frame-end control calls `VSI_ISP_IrqProcessFrameEnd`; disassembly of the pinned `libisp_gcc.a` shows statistics/pipeline processing there, with no completed-buffer enqueue. `DQBUF` delegates to `VSI_VB_DeQueueBuf`, which fails for an empty completed list. The working Viewfinder does not dequeue at all; it reads its known ISP allocation.

The revised integration therefore does not call `DQBUF` or ignore/retry its failure. It keeps the stricter full-frame event requirement and adds no extra buffers: CPU ownership still requires a successful complete pipeline `Stop` before cache invalidation/read. The pinned library's channel-disable path calls `VSI_VB_StreamOff`, which resets both queue list heads; the next capture queues the sole allocation again. SDK sources/archive and verified baselines are not patched. The host mock now models an empty completed queue (every `DQBUF` attempt fails), checks that `DQBUF` is never called, and requires a successful stop before CPU access. This fixes the identified API mismatch; hardware confirmation of the revised capture/stop/restart sequence remains required.

That no-DQBUF revision clean-built with 200 host tests passing. Binary SHA-256: `c49f474e7b790e880f2a1af6e1ba02cfa78cf3ab65c03d37ef0bdfb424aa603b`; extent `[0x80001000, 0x804814c0)`. It was subsequently user-programmed with the preview observations above. Its receipt/attempt logs remain retained. V4 uses separate `camera-inference-fast-firmware/` and `camera-inference-fast-programming/` directories, preserving v3's original build/packages/pointer. See [camera frame capture](camera-frame-capture.md); no writer or real serial access ran for v4.

Next steps, in order:

1. Use the implemented **target-specific** `tools.prepare_camera_inference` package/review helper and `tools.program_camera_inference` guarded writer as documented in the [target README](../firmware/camera_inference/README.md). Preparation/review and default programming mode do not access the board. Writing requires a separate explicit user action, `--write`, a terminal, the target-specific typed phrase, and complete revalidation. Existing stored-image preparation/programming still targets the standalone stored-image build; it must not be used to flash this candidate.
2. After a separate explicit user-triggered write, require a visually correct preview, complete `COEXIST SELFTEST PASS`, continued preview progress with no later FAIL, and a cold-power-cycle repeat. Do not accept the log without looking at the display.
3. Validate the implemented retained-frame export on hardware. Then add identical host/device preprocessing and compare logits on the **exact same captured bytes**, not the original cat photo or a later frame. RGB565 display conversion is not inference preprocessing.
4. Progress to repeated camera classification. A [host ASL baseline](asl-local-baseline.md) exists, but camera-domain accuracy, rejection and deployment remain separate gates. No camera/NPU concurrency claim is made here.
