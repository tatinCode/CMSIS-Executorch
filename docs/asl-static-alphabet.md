# Static alphabet expansion audit

## Scope and preservation

User-approved: 24 static letters (no J/Z), NO_HAND, OTHER. Kaggle J/Z/del/space
excluded entirely. Individual letters only; no word accumulation. Threshold
0.80 and the approved post-hoc revision-2 self-test gate stay unchanged.

Clean baseline: `279be0e`. Source archive and 13,756-file protection inventory:
`.work/inference/static-asl-v1-baseline/`. Preserve v5/v6 exports, firmware,
packages/pointers and logs. New v7 workspace and **separate** static package
pointer. No agent-run flash or serial access. Changed source intentionally
invalidates old helper reviews; use matching old source for old packages.

Legacy training/data class constants, camera capture, stored-image, TEST UI and
Viewfinder remain unchanged. Shared data functions gain backwards-compatible
optional class parameters; checkpoint loader adds explicit schema 3. New live
display is isolated from the preserved TEST UI.

## Evidence and limits

- Dataset: 75,000 decoded allowed images; zero exact duplicates; 11,756
  near-duplicate evaluation quarantines. Retained 55,000 train / 3,748
  development-validation / 4,496 dataset-test images. Filename blocks are not
  signer-independent partitions; dHash screening is heuristic.
- Camera: unchanged 61 reviewed train / 42 development validation records.
  **19 new letters have no reviewed E8-camera examples.**
- 26-output MobileNetV2, frozen backbone/BatchNorm, head-only fitting with
  Kaggle and reviewed camera TRAIN. Copy old head rows by **name**, not index.
- Domain/per-class reports kept separate; validation used for checkpoint
  selection. Dataset test evaluated after selection, not a final camera test.
- Tests: exact alphabet/order, every glyph/decision, negatives, calibration
  train membership, row remapping, invalid output counts, observer indices
  above nine, wrong/missing order, C++/Python gate agreement.

## Development candidate comparison

The first run (`static-train-v1`) used 50% camera sampling mass and 8,192
samples/epoch for 40 epochs. Selected epoch 8 (joint camera/Kaggle class-balanced
development loss). Float camera: **12/30 correct displays, one wrong, 0/12
negative false letters**; A 0/6, B 3/6, C 2/5, L 5/7, Y 2/6. Kaggle validation
top-1 83.13% (2,359 selected images). This is not an improvement over v6 and
demonstrates the accuracy cost of expansion; preserve rather than hide it.

A second development mix (`static-train-v2`) was compared: 25% camera
sampling mass, 16,384 samples/epoch, 40 epochs, otherwise identical source,
seed, data selections and frozen backbone. This keeps expected camera sampling
volume unchanged while giving the new letters more source-domain rehearsal.
The selected v2 checkpoint is epoch **17**, joint development loss **1.064258**
versus v1's **1.100886**. Float camera: **12/30 correct, three wrong, 0/12 negative
false letters**. A 0/6, B 2/6, C 1/5, L 6/7, Y 3/6. Thus the model is **not a
camera-accuracy improvement**, and A is still inadequate for reliable ANGELA.
Kaggle validation top-1: **87.16%** on 2,359 selected images; repeated dataset
test: **89.96%** on 2,430 images. Do not compare these with camera accuracy.

Selection uses **joint development-validation loss**, not dataset
test or imagined new-letter camera accuracy. Repeated dataset-test measurements
across development runs are not an untouched final result.

Selected checkpoint SHA-256:
`4f65df091f32855320ecff9791b67bb3ef660912d76a671789590ca88347e9fc`.
Backbone/BatchNorm tensors remained unchanged, checkpoint reload tensor/logit
delta **0.0**. Selection and exact invocation parameters are recorded in
`.work/inference/static-asl-development-selection-v1.json`.

Host suite currently passes **381 tests**; additional isolated export-environment
tests pass. Preserved capture and TEST UI package reviews also pass offline.
All 24 static glyphs/indices/panel bounds/stability also pass ASan and UBSan.
## Completed int8 export

