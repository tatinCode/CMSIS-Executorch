# Larger standalone E8-HP MRAM layout audit

## Outcome

**Audit follow-up:** the [separate stored-image target](../firmware/stored_image/README.md) implements this opt-in profile and has clean-built successfully. Actual image size is 4,653,520 bytes, with 585,264 bytes margin before the reserved tail. Model/reference/pool placement, vectors, and startup tables are checked against the built binary. The user confirmed **HandGPT, HP only**, with no HE/A32 firmware or reclaimed MRAM data to preserve. Its isolated boot package was prepared/reviewed, and the user subsequently supplied matching MobileNet PASS logs on reset and a cold-boot repeat; see [hardware acceptance](stored-image-hardware-acceptance.md). The assistant did not flash the board. Prior acceptance is not authorization for a future write.

**Original audit result:** source-level feasibility established. The existing MobileNetV2 export can fit a separate HP-only image rebased to `0x80001000`, with an executable/model region ending at `0x80500000`. This reclaims default HE application space and part of the default user area. It is not a safe modification for a board that must retain an independent HE/A32 image or other data in those ranges.

The arithmetic has margin, confirmed by the stored-image link, isolated package review, and user-reported standalone hardware acceptance. The preflight continues to reject this model under its **default 2 MiB profile**; the larger profile requires explicit selection. The verified smoke target/package, Viewfinder, installed SDK, and installed SETOOLS configuration remain unchanged. The new [camera/inference coexistence target](camera-inference-audit.md) is a separate unprogrammed build, not covered by that acceptance.

## Evidence reviewed

- Generated smoke `RTE/Device/AE822FA0E5597LS0_M55_HP/app_mem_regions.h`: HP starts at `0x80200000`; the code region ends at `APP_MRAM_USER_BASE = 0x80400000`.
- Ensemble 2.2.0 `Device/core/rtss_hp/linker/linker_gnu_mram.ld.src`: MRAM origin is `APP_MRAM_HP_BASE`, and length is `APP_CODE_MRAM_SIZE`, **not merely `APP_MRAM_HP_SIZE`**. Changing only the nominal HP-size define would not enlarge the actual linker region.
- Ensemble 2.2.0 `Device/core/common/source/mpu.c`: executable/read-only MRAM runs from **`APP_MRAM_HE_BASE`** to `APP_MRAM_USER_BASE - 1`; the user region is writable/device, execute-never. The HE-base define must therefore remain the MRAM lower bound even when no HE application is allocated.
- Ensemble 2.2.0 `Device/core/common/source/system.c`: VTOR is assigned the linked `__VECTOR_TABLE`; this is not hard-coded to the old HP origin. The GNU startup/vector section has 2048-byte alignment.
- Installed SETOOLS 1.112.00 `utils/devicesDB.db`: the configured E8 part has `app_size = 0x580000` and `alif_offset = 0x580000`. Application storage is below `0x80580000`; do not extend the user image into the system-package area.
- Installed **AUGD0005 v1.112.0** user guide, printed pages **15, 16, 34–35**: fixed-address images are 16-byte-aligned; Rev Ax `mramAddress` placement starts at `0x80000100`; `cpu_id` selects the application core, and `boot` enables XIP. An ATOC takes precedence over NTOC; without ATOC, valid vectors at `0x80000000` can start **HE**, not HP. This is why the proposal does not put HP vectors at `0x80000000` and must retain a valid HP ATOC.
- `.work/inference/programming/latest.json` currently identifies `smoke-gaqvbxd4/receipt.json`. Its package map has only `DEVICE` and HP `USER_APP` entries. Its programming log records successful writes of the smoke image at `0x80200000` and ATOC at `0x8057f0d0`.
- `.work/inference/preflight/mv2-current.json`: freshly inspected PTE, reference artifacts, and explicit planning reservations. Model SHA-256: `5a856b85afbd490654f93982484190b7e18974f1ae5511975c332d80aeb3c033`.

The installed toolkit also contains generic example configurations that boot HE or multiple cores. **Those example files are not evidence that those images are currently booted.** The actual recorded smoke package is HP-only. Conversely, a host-side package log is not a complete readback/inventory of the board's MRAM or running cores. No such readback was performed by this audit.

## Candidate regions — end addresses exclusive

| Range | Size | Proposed use |
|---|---:|---|
| `0x80000000`–`0x80001000` | 4,096 bytes | Reserved; no new image writes. Avoid the fixed-address lower limit and introducing HP vectors at the NTOC address. Existing contents are **not** assumed empty. |
| `0x80001000`–`0x80500000` | 5,238,784 bytes | One standalone HP firmware/model/input image; vectors at `0x80001000`. |
| `0x80500000`–`0x80580000` | 524,288 bytes | Reserved user/device/ATOC tail; not ordinary code/model capacity. |
| At/above `0x80580000` | Not allocated here | System-package space; no writes or updates authorized. |

This is slightly less than 5 MiB of HP capacity because the first 4 KiB stays reserved. Both the proposed origin and tail boundary satisfy vector/MPU alignment. The recorded 3,888-byte smoke ATOC/device package occupies `[0x8057f0d0, 0x80580000)`, fully within the proposed tail. A **new** candidate package must be generated and checked: its exact size/address are not assumed from the old package.

