# HandGPT — Offline ASL Handshape Recognition on the Alif Ensemble E8

> **Project status:** Hardware/toolchain bring-up in progress  
> **Target platform:** Alif Ensemble E8 DevKit (DK-E8), `AE822FA0E5597LS0` Rev A0  
> **Compute target:** Cortex-M55 High-Performance (E8-HP) + Arm Ethos-U85 NPU  
> **Development host:** Arch Linux, VS Code/CMSIS-Toolbox, Arm GNU Toolchain, Alif SETOOLS, SEGGER J-Link  
> **Demo objective:** Recognize a small vocabulary of *static ASL handshapes* entirely on-device and display the predictions.

## 1. Project overview

**HandGPT** is an offline, privacy-conscious embedded computer vision prototype that recognizes a limited set of American Sign Language (ASL) handshapes from a camera attached to an Alif Ensemble E8 development board. A lightweight neural network processes camera frames and predicts a handshape class. The Cortex-M55 runs the application and prepares images; ExecuTorch delegates supported quantized neural-network operations to the Ethos-U85 neural processing unit (NPU).

The initial demo targets **A, B, C, L, Y**, plus **NO_HAND / UNKNOWN** handling. The output can be displayed on a connected computer using SEGGER RTT (or a proven serial channel), with a simple UI added if time permits.

**Important scope statement:** Recognizing individual static handshapes is **not** equivalent to translating ASL. ASL is a full language with movement, location, facial expression, grammar, and context. The MVP does not interpret sentences or dynamic signs, including the motion-dependent letters **J** and **Z**. Do not advertise the MVP as a general ASL translator.

### Project goals

- **Offline:** Camera images and model inference run on the development board; no cloud inference required for the live demo.
- **End-to-end:** Demonstrate camera frame -> preprocessing -> on-device model -> prediction output.
- **Accelerated:** Use the Ethos-U85 for supported operators and measure real hardware performance.
- **Demonstrable:** Recognize at least five deliberately selected handshapes reliably under a controlled demo setup.
- **Documented:** Record reproducible build/flash steps, supported hardware, accuracy, and limitations.

### Non-goals for the MVP

- Full ASL translation, continuous signing, or sentence understanding.
- Dynamic/motion-based sign recognition.
- Robust performance in arbitrary lighting, backgrounds, or camera placements.
- Generative language-model inference on the E8 (the project name does not imply an LLM).
- Automatic hand tracking across a large scene unless the basic pipeline already works.

## 2. Hardware, software, and current progress

### Hardware

| Component | Role | Status |
|---|---|---|
| Alif DK-E8, `AE822FA0E5597LS0` Rev A0 | Embedded host | **Confirmed** by SETOOLS |
| Cortex-M55 HP (`E8-HP`) | Camera pipeline, application logic, inference orchestration | Target selected; Blinky built |
| Ethos-U85 NPU | Quantized inference acceleration | **Not yet validated** |
| Camera module, connected under the PCB (J16 on DK-E8) | Input frames | Physically installed; sensor/model and capture **not yet confirmed** |
| Onboard SEGGER J-Link | Debug/RTT | USB probe connection verified; **core-level debug attachment previously failed** |
| USB serial interface (`/dev/ttyACM0` during setup) | SETOOLS communication | Verified; port name may change after reconnection |
| Optional camera stand / compatible extension | Aim the lens at the signer | Recommended if the current mounting is awkward |

**Camera mounting decision:** Keep the underside camera connector in its intended location. First tilt, mount, or stand the **entire board** so that the lens sees a fully visible hand. Ask organizers for a compatible stand or extension only if necessary. Confirm ribbon/connector compatibility and power off before changing camera wiring. Do not reposition hardware by soldering as a first step.

**Camera sensor:** The Alif M55 Viewfinder repository lists **OV5675** as the default E8 sensor, but this specific board's module must still be identified. Its example also supports MT9M114 and ARX3A0; select the component that matches the actual camera.

### Development environment

- Arch Linux (host)
- VS Code with the Alif/CMSIS project workflow
- `alif_vscode-template` checked out at `8b1aa0b0…` during initial bring-up
- GCC Arm Embedded 13.3.1 through the installed Arm tools environment
- Alif SETOOLS 1.112.00 at `/home/tatin/alif/app-release-exec-linux`
- SEGGER J-Link Commander 9.86
- Python + PyTorch for training and validation
- ExecuTorch + Arm Ethos-U backend + Vela toolchain for model export/delegation

