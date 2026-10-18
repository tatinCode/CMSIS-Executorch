# Stored-image MobileNetV2 hardware acceptance

The user supplied two matching UART results: an initial reset run and a reported full-power-cycle repeat with SW4 U4. Both end with:

```text
IMAGE PASS error=0 elapsed_ms=19 IRQs=1 jobs=1/1 checked=1000 max_error_millionths=371032 timeouts=0 uart_timeouts=0
```

This is **user-supplied board acceptance evidence**, not an assistant-performed flash/readback or a claim that a fresh build has automatically been hardware-tested.

- Board/core: Ensemble E8, M55 HP only.
- Layout: `hp-only-large`, image base `0x80001000`, reserved tail starts `0x80500000`.
- NPU: base `0x49042000`, IRQ 366, architecture 2.0.0, 256 MACs.
- Model SHA-256: `5a856b85afbd490654f93982484190b7e18974f1ae5511975c332d80aeb3c033`.
- Preprocessed input SHA-256: `9e9206aeddbdeff2158a6d9ff7b53dd643184508842cf68bbd014d4f296e8f9a`.
- Associated reviewed host binary identity: `a896d5a06b57113373a6b67708c09ac5846e17ed4c7457ccfd1c73b742770924`. UART reports model/input hashes, not a full binary readback hash.
- Runtime measurement: **19 ms** for method execution including CPU/runtime/cache work, not an NPU-only cycle benchmark or camera pipeline FPS.

The supplied numerical/allocator output, identical across both reports:

```text
method_high_water=864/262144
temporary_high_water=1505280/1572864 method_high_water=864/262144 execute_error=0x0
comparison: finite=1 max_abs_error=0.371032 rmse=0.103616 cosine=0.994494 board_top1=478 host_top1=478
top1 index=478 label=carton logit=5.001952 host_logit=5.149241
top2 index=534 label=dishwasher logit=4.833347 host_logit=4.740320
top3 index=281 label=tabby logit=4.411834 host_logit=4.550530
top4 index=283 label=Persian cat logit=4.411834 host_logit=4.254138
top5 index=742 label=printer logit=4.271330 host_logit=4.381328
```

All 1,000 finite logits passed the predeclared maximum absolute error ≤ 1.0, cosine ≥ 0.98, and matching top-1 gates. Both runs reported one completed NPU job/IRQ and zero semaphore/UART timeouts. `carton` is the matching host/device prediction for the stored cat-on-cardboard photo, not a camera/ASL result. The observed method high-water mark alone does not justify shrinking pools or prove a future combined application fits.

The static-image hardware milestone is accepted for this reported artifact. Existing generated build manifests remain `hardware_verified: false`: this document records the separate user observation and does not grant hardware acceptance to later rebuilds or the new camera/inference integration target.
