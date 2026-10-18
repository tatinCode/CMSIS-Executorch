#pragma once

#include <stddef.h>
#include <stdint.h>

enum class SmokeStage : uint32_t {
    Boot, BoardReady, RuntimeReady, NpuReady, Loading, Executing, Pass, Fail, Fault
};

// Intentionally readable without UART via a debugger or a memory dump.
struct SmokeStatus {
    uint32_t magic;
    uint32_t version;
    SmokeStage stage;
    uint32_t error;
    uint32_t elapsed_ms;
    uint32_t npu_irqs;
    uint32_t npu_jobs_started;
    uint32_t npu_jobs_finished;
    uint32_t semaphore_timeouts;
    uint32_t uart_timeouts;
    uint32_t hardware_macs;
    uint32_t checked_values;
    uint32_t max_error_millionths;
    uint32_t fault_cfsr;
    uint32_t fault_hfsr;
};
static_assert(sizeof(SmokeStatus) == 60, "Keep the diagnostic record ABI stable");

extern "C" volatile SmokeStatus g_smoke_status;

bool smoke_board_init();
bool smoke_npu_init();
uint32_t smoke_millis();
void smoke_set_result(bool passed);
[[noreturn]] void smoke_idle();
bool run_smoke_model();
