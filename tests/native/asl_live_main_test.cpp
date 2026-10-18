#include "live_asl.h"
#include "preview.h"
#include "smoke_platform.h"

#include <cassert>
#include <string>

int live_main();
extern "C" { volatile SmokeStatus g_smoke_status{}; }
static std::string scenario;
static unsigned frames, calls, npu_calls, selftests, passes, blanks, failure_panels;
static uint32_t scanlines, now;
struct Finished {};

bool smoke_board_init() { return scenario != "board-error"; }
bool preview_init() { return scenario != "init-error"; }
bool preview_frame() {
    ++calls;
    if ((scenario == "pre-frame-error" && calls == 1) ||
        (scenario == "post-frame-error" && calls == 4) || calls == 10) { return false; }
    ++frames;
    scanlines += 2;
    now += 1000;
    return true;
}
uint32_t preview_frames() { return frames; }
uint32_t preview_scanlines() { return scanlines; }
bool smoke_npu_init() { ++npu_calls; return scenario != "npu-error"; }
bool live_asl_init() {
    ++selftests;
    assert(frames == 3);
    if (scenario != "display-stalled") { ++scanlines; }
    if (scenario == "selftest-325") { g_smoke_status.error = 325; return false; }
    return scenario != "selftest-error";
}
bool live_asl_blank_display() { ++blanks; return true; }
bool live_asl_selftest_failure(uint32_t code) {
    ++failure_panels;
    assert(code == g_smoke_status.error);
    return true;
}
bool live_asl_ready() { return passes && !live_asl_faulted(); }
bool live_asl_faulted() { return scenario == "prediction-fault"; }
uint32_t live_asl_predictions() { return frames > 3 ? frames - 3 : 0; }
uint32_t live_asl_latency() { return 42; }
handgpt::AslLetter live_asl_letter(uint32_t) {
    return live_asl_faulted() ? handgpt::AslLetter::None : handgpt::AslLetter::A;
}
void smoke_set_result(bool passed) { if (passed) { ++passes; assert(frames == 3 && selftests == 1); } }
uint32_t smoke_millis() { return now; }
[[noreturn]] void smoke_idle() { throw Finished{}; }

int main(int argc, char** argv) {
    assert(argc == 2);
    scenario = argv[1];
    try { live_main(); assert(false); } catch (const Finished&) {}
    const bool selftest_failure = scenario == "selftest-error" || scenario == "display-stalled" || scenario == "selftest-325";
    assert(blanks == (selftest_failure ? 0u : 1u));
    assert(failure_panels == (selftest_failure ? 1u : 0u));
    const bool boot_pass = scenario == "success" || scenario == "prediction-fault" || scenario == "post-frame-error";
    assert(passes == (boot_pass ? 1u : 0u));
    if (scenario == "board-error") { assert(calls == 0 && g_smoke_status.error == 300); }
    if (scenario == "init-error") { assert(calls == 0 && g_smoke_status.error == 301); }
    if (scenario == "pre-frame-error") { assert(npu_calls == 0 && g_smoke_status.error == 302); }
    if (scenario == "npu-error") { assert(selftests == 0 && g_smoke_status.error == 303); }
    if (scenario == "selftest-error" || scenario == "display-stalled") { assert(calls == 3 && g_smoke_status.error == 304); }
    if (scenario == "selftest-325") { assert(calls == 3 && g_smoke_status.error == 325); }
    if (scenario == "success" || scenario == "prediction-fault") { assert(frames == 9 && g_smoke_status.error == 305); }
}
