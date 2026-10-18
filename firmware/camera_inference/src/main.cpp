#include "preview.h"
#include "frame_export.h"
#include "smoke_platform.h"
#include "image_config.h"

#include <cstdio>

bool run_stored_image();

[[noreturn]] static void fail(uint32_t error) {
    if (g_smoke_status.error == 0) { g_smoke_status.error = error; }
    smoke_set_result(false);
    std::clearerr(stdout);
    std::printf("COEXIST FAIL error=%lu frames=%lu scanlines=%lu uart_timeouts=%lu\n",
                static_cast<unsigned long>(g_smoke_status.error), static_cast<unsigned long>(preview_frames()),
                static_cast<unsigned long>(preview_scanlines()), static_cast<unsigned long>(g_smoke_status.uart_timeouts));
    smoke_idle();
}

int main() {
    if (!smoke_board_init()) { fail(200); }
    std::printf("\nHandGPT camera/display + STORED-image self-test (NOT camera classification)\n");
    std::printf("layout=hp-only-large model_sha256=%s input_sha256=%s\n", IMAGE_MODEL_SHA256, IMAGE_INPUT_SHA256);
    if (!preview_init()) { fail(201); }
    for (unsigned i = 0; i < 3; ++i) { if (!preview_frame()) { fail(202); } }
    std::printf("COEXIST preview before self-test: frames=%lu; camera paused, display scanning\n",
                static_cast<unsigned long>(preview_frames()));
    if (!smoke_npu_init()) { fail(203); }
    const uint32_t before = preview_scanlines();
    if (!run_stored_image()) { fail(204); }
    const uint32_t during = preview_scanlines() - before;
    // Require the display to continue scanning while the model is loaded/run;
    // do not claim simultaneous camera DMA or continuous NPU classification.
    if (during == 0) { fail(205); }
    for (unsigned i = 0; i < 2; ++i) { if (!preview_frame()) { fail(206); } }
    if (g_smoke_status.uart_timeouts) { fail(207); }
    smoke_set_result(true);
    std::printf("COEXIST SELFTEST PASS frames=%lu display_events_during_test=%lu elapsed_ms=%lu IRQs=%lu jobs=%lu/%lu checked=%lu\n",
                static_cast<unsigned long>(preview_frames()), static_cast<unsigned long>(during),
                static_cast<unsigned long>(g_smoke_status.elapsed_ms), static_cast<unsigned long>(g_smoke_status.npu_irqs),
                static_cast<unsigned long>(g_smoke_status.npu_jobs_started), static_cast<unsigned long>(g_smoke_status.npu_jobs_finished),
                static_cast<unsigned long>(g_smoke_status.checked_values));
    if (g_smoke_status.uart_timeouts) { fail(207); }
    // One-way transport: the host listens before the user resets the board.
    // Earlier capture attempts stalled; their precise fault is not established.
    // Removing RX avoids that dependency without changing the proven TX port.
    std::printf("CAPTURE READY v=4; full-view PIL-bilinear 128x128 export; settle_ms=2000; send nothing\n");
    const uint32_t settle = smoke_millis();
    do {
        if (!preview_frame()) { fail(208); }
    } while (static_cast<uint32_t>(smoke_millis() - settle) < 2000);
    // No new camera DMA during export. The LCD scans the final settled frame.
    const bool exported = camera_export_frame();
    // Export/TX failure must not kill preview or erase measured timeout counts.
    // Clear newlib's stream error so later best-effort diagnostics can recover.
    std::clearerr(stdout);
    std::printf(exported ? "CAPTURE DONE v=4; preview resuming\n" :
                          "CAPTURE EXPORT LOST v=4; preview resuming; reset to retry\n");
    uint32_t last = smoke_millis();
    while (true) {
        if (!preview_frame()) { fail(208); }
        if (smoke_millis() - last >= 1000) {
            last = smoke_millis();
            std::clearerr(stdout);
            std::printf("PREVIEW frames=%lu scanlines=%lu (boot export finished; not live classification)\n",
                        static_cast<unsigned long>(preview_frames()), static_cast<unsigned long>(preview_scanlines()));
            std::printf("CAPTURE IDLE v=4; press the board reset for another export\n");
        }
    }
}
