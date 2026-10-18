#include "smoke_platform.h"

#include <cassert>
#include <cstdlib>
#include <cstring>

extern "C" {
volatile SmokeStatus g_smoke_status = {};
}

static bool model_ran;

static bool scenario(const char* name) {
    const char* selected = std::getenv("HANDGPT_TEST_SCENARIO");
    return selected && std::strcmp(selected, name) == 0;
}

bool smoke_board_init() { return !scenario("board_fail"); }
bool smoke_npu_init() { return !scenario("npu_fail"); }
bool run_smoke_model() {
    assert(!scenario("board_fail") && !scenario("npu_fail"));
    model_ran = true;
    if (scenario("inference_fail")) {
        g_smoke_status.error = 19;
        return false;
    }
    g_smoke_status.checked_values = 16;
    return true;
}
void smoke_set_result(bool passed) {
    g_smoke_status.stage = passed ? SmokeStage::Pass : SmokeStage::Fail;
}
[[noreturn]] void smoke_idle() {
    assert(model_ran == (!scenario("board_fail") && !scenario("npu_fail")));
    assert((g_smoke_status.stage == SmokeStage::Pass) == scenario("pass"));
    std::exit(0);
}
