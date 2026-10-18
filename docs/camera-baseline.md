# Working camera/display baseline

## Verified scope

The user confirmed that the Viewfinder camera preview works on the attached display after selecting **MT9M114 instead of OV5675**. Build and programming logs were also supplied. This is a camera/display baseline, not evidence of model inference, NPU execution, touch handling, or cold-power-cycle reliability.

| Item | Baseline |
|---|---|
| Board | Alif Ensemble E8 DevKit, `AE822FA0E5597LS0`, Rev A0 |
| CMSIS target / build | `DevKit-E8-HP` / `debug` |
| Core | Cortex-M55 HP |
| Camera marking | `MT9M114-105 AP-Vision` (user identification) |
| Camera component | `AlifSemiconductor::BSP:External peripherals:CAMERA Sensor MT9M114` |
| Display | Attached panel works with the example's `ILI9806E LCD panel` component; physical panel marking not recorded |
| Output | Live camera preview; application UART defaults to 115200 baud, 8-N-1, no flow control |
| Programming | VS Code **Program with Security Toolkit** task; SETOOLS identified the board through `/dev/ttyACM0` during bring-up |

The earlier setup notes identify J16 as the underside camera connector. Retain the working wiring; power off before changing any ribbon or connector. Serial device names can change after reconnection. The programming port has not been confirmed as the application UART output path.

## Source and licensing

- Upstream: <https://github.com/alifsemi/alif_M55-viewfinder>
- Fork: <https://github.com/tatinCode/alif_M55-viewfinder>
- Upstream starting revision: `3c6144110c9ffd69911429e9cb8208d43b31a740`.
- HandGPT pins the configured fork revision through its Git submodule entry. Run `git submodule status --recursive` from the HandGPT root to see the exact checked-out revisions.

Baseline configuration commit: `82ade9d` (`Preserve DevKit-E8-HP MT9M114 preview configuration`). It saves the MT9M114 board component, the active DevKit-E8-HP target, and the already tracked generated component header. Local J-Link experiments, absolute-path editor settings, and generated tools-environment metadata are not part of this baseline.

Keep the vendor's `License.txt` and source copyright notices. The covered Alif code is restricted to Alif hardware; dependencies have their own applicable licenses. This baseline does not relicense vendor code.

## Recorded tool versions

These are from the supplied build logs, checked-in pack resolution, and local environment metadata, not a claim that every supported version has been tested.

| Tool / pack | Recorded version |
|---|---|
| Host | Arch Linux |
| Arm GNU compiler | 13.3.1 |
| VS Code CMSIS extension | 1.72.0 |
| CMSIS-Toolbox | Environment metadata reports 2.15.0; generated header reports csolution `2.15.1+p3-gf46d68bf` |
| CMake | 3.31.12 |
| Ninja | Environment metadata identifies 1.13.2 |
| `AlifSemiconductor::Ensemble` | 2.2.0 |
| `AlifSemiconductor::Dave2DDriver` | 2.0.2 |
| `AlifSemiconductor::AIPL` | 1.3.5 |
| `ARM::CMSIS` | 6.1.0 |
| SETOOLS | Earlier bring-up notes record 1.112.00; installed at `/home/tatin/alif/app-release-exec-linux` on this host |
| J-Link Commander | 9.86; successful core attachment is **not** part of this baseline |

## Reproduce the preview

1. From the HandGPT root, initialize dependencies:

   ```bash
   git submodule update --init --recursive
   code firmware/vendor/alif_viewfinder
   ```

2. Activate the Arm tools environment and open `alif.csolution.yml` in CMSIS Solution. Select **DevKit-E8-HP**, build type **debug**, not AppKit or HE.
3. Confirm `board/devkit-e8/board.clayer.yml` selects **only MT9M114** among the camera components. Keep the existing E8 ISP/MIPI settings. Do not substitute the OV5675 default.
4. Install the exact solution packs and build. The expected context is `viewfinder.debug+DevKit-E8-HP`, producing:

   ```text
   out/viewfinder/DevKit-E8-HP/debug/viewfinder.elf
   out/viewfinder/DevKit-E8-HP/debug/viewfinder.bin
   ```

