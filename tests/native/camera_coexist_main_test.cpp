#include "preview.h"
#include "smoke_platform.h"

#include <cassert>
#include <string>

int coexist_main();
static std::string scenario;
static unsigned frame_calls, frames, npu_calls, method_calls, passes, exports;
static uint32_t scanlines;
static uint32_t now = 0xfffffc00u; // Settle timing must survive millisecond wrap.
struct Finished {};
extern "C" { volatile SmokeStatus g_smoke_status{}; }

bool smoke_board_init() { return scenario != "board-error"; }
bool preview_init() { return scenario != "preview-init-error"; }
bool preview_frame() {
    ++frame_calls;
    if ((scenario == "pre-frame-error" && frame_calls == 1) ||
        (scenario == "post-frame-error" && frame_calls == 4) ||
        (scenario == "settle-error" && frame_calls == 6) || frame_calls == 10) { return false; }
    ++frames;
    scanlines += 2;
    now += 1000;
    return true;
}
uint32_t preview_frames() { return frames; }
uint32_t preview_scanlines() { return scanlines; }
bool smoke_npu_init() { ++npu_calls; return scenario != "npu-error"; }
bool run_stored_image() {
    ++method_calls;
    assert(frames == 3);
    if (scenario == "image-error") { g_smoke_status.error = 23; return false; }
    if (scenario != "no-display-events") { ++scanlines; }
    if (scenario == "uart-error") { g_smoke_status.uart_timeouts = 1; }
    g_smoke_status.elapsed_ms = 19;
    g_smoke_status.npu_irqs = g_smoke_status.npu_jobs_started = g_smoke_status.npu_jobs_finished = 1;
    g_smoke_status.checked_values = 1000;
    return true;
}
// Boot-triggered export: exactly one attempt per verified self-test, with
// frame captured after two seconds of settling, with no UART receive helper.
bool camera_export_frame() {
    assert(passes == 1 && frames == 7);
    ++exports;
    if (scenario == "capture-timeout") { ++g_smoke_status.uart_timeouts; return false; }
    return scenario != "capture-error";
}
void smoke_set_result(bool passed) {
    if (passed) { ++passes; assert(frames == 5 && method_calls == 1); now = 0xfffffc00u; }
}
uint32_t smoke_millis() { return now; }
[[noreturn]] void smoke_idle() { throw Finished{}; }

int main(int argc, char** argv) {
    assert(argc == 2);
    scenario = argv[1];
    try { coexist_main(); assert(false); } catch (const Finished&) {}
    const bool selftest_ok = scenario == "success" || scenario == "capture-error" ||
                             scenario == "capture-timeout" || scenario == "settle-error";
    assert(passes == (selftest_ok ? 1u : 0u));
    if (scenario == "board-error") { assert(g_smoke_status.error == 200 && frame_calls == 0); }
    if (scenario == "preview-init-error") { assert(g_smoke_status.error == 201 && frame_calls == 0); }
    if (scenario == "pre-frame-error") { assert(g_smoke_status.error == 202 && npu_calls == 0); }
    if (scenario == "npu-error") { assert(g_smoke_status.error == 203 && method_calls == 0); }
    if (scenario == "image-error") { assert(g_smoke_status.error == 23 && frame_calls == 3); }
    if (scenario == "no-display-events") { assert(g_smoke_status.error == 205 && frame_calls == 3); }
    if (scenario == "post-frame-error") { assert(g_smoke_status.error == 206 && frame_calls == 4); }
    if (scenario == "uart-error") { assert(g_smoke_status.error == 207 && exports == 0); }
    if (scenario == "success") { assert(g_smoke_status.error == 208 && frame_calls == 10 && exports == 1); }
    // A failed boot export is tolerated and cannot end the boot as fail(210).
    if (scenario == "capture-error" || scenario == "capture-timeout") {
        assert(g_smoke_status.error == 208 && frame_calls == 10 && exports == 1);
    }
    if (scenario == "capture-timeout") { assert(g_smoke_status.uart_timeouts == 1); }
    if (scenario == "settle-error") { assert(g_smoke_status.error == 208 && exports == 0); }
}
