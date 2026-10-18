# Frozen best-effort presentation candidate

The user explicitly accepts the current character roster and known accuracy
failures. Feature scope is frozen: **24 static letters** (A–Y except J/Z),
NO_HAND and OTHER, individual-letter display only. No words/name accumulation,
motion letters, threshold changes or additional model training in this freeze.

The chosen checkpoint is the completed replay-v3 float candidate, SHA-256
`0d3f831307bfcd19e8f7f7c4f201a2ba8c48a453576324c92345f59c1deb300d`.
No QAT checkpoint passed the accuracy floors; no such checkpoint is substituted.
Training includes the original 61 camera TRAIN + 90 reviewed continuous frames,
with Kaggle rehearsal. The original 42 development-validation images never
entered training or calibration. Eight letters remain camera-untrained.

## Accuracy is explicitly best-effort

Prior int8 evidence for this model: old development **13/30 correct displays**,
one wrong letter, zero negative false letters; **A 2/6, L 3/7, Y 3/6**. New camera
**TRAINING FIT ONLY: 89/90**; this is not fresh-pose accuracy. Kaggle validation
top-1 81.18%. Both quantization accuracy gates fail. These failures remain
recorded; the user accepts them for an experimental live trial, not as proof
of reliability. No unseen-person or full-alphabet camera accuracy is established.

The best-effort helper is separate from the strict replay helper, whose A/L/Y
checks still reject this model. Only the exact frozen checkpoint is accepted.
Numerical boot gate revision 2, threshold 0.80, model input orientation, LCD
rotation, IRQ 366, MRAM GOT and measured memory reservations are unchanged.

## Artifacts and offline workflow

- Export: `.work/asl/demo-export-v1/` (fresh export with the fixed finalizer).
- Firmware: `.work/inference/asl-demo-firmware-v2/` (clean screen, no footer).
- Package: `.work/inference/asl-demo-programming-v2/latest.json`.
- All previous live/collector/replay packages and pointers remain separate.

```bash
python3 -B -m tools.demo_asl build --experimental --layout hp-only-large \
  --accept-known-accuracy-limitations
python3 -B -m tools.demo_asl prepare --setools-root <installed-SETOOLS> \
  --experimental --hp-only-board
python3 -B -m tools.demo_asl review
```

## Deliberate user flash (only after package review succeeds)

Stop collector/serial monitors. Power off, SW4 -> SE, reconnect:

```bash
python3 -B -m tools.demo_asl program --port /dev/ttyACM0 --write
```

Type **`PROGRAM E8-HP BEST EFFORT ASL`**. On tool success, power off,
SW4 -> U4, reconnect. Then run:

```bash
.work/asl/venv/bin/python -B -m tools.observe_asl_live \
  --port /dev/ttyACM0 --seconds 30 --export .work/asl/demo-export-v1 \
  --output .work/inference/asl-live-acceptance/demo-boot01.log
```

After READ ONLY, press RESET once. Require three self-test repeats,
`ASL SELFTEST PASS checked=26 repeats=3`, `LIVE ASL START`, increasing predictions
and `fault=0`. Numerical failures are **not accepted** by this accuracy waiver.
Rehearse actual presentation poses and negatives, then repeat from cold boot.
Demonstrate only letters confirmed working live. No agent-run flashing occurs.

## Completed export and build

- Fresh PTE: **2,586,528 bytes**, SHA-256
  `c44c12657e3a4026f37f3e036d8e6ff879227b83e6f7391598636eedb1b07d19`.
- Firmware: **3,480,640 bytes**, SHA-256
  `7ef7211f6fd2fd40d2525f224b5a802f40be3e64bd34191b2ffdee525f1662c1`;
  MRAM headroom **1,758,144 bytes**. Memory reservations unchanged.
- Build records `known_accuracy_failures_accepted=true`; accuracy gates remain
  false in the export. A 2/6, L 3/7, Y 3/6 and new TRAIN fit 89/90 reconfirmed.
- **555 host tests pass**. All 2,934 previously protected files remain unchanged.

Package review **passed**: stage
`.work/inference/asl-demo-programming-v1/camera-inference-0vq72skq`.
Binary/package identity, HP-only layout/TOC/map/CRC and private staging checks
passed. Integrity receipt: `.work/inference/frozen-demo-integrity-v1.json`.
Previous packages/pointers remain unchanged. No agent serial access or writer
attempt occurred. Only the user's deliberate flash and hardware rehearsal remain.

### Clean-screen revision v2

Removed only the on-screen EXPERIMENTAL footer and renamed its remaining
failure-message glyph helper. SELFTEST FAIL diagnostics, UART qualifications,
accuracy documentation and numerical boot gates remain. All six generated
model/input/logits/class/gate/config headers are byte-identical to v1.

New binary: **3,480,480 bytes**, SHA-256
`224d4591a2086f47276a889661391677ac7a736a8e797e7d2d26fadba3ca9a04`.
Package review passed:
`.work/inference/asl-demo-programming-v2/camera-inference-776sx2ld`.
Memory reservations unchanged; v1 artifacts and pre-edit sources retained.

Working-tree tests: **554 pass, one unrelated local test errors** because
`tests/test_asl_model.py` contains stray text. That user edit is untouched and
must be excluded from the PR. With only that test module taken from HEAD in
memory, **all 555 tests pass**. No working-tree test file was restored/overwritten.
Evidence: `.work/inference/footer-removal-integrity-v2.json`.

No agent flashing or PR publication yet. Reflash with the same `tools.demo_asl`
command above, then confirm self-test/live/cold-boot behavior before opening PR.