The fresh stored-image package has now independently passed those checks: the HP image occupies `[0x80001000, 0x804711d0)`, and its new device/certificate/ATOC package occupies `[0x8057f0d0, 0x80580000)` (3,888 bytes). Both its configuration and binary TOC contain only DEVICE + USER_APP (HP). The new helper checks all contiguous map extents, binary TOC entries/tail/CRC, staged identities, and the embedded model/input/golden bytes. It offers **prepare/review only**, never serial access or a writer. Its private receipt lives under `.work/inference/stored-image-programming/`, separate from smoke; it records preservation confirmation but keeps programming authorization false.

Only the separate target's generated configuration would change:

```text
APP_MRAM_HE_BASE   = 0x80000000   # retain: SDK MPU uses it as the MRAM lower bound
APP_MRAM_HE_SIZE   = 0            # no independent HE application allocation
APP_MRAM_HP_BASE   = 0x80001000
APP_MRAM_HP_SIZE   = 0x004FF000
APP_MRAM_USER_BASE = 0x80500000
APP_MRAM_USER_SIZE = 0x00080000
APP_CODE_MRAM_SIZE = APP_MRAM_USER_BASE - APP_MRAM_HP_BASE
```

These values are now applied **only to the separate stored-image generated target**, not smoke or Viewfinder. The future candidate ATOC must select `M55_HP`, name the new image, and use `mramAddress = 0x80001000`; it must not boot HE/A32. The GNU vector placement, reset entry, MSP, MPU ranges, model extent, data load images, startup copy/zero tables, and boot package must all agree on that profile. The CPU-only method pool stays in DTCM; planned arena and actual delegate temporary/scratch pool stay in SRAM0 as in the corrected smoke target.

## Budget calculation

Using the conservative reservations from the existing preflight example:

| MRAM consumer | Bytes |
|---|---:|
| MobileNetV2 PTE | 3,835,520 |
| Embedded float32 image | 602,112 |
| Embedded golden logits | 4,000 |
| Firmware/other read-only reservation | 262,144 |
| **Estimated image total** | **4,703,776** |

- Candidate capacity: **5,238,784 bytes**.
- Estimated remaining margin: **535,008 bytes**.
- Estimated image end: **`0x8047d620`**, below the `0x80500000` tail boundary.
- Retaining HP origin `0x80200000` and merely extending upward to the application-area end would provide **3,670,016 bytes**, insufficient even for the PTE alone and leaving no explicit tail reservation.

The 262,144-byte firmware reservation is **not a measured MobileNet firmware size**. A successful final link and its loadable MRAM extent must fit the candidate region before any programming package can be accepted.

The reference SRAM estimate reserves separate I/O buffers conservatively, a 752,640-byte planned arena, and a 1,572,864-byte temporary pool (covering 1,505,280 actual scratch bytes plus 65,536 declared overhead bytes). Total SRAM0 reservations are **2,931,616 bytes**, leaving **1,262,688 bytes** within SRAM0. DTCM reservations total **442,368 bytes**, leaving **606,208 bytes**. These CPU/overhead estimates need real allocation/linker checks; they do not establish room for camera/display coexistence. SRAM1 is not counted as automatically usable spare capacity.

## Conditions before implementation/programming

1. Treat the candidate as **HP-only and opt-in**. Do not relax the existing smoke/preflight/programming address checks globally. A larger target needs its own explicit layout profile and identity.
2. Before programming the reclaimed space, confirm the board is dedicated to this HP experiment and that no HE/A32 application or other MRAM data there must survive. If preservation is uncertain, establish an appropriate backup/readback procedure first; do not erase or use discovery/debug-stub/system-package writes to investigate.
3. Deliberately regenerate the model/reference export with the newly hardened hash bindings. The old export remains inspectable but cannot pass the new integrity gate. Do not hand-fill missing hashes to bypass it.
4. Build a **separate stored-image CMSIS target** and inspect its actual ELF/map/bin. Require full model/input/code extent below `0x80500000`, vectors/reset at the candidate base, no overlaps, valid MPU configuration, and all actual NPU scratch in SRAM0. Check real method/temporary allocations, not just the 96-byte smoke arena or the MobileNet planned arena.
5. Generate a fresh package using an isolated SETOOLS workspace. Verify E8 Rev A0, a sole HP application image at `0x80001000`, only the intended boot entries, and the complete device/certificate/ATOC package inside the reserved tail. No device write occurs during generation.
6. Keep known smoke and Viewfinder binaries/configurations available as fallback. A later candidate write **would overwrite their on-board HP range and much of the default HE range**; separate source projects do not make these flash images coexist. Restoring a fallback requires deliberately programming its image and its matching boot table.
7. Only then request a separate, explicit programming action. Do not erase the ATOC as a shortcut: existing vectors in the untouched first 4 KiB could select NTOC HE fallback. Successful HP boot/UART, NPU job/IRQ evidence, quantized-vs-host numerical comparison, and cold-boot repeatability are still required.

## Next step

The standalone target/package and guarded programmer are implemented, with user-supplied UART/cold-boot acceptance recorded separately. Next is the [camera/display coexistence gate](camera-inference-audit.md), which builds offline and now has its own package/review helper and confirmation-gated programmer. Its hardware acceptance remains outstanding. Existing standalone programmers must not be repurposed for it. This audit is not permission to write automatically.
