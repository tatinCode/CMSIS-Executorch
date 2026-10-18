#include "smoke_platform.h"

#include <cstdio>

[[noreturn]] static void finish(bool passed) {
    smoke_set_result(passed);
    // An earlier I/O error must not suppress this final best-effort diagnostic.
    // A genuine UART failure remains an error; _write never pretends it worked.
    std::clearerr(stdout);
    std::printf("SMOKE %s error=%lu elapsed_ms=%lu IRQs=%lu jobs=%lu/%lu "
                "checked=%lu max_error_millionths=%lu timeouts=%lu uart_timeouts=%lu\n",
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
    if (!smoke_board_init()) {
        g_smoke_status.error = 1;
        finish(false);
    }
    std::printf("\nHandGPT E8-HP smoke test (not camera firmware)\n");
    std::printf("status=%p; UART=115200 8N1; U85 IRQ=366\n",
                static_cast<const void*>(const_cast<const SmokeStatus*>(&g_smoke_status)));
    if (!smoke_npu_init()) {
        g_smoke_status.error = 2;
        finish(false);
    }
    finish(run_smoke_model());
}