5. Set `alif.setools.root` in VS Code **User Settings** to the local SETOOLS directory. On the bring-up host it is `/home/tatin/alif/app-release-exec-linux`; do not copy that host-specific path to another machine blindly.
6. With the board connected, intentionally run **Terminal → Run Task → Program with Security Toolkit** (or its **select COM port** variant). This task writes the application and boot table. If prompted, select the board's current SETOOLS serial port.
7. Require a successful task result and completed application/boot-table transfers. After the task finishes, reset the board if needed. Move a hand in front of the lens and confirm the preview updates.

The example initializes the camera before the display. A blank display can therefore be caused by a camera initialization failure. Do not assume that successful programming proves the application started.

## Local build check and artifact identity

The agent reran `cbuild alif.csolution.yml --target all --active DevKit-E8-HP` using the recorded Arm tools and pack root. It succeeded under GCC 13.3.1 with `ninja: no work to do`; this checks the current incremental build, not a fresh checkout or a hardware retest. No board programming was performed by the agent.

The local artifacts present at that check had these SHA-256 hashes (relative to the Viewfinder directory):

```text
0001b6cf3897331aa2ea56397371a0e3e044586f86a67b7417dba90026aec60a  out/viewfinder/DevKit-E8-HP/debug/viewfinder.bin
f0ff555846155ef47747857d25decf0bf0f23bcd529dbe29af5086db8175c9d6  out/viewfinder/DevKit-E8-HP/debug/viewfinder.elf
```

These identify the existing local files; they do not prove which bytes are flashed on the board. Regenerated ELF hashes can differ with build paths or tool versions. Build outputs remain outside Git.

## Bring-up evidence and cautions

- Supplied logs show the correct DevKit-E8-HP context linked successfully under GCC 13.3.1.
- Supplied SETOOLS logs identify `AE822FA0E5597LS0`, Rev A0, and report completed application and `AppTocPackage.bin` transfers (`100%`, `Done`).
- Those supplied logs were from before the camera-component correction; they are retained as programming-path evidence, not the MT9M114 binary's identity or memory measurement. After the correction, the user reported a working preview. A post-correction programming log and measured memory report remain to be recorded.
- The logo's discarded-`const` warning did not prevent linking. It has not been fixed as part of preserving the baseline.
- The earlier build reserved the full DTCM region and substantial SRAM0/SRAM1. Inspect the current linker map before adding inference; do not add the root runner's large pools without a shared memory budget.
- A full host disk previously caused a partial Ensemble pack installation. Check free space if pack sources are missing. The solution needs AIPL **1.3.5**, while the example's first-time installation task names **1.3.3**; use the solution's pinned requirements.
- JTAG attachment previously failed with `TDO is constant high`; earlier SWD attempts also failed core discovery. Do not treat local J-Link launch/task edits as a verified flashing workflow.
- Avoid **CMSIS Erase**, debug-stub installation, system-package updates, and discovery commands that write the boot table when merely checking connectivity. Use the verified application-programming task deliberately.

## Baseline acceptance checklist

- [x] Correct camera identified and MT9M114 component selected.
- [x] User reports a working camera-to-screen preview.
- [ ] Repeat the preview from a fresh checkout of the pinned fork revision.
- [ ] Confirm it starts after a cold power cycle without an attached debugger.
- [ ] Record the post-correction build/programming log and current memory report.
- [ ] Record resolution, output pixel format, orientation/mirroring, and usable hand distance.
- [ ] Verify UART logging and record its physical/host connection.
- [ ] Photograph the working board, camera, and panel markings without recording people without consent.

Next milestone: validate known-good inference on a stored image, then run that same inference alongside the working preview. No model runtime or handshape classifier is added by this baseline.
