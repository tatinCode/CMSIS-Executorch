# Stored-image inference: local toolchain workflow

This workflow prepares a **separate** ExecuTorch source checkout, Python environment, model, and Cortex-M55 runtime on the development computer. It does not install into the learning tutorial environment, modify Viewfinder, create cloud resources, or program a board.

**Current boundary:** host/model/runtime artifacts and a separate [CMSIS smoke firmware target](../firmware/smoke/README.md) are available. Root-level `main.cpp` remains an unvalidated reference runner, not the supported entry point for these generated assets. Physical-board execution and camera/inference coexistence are separate acceptance steps.

## Compatibility choices

| Item | Selection |
|---|---|
| Host | Linux x86_64 or aarch64 |
| Python | Isolated 3.10, managed by `uv`; exact resolved version recorded locally |
| ExecuTorch | `40d94b6d62a195a2f46b2baa20383fa4af27f7d4` (Arm's E8 reference revision) |
| PyTorch / torchvision / torchao | 2.10.0 CPU / 0.25.0 / 0.15.0 |
| Ethos-U Vela | 4.5.0 |
| TOSA tools | `v2025.11.0`; resolved commit recorded locally |
| Model target | `ethos-u85-256`, `Sram_Only` |
| Runtime target | Cortex-M55, hard-float, Arm GNU 13.3.1 |

Core package pins are in [`ml/reference/requirements-host.txt`](../ml/reference/requirements-host.txt). Resolved transitive dependencies are recorded in `.work/inference/environment.freeze.txt`; this is not a fully locked, bit-for-bit reproducible Python environment. Do not mix artifacts from the learning tutorial's `release/1.0` checkout with these artifacts.

## Prerequisites and safety

- Host tools: `git`, `uv`, `cmake`, `ninja`, and a C++ compiler. Install missing system packages deliberately; the helper never invokes `sudo`.
- Existing Arm GNU 13.3.1 compiler. The helper checks `PATH` and Arm Environment Manager's usual `~/.vcpkg/artifacts/*` location, or accepts an explicit path.
- Allow substantial disk space: bootstrap checks for at least 15 GiB free. Larger builds can require more. Use **one build job** on the current low-memory machine; close unnecessary applications if memory is tight.
- Dependencies, tool builds, pip temporary files, weights, and artifacts live under ignored `.work/inference/`, not RAM-backed `/tmp` or the camera submodule.
- Downloads/builds can take a long time. A stage failure stops the command; do not continue to later stages just because a file exists.
- Dependencies retain their upstream licenses. This workflow omits FVP installers and does not automatically accept their EULAs. Review applicable source/package and pretrained-weight terms before redistribution.

## Run from the HandGPT root

### 1. Inspect the host

```bash
python3 tools/inference.py doctor
```

This command is read-only. Missing managed Python is expected before bootstrap. System Python 3.14 can run the helper; it does not run ExecuTorch.

### 2. Install the pinned host environment

```bash
python3 tools/inference.py bootstrap --jobs 1
```

The helper fetches the exact ExecuTorch commit and its submodules, creates `.work/inference/venv` using Python 3.10, installs CPU-only PyTorch and the constrained requirements, then builds an editable host ExecuTorch package. Unrelated host backends are disabled to reduce build cost. A changed source revision is rejected instead of being reset.

On Arch's GCC 16, old FlatCC code triggers `-Werror=unterminated-string-initialization` for its intentionally non-NUL hexadecimal lookup table. The helper keeps the warning visible but prevents just that warning from becoming an error in host C compilation. It does not disable all warnings or patch the pinned upstream source. Cross-compilation explicitly excludes this host-only flag from Cortex-M55 C compiler flags.

On success, `pip check` must pass and environment metadata is written. This is a host installation, **not** an embedded runtime build.

### 3. Build Arm export tooling

```bash
python3 tools/inference.py arm-tools --jobs 1
```

This builds TOSA serialization against the compatible pinned pybind11 and checks its imports with Vela. It does not install a virtual board or download another Arm compiler. The TOSA reference simulator is not required by this export-only stage; if an upstream import unexpectedly requires it, stop and diagnose rather than ignoring the error.

The serialization build uses a persistent local build directory and omits upstream test executables, so a retry can reuse compiled objects. The model/tool checks below still need to pass.

### 4. Export a tiny smoke test first

```bash
python3 tools/inference.py export --model smoke --jobs 1
```

The deterministic model computes `output = input + input` for a float32 tensor shaped `[1, 16]`. It is only a setup/delegation check, not an image classifier. Outputs go into `.work/inference/models/smoke/`:

- `smoke_ethosu85_256.pte`
- `reference_model_pte.h` (16-byte-aligned constant model array)
- `intermediates/delegation_info.txt` and compiler artifacts
- `program_metadata.json` with validated input/output shape, dtype, layout, delegate IDs, remaining CPU operators, planned arena sizes, and decoded per-delegate scratch requirements
- `manifest.json` with target, model size, hash, and `hardware_verified: false`

The helper deserializes the actual `.pte` and rejects wrong input/output contracts, unexpected input layout, or a forward method without an `EthosUBackend` delegate. Inspect the delegation report as well: quantize/dequantize or other operations may remain on the CPU. Generating a `.pte` alone does not establish NPU execution or numerical correctness on hardware. On an export/runtime retry, the previous success manifest is invalidated before rebuilding, so a failed stage cannot retain a stale success manifest.

### 5. Cross-compile the embedded runtime

```bash
python3 tools/inference.py runtime --jobs 1
```

If compiler discovery fails, supply its **bin directory**:

```bash
python3 tools/inference.py runtime --jobs 1 --toolchain-bin /path/to/arm-gnu-13.3.1/bin
```

This configures the pinned upstream `arm-baremetal` preset for **Cortex-M55**, builds the install target, and separately builds the matching source checkout's driver for **Ethos-U85-256**. The driver uses the existing CMSIS 6.1.0 pack; pass `--cmsis-root /path/to/ARM/CMSIS/6.1.0` if it is not in the default pack directory. The helper checks required installed libraries/headers and uses `arm-none-eabi-readelf` to reject archives containing host objects rather than ARM objects. The combined install workspace is `.work/inference/runtime-cortex-m55/`.

The driver is built with its generic configuration, not a hardware-validated E8 memory/AXI mapping. Board-specific power, cache, address translation, memory-region settings, and completion hooks still need review before firmware integration. Do not link these Arm archives into a host executable or mix the bundled Ethos-U driver with another driver implementation accidentally.

### 6. Export MobileNet with a local test image

Use an image you have permission to process/store. Export retains its preprocessed tensor and outputs locally; no camera recording is performed.

```bash
python3 tools/inference.py export --model mv2 --image /path/to/test-image.jpg --jobs 1
```

This downloads torchvision's explicit `MobileNet_V2_Weights.IMAGENET1K_V2` weights if absent, calculates host reference logits, and uses the same prepared image for initial quantization calibration. One image is sufficient for a bring-up experiment, **not** representative production calibration or an accuracy claim.

`.work/inference/models/mv2/reference/` contains:

- `input.npy`: float32 `[1, 3, 224, 224]`, used by model export.
- `input.f32`: little-endian float32 NCHW data for device comparison.
- `reference_input.h`: a constant float array preserving normalized fractions.
- `host_logits.npy` and `reference.json`: host outputs/top-5 labels, image/input/logit hashes, weight identifier, and the exact preprocessing contract. New exports bind `reference.json` to the model manifest via `reference_sha256`.

The model uses the same explicit weights during calibration and reference inference. Preprocessing is EXIF orientation correction → RGB → bilinear short-edge resize to 256 → centered 224 crop → division by 255 → ImageNet normalization → NCHW float32.

**Do not cast normalized floats directly to int8.** That truncates useful fractional information; it is not a substitute for quantization using a defined scale and zero point. The legacy root runner currently does such an int8-to-float conversion and must be revised or replaced before it can use these assets. `reference_input.h` deliberately uses a different symbol/type to prevent accidental substitution.

### 7. Fail-closed memory preflight (host only)

Run from the HandGPT root with the **original image used by the export**. The checker uses the pinned HandGPT Python/ExecuTorch/FlatBuffers tools to freshly deserialize the `.pte`; it does not trust a caller's scratch estimate or an unrelated Vela `.npz`. It never opens a serial port, programs the board, downloads dependencies, changes the linker layout, or re-exports the model.

```bash
python3 -m tools.memory_preflight .work/inference/models/mv2 \
  --image ml/reference/images/cat.jpg \
  --output .work/inference/preflight/mv2-current.json \
  --scratch-bytes 1572864 --temporary-overhead-bytes 65536 \
  --arena-bytes 752640 --other-sram-bytes 0 --sram-bytes 4194304 \
  --firmware-mram-bytes 262144 --other-mram-bytes 0 --mram-bytes 2097152 \
  --method-pool-bytes 262144 --other-dtcm-bytes 16384 \
  --heap-bytes 131072 --stack-bytes 32768 --dtcm-bytes 1048576
```

These CPU/firmware/overhead reservations are **explicit planning estimates**, not measured MobileNet requirements. Adjusting estimates is not permission to enlarge the HP region. `other-dtcm-bytes` must include platform globals, initialised data, the SDK application heap, and any other DTCM allocations not covered by the named pools. `other-mram-bytes` and `other-sram-bytes` cover additional consumers such as camera/display buffers when planning future coexistence.

The **default** layout is fixed at HP MRAM base `0x80200000` / 2 MiB, SRAM0 base `0x02000000` / 4 MiB, and DTCM base `0x20000000` / 1 MiB. Capacities exceeding the selected profile are rejected, not treated as an arbitrary new memory map. The separately audited `--layout hp-only-large` profile provides `[0x80001000, 0x80500000)` for a standalone HP image and reserves the final 512 KiB tail. It reclaims default HE/user MRAM and **does not authorize programming**. The current profile remains the default.

Checks include:

- Manifest model/target/revision/memory mode, model byte count, and model/metadata hashes.
- Fresh PTE method contracts and delegation compared against cached metadata; unknown methods/backends are rejected.
- The pinned Vela payload's scratch size and bounded I/O descriptors. Planned arena bytes and delegate scratch are **different** allocations; actual scratch plus declared temporary overhead must fit the temporary pool.
- Source-image hash, exact preprocessing/weight contract, raw/NPY input consistency, finite float32 input/logits, valid NPY shape/order/dtype, and host top-5 consistency.
- Recorded hashes binding the export to `reference.json`, and the reference to `input.npy` and `host_logits.npy`. **Older exports missing these bindings remain blocked.** Re-export deliberately using step 6 (and re-export after modifying reference-generation/inspection code); do not invent hashes for old files to bypass the check. The current MobileNet artifacts are left unchanged by preflight.
- Conservative aligned storage: MRAM holds the PTE, float input, golden logits, firmware reservation, and other MRAM consumers. SRAM reserves separate input/output buffers, the **entire declared** planned and temporary pools, and other consumers. DTCM reserves method pool, heap, stack, and other CPU consumers. Separate I/O reservations may overcount tensors already planned by ExecuTorch; they intentionally do not assume aliasing/reuse.

The current MobileNet export is **3,835,520 bytes**, has **752,640 planned bytes**, and requests **1,505,280 scratch bytes**. The default-profile example remains blocked: the model alone exceeds 2 MiB; with the example's image/golden/code reservations MRAM requires 4,703,776 bytes. Even extending upwards from the existing HP base to the application-area end at `0x80580000` would provide only 3,670,016 bytes, insufficient for the model alone. Explicitly selecting the audited larger profile (and using `--mram-bytes 5238784`) accommodates this planning budget. The [separate stored-image builder](../firmware/stored_image/README.md) selects that profile explicitly and additionally checks the actual linked image.

Exit code **0** means `fits: true` / `status: fits_declared_budgets`; **1** means blocked. `firmware_link_verified` and `hardware_verified` are always **false**. A fit is only a budget estimate: exact section placement, NPU reachability, real CPU/temporary allocation high-water marks, and numerical accuracy still require linker and board validation. Hashes establish artifact identity/consistency, not independent reproduction of calibration or host inference.

Use a dedicated JSON report path, not an input artifact. Once CLI arguments are parsed and the output is validated, any previous report is invalidated before checks, and the new report is published atomically. Missing/malformed budget values and artifact failures publish `fits: false`, not a stale successful result. Always check the command's exit code; CLI usage/output-path errors can prevent report publication. The old `--runtime-bytes` option is intentionally replaced by explicit `--firmware-mram-bytes` and `--method-pool-bytes` reservations.

## Tests

The standalone stored-image hardware milestone is now accepted from [user-supplied reset/cold-boot logs](stored-image-hardware-acceptance.md). The next [camera/display coexistence gate](camera-inference-audit.md) has a separate build and must obtain its own hardware acceptance; preflight success never confers that result.

Lightweight safety/header tests do not require PyTorch or a board:

```bash
python3 -m unittest discover -s tests -v
```

The tests check environment isolation, bounded build parallelism, source-revision safeguards, low-disk checks, explicit image requirements, hashing, and generated C++ headers. They do not prove model/runtime builds or hardware behavior.

## Before firmware integration

1. Require successful host setup, smoke export, and Cortex-M55 runtime build. Save the delegation report and manifests.
2. Build the separate stored-input CMSIS target with `python3 -m tools.smoke_firmware --jobs 1`, leaving the working Viewfinder target intact. See its [build and hardware-acceptance instructions](../firmware/smoke/README.md).
3. Use one compatible Ethos-U driver, initialize the required NPU clocks/power/address mapping through verified E8 APIs, and provide real timing and bounded completion waits.
4. Use matching input/output metadata and compare fixed-input device outputs against host/reference results, accounting for quantization differences.
5. Review the linker map and NPU-accessible memory before allocating any pools. Viewfinder already reserves substantial SRAM; the legacy runner's roughly 7.6 MiB of pools cannot simply be added.
6. Establish a working output path. UART and RTT are different; J-Link core attachment is not currently verified.
7. Program the board only as an explicit next action, then record successful inference and actual NPU activity. Only afterward merge with the camera loop.

### E8 SDK findings to retain during firmware work

See the [larger standalone HP MRAM audit](hp-mram-layout-audit.md) for the opt-in profile beginning at `0x80001000`, now implemented and clean-built by the [stored-image target](../firmware/stored_image/README.md), but **not programmed or hardware-verified**. The default preflight profile remains limited to 2 MiB. The audit includes the SETOOLS lower-address/NTOC restrictions, SDK MPU dependency, reserved boot tail, and conditions for reclaiming default HE space.

The installed Ensemble 2.2.0 example at `Boards/DevKit-e8/Layers/M55_HP/ethos_setup.c` selects `NPU_HG_BASE` for U85 and supplies `IRQ366_Handler` with NVIC interrupt 366. This conflicts with the legacy root runner's assertion that the HG NPU has no IRQ to M55 HP. Hardware routing still needs verification, but do not copy that assertion or the two simultaneous polling paths as established facts.

The SDK's `linker_gnu_mram.ld.src` places general read-only data in MRAM and supports `.bss.at_sram0` / `.bss.at_sram1`. A new firmware target must select and inspect its actual linker layout: CPU-local TCM is not interchangeable with NPU-accessible bulk SRAM, and model bytes must remain reachable by the NPU. These are source-level findings, not a board execution result.

References: [Arm E8 image classification](https://learn.arm.com/learning-paths/embedded-and-microcontrollers/alif-image-classification/), [ExecuTorch Arm backend](https://docs.pytorch.org/executorch/stable/embedded-arm-ethos-u.html).