**Host-specific configuration (Arch Linux):**

```json
{
  "alif.setools.root": "/home/tatin/alif/app-release-exec-linux",
  "cortex-debug.JLinkGDBServerPath": "/usr/bin/JLinkGDBServerCLExe"
}
```

Check the second executable path with `command -v JLinkGDBServerCLExe`; don't assume it exists at that exact location on every machine. The prior macOS paths (`/Users/...`, `/Applications/...`) must not appear in active Linux programming tasks.

### Progress tracker (as of 2026-10-17)

| Milestone | Status | Evidence / remaining verification |
|---|---|---|
| USB detects onboard J-Link | ✅ Done | `1366:1024` detected |
| Non-root J-Link access | ✅ Done for shell with `jlink` group | `JLinkExe` reported `Connecting to J-Link ...O.K.`; log out/in to persist group membership in all sessions |
| SETOOLS installed | ✅ Done | SETOOLS `--help` worked |
| Correct silicon identified | ✅ Done | `AE822FA0E5597LS0` Rev A0 |
| SETOOLS serial transport | ✅ Done | Communicated via `/dev/ttyACM0` |
| System Package update | ✅ Completed operation | `updateSystemPackage -d` reported authenticated, verified images and `Done`; avoid repeating casually |
| Blinky builds for `E8-HP` | ✅ Done | `blinky.elf` linked under Arm GCC 13.3.1 |
| Blinky programmed and executing | 🟡 **Needs confirmation** | User reports reset works; verify the **expected LED visibly blinks**, not just that reset succeeds |
| J-Link attaches to M55 HP | 🔴 Not yet | SW-DP responded, but CoreSight core discovery failed; investigate only if needed for RTT/debug |
| Camera captures a frame | ⬜ Not started | Confirm sensor and viewfinder example |
| Model inference on E8/U85 | ⬜ Not started | Start with official reference inference example |
| Live handshape demo | ⬜ Not started | End-to-end integration |

> **Safety note:** `app-write-mram --discover` in this environment did **not** behave as a read-only probe: it also wrote `build/AppTocPackage.bin`. Likewise `updateSystemPackage -d` writes system components. Use verified programming tasks intentionally; don't rerun these commands just to check USB detection.

## 3. Proposed system architecture

```text
                Hand in camera field of view
                            |
                            v
                    Camera sensor (J16)
                            |
                            v
                 E8 image/camera driver
                            |
                            v
            Cortex-M55 HP: frame acquisition
                            |
                            v
              Preprocessing / centered ROI
         (colorspace, resize, crop, normalization)
                            |
                            v
                Quantized model input tensor
                            |
                            v
           ExecuTorch runtime + Ethos-U85 delegate
                            |
                            v
          Class scores: [A, B, C, L, Y, ...]
                            |
                            v
            Threshold + smoothing + UNKNOWN
                            |
                            v
            SEGGER RTT / serial / optional UI
```

### Firmware responsibilities

1. Initialize clocks, buffers, camera, logs, NPU, and model runtime using the board-supported APIs.
2. Capture a frame (start with one frame at a time, not continuous streaming).
3. Convert the frame to the same format used to train the model.
4. Feed the prepared tensor to ExecuTorch and execute inference.
5. Convert output scores to a predicted class; reject low-confidence or no-hand frames.
6. Emit `label`, `confidence`, frame number, and inference time.
7. Repeat with a bounded frame rate and predictable memory use.

**Do not invent the camera driver's image format.** Verify RGB/YUV/RAW output from the selected sensor/example, and document the exact resize/crop/channel order/quantization used. Preprocessing mismatches are a common source of poor on-device accuracy.

### Proposed module boundaries

```text
firmware/
  app_main.cpp               # Initialization and main loop
  camera_capture.{h,cpp}     # Capture one frame and report its format
  preprocess.{h,cpp}         # ROI, resize, color conversion, quantization
  model_runner.{h,cpp}       # ExecuTorch runtime / NPU integration
  postprocess.{h,cpp}        # Scores, thresholds, smoothing, labels
  output.{h,cpp}             # RTT or UART output

ml/
  collect/                   # Image acquisition and annotation helpers
  datasets/README.md         # Dataset origin, splits, consent, labels
  train.py                   # Host training entry point
  evaluate.py                # Metrics on held-out signers
  export_ethosu.py           # Export/quantization workflow
  labels.txt                 # Class index-to-name mapping

docs/
  hardware.md                # Module, lens orientation, board connection
  flashing.md                # Exact reproducible Arch/SETOOLS steps
  experiments.md             # Accuracy, latency, memory, versions
```

