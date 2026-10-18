// Reuse the verified UART/NPU/cache port without changing the smoke project.
#include "smoke_platform.h"
#include "image_config.h"

#include <cstdio>

bool run_stored_image();

[[noreturn]] static void finish(bool passed) {
    smoke_set_result(passed);
    std::clearerr(stdout);
    std::printf("IMAGE %s error=%lu elapsed_ms=%lu IRQs=%lu jobs=%lu/%lu checked=%lu "
                "max_error_millionths=%lu timeouts=%lu uart_timeouts=%lu\n",
                passed ? "PASS" : "FAIL",
                static_cast<unsigned long>(g_smoke_status.error),
                static_cast<unsigned long>(g_smoke_status.elapsed_ms),
                static_cast<unsigned long>(g_smoke_status.npu_irqs),
                static_cast<unsigned long>(g_smoke_status.npu_jobs_started),
                static_cast<unsigned long>(g_smoke_status.npu_jobs_finished),
                static_cast<unsigned long>(g_smoke_status.checked_values),
                static_cast<unsigned long>(g_smoke_status.max_error_millionths),
                static_cast<unsigned long>(g_smoke_status.semaphore_timeouts),
                static_cast<unsigned long>(g_smoke_status.uart_timeouts));
    smoke_idle();
}

int main() {
    if (!smoke_board_init()) { g_smoke_status.error = 1; finish(false); }
    std::printf("\nHandGPT stored-image MobileNetV2 (not ASL or camera firmware)\n");
    std::printf("layout=hp-only-large base=0x80001000 tail=0x80500000 status=%p\n",
                static_cast<const void*>(const_cast<const SmokeStatus*>(&g_smoke_status)));
    std::printf("model_sha256=%s\ninput_sha256=%s\n", IMAGE_MODEL_SHA256, IMAGE_INPUT_SHA256);
    std::printf("comparison: max_abs_logit_error<=%f cosine>=%f and matching top1\n",
                static_cast<double>(IMAGE_MAX_LOGIT_ERROR), static_cast<double>(IMAGE_MIN_COSINE));
    if (!smoke_npu_init()) { g_smoke_status.error = 2; finish(false); }
    finish(run_stored_image());
}
