#include "preview.h"
#include "live_asl.h"
#include "smoke_platform.h"
#include "image_config.h"

#include <cstdio>

[[noreturn]] static void fail(uint32_t code, bool selftest = false) {
    if (!g_smoke_status.error) { g_smoke_status.error = code; }
    if (selftest) { live_asl_selftest_failure(g_smoke_status.error); }
    else { live_asl_blank_display(); }
    smoke_set_result(false);
    std::printf("LIVE ASL FAIL error=%lu frames=%lu\n", static_cast<unsigned long>(g_smoke_status.error),
                static_cast<unsigned long>(preview_frames()));
    smoke_idle();
}

int main() {
    if (!smoke_board_init()) { fail(300); }
    std::printf("\nHandGPT LIVE ASL EXPERIMENTAL; classes=%s; threshold=0.80\n", handgpt::kAslClassOrder);
    std::printf("model_sha256=%s input_sha256=%s; LCD rotated 180; ML input NOT rotated\n",
                IMAGE_MODEL_SHA256, IMAGE_INPUT_SHA256);
    if (!preview_init()) { fail(301); }
    for (unsigned i = 0; i < 3; ++i) { if (!preview_frame()) { fail(302); } }
    if (!smoke_npu_init()) { fail(303); }
    const uint32_t before = preview_scanlines();
    if (!live_asl_init() || preview_scanlines() == before) { fail(304, true); }
    smoke_set_result(true);
    std::printf("LIVE ASL START; real camera predictions only; no boot frame export; experimental accuracy\n");
    uint32_t last = smoke_millis();
    while (true) {
        if (!preview_frame()) { fail(305); }
        if (static_cast<uint32_t>(smoke_millis() - last) >= 1000) {
            last = smoke_millis();
            const auto value = live_asl_letter(last);
            std::clearerr(stdout);
            std::printf("LIVE ASL frames=%lu predictions=%lu display=%s latency_ms=%lu fault=%u IRQs=%lu jobs=%lu/%lu\n",
                static_cast<unsigned long>(preview_frames()), static_cast<unsigned long>(live_asl_predictions()),
                handgpt::valid_letter(value) ? handgpt::kAslNames[static_cast<unsigned>(value)] : "--",
                static_cast<unsigned long>(live_asl_latency()),
                live_asl_faulted() ? 1u : 0u, static_cast<unsigned long>(g_smoke_status.npu_irqs),
                static_cast<unsigned long>(g_smoke_status.npu_jobs_started),
                static_cast<unsigned long>(g_smoke_status.npu_jobs_finished));
        }
    }
}