`.work/asl/static-export-v1/manifest.json` binds the selected v2 checkpoint and
all calibration/evaluation sources. Calibration: **61 reviewed camera TRAIN +
250 selected Kaggle TRAIN** images, all 26 classes covered, no validation/test
observer updates. Serialized reload logit delta **0.0** on both validation domains.

- PTE: **2,586,528 bytes**, SHA-256
  `7317eb7670e5d10db00b0b5684cdfd4002e346ee915877584d317c8570728400`.
- Exactly one Ethos-U delegate; only existing CPU quantize/dequantize boundary
  kernels. Input float32 `[1,3,128,128]`, output float32 `[1,26]`.
- Arena: **245,760 / 752,640** required/reserved; NPU scratch **491,520**;
  temporary required/reserved **557,056 / 1,572,864**; method pool **262,144**.
- Graph/PTE output scale **0.21448995172977448**. Unchanged eight-step policy
  therefore gives max absolute error **1.7159196138381958**, cosine >=0.999,
  identical top-1 and 0.80 display decision. The absolute bound changes with the
  model scale; the approved number of steps is not increased.

| Development domain | Float | PT2E int8 simulation |
|---|---:|---:|
| Camera correct letter displays / 30 | 12 | **11** |
| Camera wrong letters | 3 | **2** |
| Camera negative false letters / 12 | 0 | **0** |
| Kaggle raw top-1 / 2,359 images | 87.16% | **86.01%** |
| Kaggle correct letter displays / 2,312 | 1,736 | **1,725** |
| Kaggle wrong letters | 50 | **52** |

**Both domain nonregression gates failed.** Camera PT2E per-class correct
displays: A 0/6, B 3/6, C 1/5, L 4/7, Y 3/6. E/G/N Kaggle development displays:
79/100, 80/100, 80/100 respectively, but **zero camera examples**. This is an
experimental expanded alphabet, not a model that can reliably spell ANGELA.
Passing the future device numerical test will not change that accuracy evidence.

## Completed build and offline package review

- Workspace: `.work/inference/asl-live-firmware-v7/manifest.json`, one-job build.
- Binary: **3,480,640 bytes**, SHA-256
  `cca23b50e3eec71c19cb562ce61b43056ecaea6ece8ae8f82c0e9bea8cedc2ad`.
- MRAM `[0x80001000, 0x80352c40)`, **1,758,144 bytes** headroom. SRAM1
  reservation **2,227,200**, heap **131,072**, stack **32,768** unchanged.
- Fresh serialized PTE inspection, ARM ABI, compiled sources, memory/vector/GOT
  layout and actual embedded model/input/padded-logit bytes all revalidated.
  Generated class/gate headers match the export exactly.
- Separate package stage: `.work/inference/asl-static-programming-v1/`
  `camera-inference-wu9kif07`. HP-only DEVICE + USER_APP, package tail
  `[0x8057f0d0, 0x80580000)` (**3,888 bytes**), binary TOC/CRC verified.
  Stage mode 0700; private preparation log mode 0600. Writer review passes.
- Final host suite: **381 passed**. Static display also passes ASan/UBSan;
  export-environment tests pass. Capture and TEST UI fallbacks pass their own
  offline reviews; Viewfinder clean; `git diff --check` clean.
- Integrity report `.work/inference/static-asl-integrity-v1.json`: **13,752
  protected files unchanged**, four intentional legacy ML source/documentation
  changes recorded. Every original `.work/` artifact, including v6 package and
  old latest pointer, remains unchanged. Revalidated 103 camera records and
  independently compared all **312 frozen backbone/BatchNorm tensors**.

**No serial port opened, no writer attempt, no static-26 hardware acceptance.**
Require three 26-logit self-test repeats, progressing live predictions, all-letter
screen checks and cold boot after a deliberate user flash. Numerical PASS alone
cannot override the failed accuracy/nonregression evidence above.