This structure is **proposed**, not already present in the Alif template. Integrate incrementally with the CMSIS project rather than restructuring the vendor build during hackathon bring-up.

## 4. Milestones and acceptance criteria

Work **top to bottom**. Each milestone has a measurable exit condition. Don't jump to a live CNN before validating camera capture and a known-good inference path separately.

### M0 — Confirm Blinky runs (short task)

- [x] Configure `E8-HP` and build Blinky.
- [ ] Run the verified **Program with Security Toolkit** task and record its final success log.
- [ ] Reset the board if the programming flow requires it.
- [ ] **Observe the intended user LED blinking repeatedly.** A reset alone is not sufficient evidence.
- [ ] Save the compiler, packs, template revision, and SETOOLS versions in `docs/flashing.md`.

**Done when:** The expected LED toggles without VS Code actively running.

### M1 — Bring up the camera

- [ ] Confirm the camera sensor part number and physical orientation.
- [ ] Clone/read [Alif M55 Viewfinder](https://github.com/alifsemi/alif_M55-viewfinder) and select `DevKit-E8`, `E8-HP`, and the matching camera component.
- [ ] Build and program the unmodified example first (after reviewing its instructions).
- [ ] Demonstrate one clear, correctly oriented frame or preview; if no display is available, arrange a supported transfer/debug path.
- [ ] Capture sample frames of an open hand and empty background.
- [ ] Record resolution, pixel format, exposure/lighting, frame time, and camera-to-hand distance.

**Done when:** You can repeatedly acquire readable hand frames, not merely initialize the sensor.

**Physical layout:** Aim lens at your hand; try a plain background and stable lighting. A 30–60 cm distance is a **starting experiment**, not a camera specification. Place the board on a stand if its underside camera is pointing into the desk.

### M2 — Validate known-good on-device inference (parallelizable with M1)

- [ ] Complete a simple official inference example **before** integrating a custom hand model: [Arm MNIST on E8](https://learn.arm.com/learning-paths/embedded-and-microcontrollers/observing-ethos-u-on-alif/) or the [Arm image-classification learning path](https://learn.arm.com/learning-paths/embedded-and-microcontrollers/alif-image-classification/).
- [ ] Produce a supported ExecuTorch `.pte` model and link/runtime artifacts following the tutorial **version being used**.
- [ ] Build and program `E8-HP` inference firmware with a fixed test image.
- [ ] Read a plausible inference result using the example's documented output path (often SEGGER RTT).
- [ ] Record whether model operators execute on the Ethos-U85 or fall back to CPU, and capture timing.

**Done when:** A repeatable inference result is produced by the physical E8 board with a known test input.

**Critical dependency:** Arm's reference image-classification path notes that memory settings and linker layout must be modified for larger models; do not assume the Blinky layout can hold ExecuTorch plus a full-size model.

### M3 — Build the handshape dataset and train a host baseline

- [ ] Fix the MVP label vocabulary: `A`, `B`, `C`, `L`, `Y`, `NO_HAND`. Consider an additional `UNKNOWN` rejection mechanism; do not assume training `NO_HAND` covers every unsupported gesture.
- [ ] Take labeled samples under multiple lighting conditions and camera angles, preferably using the actual board camera or a comparable viewpoint.
- [ ] Obtain participant consent before recording and decide whether original images are stored or deleted after training.
- [ ] Split by **person/signing session**, not by near-identical video frames, to reduce leakage.
- [ ] Start with a small, NPU-compatible CNN or MobileNetV2-style backbone; choose input size (for example, 96×96 or 128×128 RGB) after measuring accuracy, RAM use, and supported operators.
- [ ] Train in PyTorch; save the checkpoint, labels, fixed preprocessing definition, and validation metrics.
- [ ] Validate on **unseen hands**, backgrounds, and no-hand images.

**Done when:** The host model performs meaningfully above chance on held-out data, and its failure cases are documented. An initial *goal* is ≥90% accuracy in a controlled, held-out-signers demo set, but adjust the target to the amount and quality of available data; do not present it as achieved until measured.

### M4 — Export and run the actual HandGPT model on the NPU

- [ ] Test that the architecture's operators are supported by ExecuTorch's Arm Ethos-U backend.
- [ ] Quantize using the appropriate Ethos-U quantization flow (integer operations are required for NPU delegation).
- [ ] Export a `.pte` targeting Ethos-U85; preserve calibration inputs and export versions.
- [ ] Run a **fixed handshape test image** through the embedded model before using the camera.
- [ ] Match host and device class indices and image normalization/quantization.
- [ ] Compare host output vs device output on the same test images; record acceptable differences.
- [ ] Measure model binary size, RAM high-water mark where possible, inference time, and NPU use.

**Done when:** The board correctly classifies a known stored image using the deployed HandGPT model.

### M5 — Integrate live camera -> recognition

- [ ] Replace the fixed test image input with a single actual camera frame.
- [ ] Implement a bounded, reusable image buffer; avoid per-frame dynamic allocation where possible.
- [ ] Implement preprocessing with exactly the same tensor shape/channel order/quantization as training.
- [ ] Add `NO_HAND` handling and low-confidence rejection.
- [ ] Add a short prediction-stability filter (e.g., majority vote over 3–5 frames) to prevent flicker; tune empirically.
- [ ] Display `prediction`, `confidence`, and timing; preserve a way to inspect raw results.
- [ ] Run a 5-minute demo loop without lockups, buffer corruption, or repeated flashing.

**Done when:** A signer can hold A, B, C, L, or Y in view and receive a stable on-device prediction without internet access.

### M6 — Demo, evaluation, and presentation

- [ ] Prepare a simple five-sign live demonstration and a **fallback recorded video**.
- [ ] Display an honest supported-label list and limitations.
- [ ] Test at least two people where practical, plus hands absent from the frame.
- [ ] Record accuracy/confusion matrix, end-to-end latency, and inference-only latency.
- [ ] Capture architecture diagram and one clean photo of the board/camera setup.
- [ ] Document how to reproduce the demo: host tool versions, build target, flashing task, model version, output terminal.
- [ ] Confirm the demonstration works after a cold power cycle.

**Done when:** Someone unfamiliar with the project can see a sign, see the prediction, and understand that classification runs on the board.

## 5. Machine-learning design decisions

### Baseline input/output contract

| Property | Initial proposal | Must be validated |
|---|---|---|
| Input | Centered hand ROI; RGB at fixed resolution | Real sensor pixel format and model export |
| Input tensor | Small spatial input (e.g., 96×96 or 128×128) | Memory/runtime and accuracy |
| Classes | A, B, C, L, Y, NO_HAND | Label order identical on host/device |
| Output | Class scores -> label/confidence | Output quantization or dequantization |
| Unknown handling | Reject low-confidence results | Threshold tuned on held-out unknown gestures |
| Stability | 3–5-frame vote or hysteresis | Responsiveness versus flicker |
| Inference backend | Ethos-U85 via ExecuTorch | Supported delegated operators and runtime proof |

### Dataset and evaluation rules

- Use diverse skin tones, hand sizes, handedness, sleeves, and lighting conditions where possible. Report limitations if data are narrow.
- Avoid collecting many adjacent video frames and then randomly splitting them across train/test: those images are too similar to measure generalization fairly.
- Include **background-only** examples, and deliberately test unsupported gestures; these are not automatically solved by a `NO_HAND` class.
- Favor a centered, single-hand interface for the MVP. True hand detection/tracking is a separate computer-vision problem and a stretch goal.
- Label examples consistently and review ambiguous poses. Document whether the hand is mirrored in capture/preprocessing.
- Record a **confusion matrix**, per-class precision/recall, unknown rejection behavior, and signer-separated accuracy; don't report training accuracy as real-world performance.

### Performance targets (aspirational, not measured)

- **Functional priority:** Correct end-to-end output from live frames.
- **Responsiveness goal:** Aim for 2–5 stable predictions/second initially; optimize only after profiling.
- **Memory priority:** No allocation failures and no hidden CPU fallback causing instability.
- **Privacy goal:** No network dependency for live inference and no default retention of captured frames.

## 6. Integration guidance and known risks

| Risk | Symptom | Mitigation |
|---|---|---|
| Camera points downward from board underside | Hand not in view | Stand/tilt the board; verify orientation before modifying hardware |
| Camera sensor mismatch | Camera initialization fails or image corrupt | Identify sensor and choose correct BSP camera component |
| Incorrect preprocessing | Host model accurate, E8 predictions poor | Save golden inputs/outputs and compare byte-level pipeline stages |
| Model unsupported on NPU | Export failure or slow CPU fallback | Use small supported ops; inspect delegation; validate with reference model |
| Memory pressure | Link failure, crash, corrupt frames | Follow E8 memory/linker tutorial; budget capture tensors, arena, stack, code |
| SWD core attach failure | `Could not find core in Coresight setup` | Don't equate probe connection with CPU attachment; follow vendor debug instructions and prioritize SETOOLS programming if RTT not essential |
| Old macOS file paths | Copy/program task cannot find destination | Use `/home/tatin/alif/app-release-exec-linux` and inspect tasks/settings |
| Maintenance/System Package operations | Device state changes unexpectedly | Avoid repeated manual writes; only use documented programming and recovery flow |
| Dataset shortcut or leakage | High validation, poor live predictions | Signer/session-separated evaluation and real camera samples |
| Over-scoping ASL | No complete working demo | Present static handshape recognition as the honest MVP |

## 7. Hackathon priority order and fallback demos

**Priority 0:** Make sure Blinky actually blinks.  
**Priority 1:** Get **one camera image**.  
**Priority 2:** Get **one known model inference** on Ethos-U85.  
**Priority 3:** Train a small model that distinguishes five handshapes.  
**Priority 4:** Run the custom model on a **stored** hand image on E8.  
**Priority 5:** Connect the live camera; make predictions stable.  
**Priority 6:** Polish UI and presentation.

If pressed for time, present the strongest honest result:

- **Tier A (ideal):** Live camera -> custom HandGPT model -> E8 NPU -> live label.
- **Tier B (solid):** Saved hand images -> custom model -> E8 NPU -> label, plus separate working camera capture.
- **Tier C (working technical proof):** Official ExecuTorch/Ethos-U85 reference inference on the E8, plus a handshape classifier tested on the host; clearly identify integration as unfinished.

Avoid showing a laptop-hosted classifier while implying it ran on the board. Label each demo's actual compute location.

## 8. Immediate next actions

1. **Verify Blinky:** Does the intended LED repeatedly blink after programming/reset? Record the successful programming log.
2. **Confirm camera hardware:** Photograph/read the camera-module marking; choose whether a stand is needed.
3. **Run M55 Viewfinder:** Use the documented `E8-HP` camera sample and its matching sensor component to obtain an image.
4. **In parallel:** Follow the official Arm **MNIST** or **image classification** ExecuTorch/Ethos-U85 learning path through an actual test-image inference.
5. **Only then** start integrating custom handshape data and models.

## 9. Reference documentation

These are implementation references, not proof that the above milestones have been completed:

- [Arm — Set up the Alif Ensemble E8 DevKit](https://learn.arm.com/learning-paths/embedded-and-microcontrollers/alif-image-classification/board-setup/)
- [Arm — Image classification on E8 with ExecuTorch and Ethos-U85](https://learn.arm.com/learning-paths/embedded-and-microcontrollers/alif-image-classification/)
- [Arm — MNIST on Alif E8 with ExecuTorch and Ethos-U85](https://learn.arm.com/learning-paths/embedded-and-microcontrollers/observing-ethos-u-on-alif/)
- [Alif — M55 Viewfinder camera example](https://github.com/alifsemi/alif_M55-viewfinder)
- [Alif — VS Code/CMSIS template](https://github.com/alifsemi/alif_vscode-template)
- [ExecuTorch — Arm Ethos-U backend](https://docs.pytorch.org/executorch/stable/embedded-arm-ethos-u.html)
- [ExecuTorch — Arm Ethos-U quantization](https://docs.pytorch.org/executorch/stable/backends/arm-ethos-u/arm-ethos-u-quantization.html)
- [Arm — E8 ML firmware memory layout](https://learn.arm.com/learning-paths/embedded-and-microcontrollers/alif-image-classification/memory-configuration/)

---

**Working definition of success:** A hand appears in front of the DK-E8 camera; without sending the image to a cloud service, the E8 captures the image, preprocesses it, executes a quantized HandGPT classifier using ExecuTorch/Ethos-U85, and reports a stable supported handshape label with measured latency. The demo and documentation explicitly describe this as *limited static ASL handshape recognition*, not complete sign-language translation.
