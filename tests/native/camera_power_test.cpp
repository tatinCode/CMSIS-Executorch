#include "fake_power_sdk.h"
#include "preview.h"
#include <cassert>
#include <string>

extern "C" { uint32_t se_services_s_handle = 123; }
static std::string scenario;
static unsigned sets;
extern "C" uint32_t SERVICES_get_run_cfg(uint32_t handle, run_profile_t* profile, uint32_t* error) {
    assert(handle == 123);
    profile->power_domains = profile->memory_blocks = profile->ip_clock_gating = profile->phy_pwr_gating = 0x80000000;
    *error = scenario == "get-service-error" ? 1 : 0;
    return scenario == "get-transport-error" ? 1 : 0;
}
extern "C" uint32_t SERVICES_set_run_cfg(uint32_t handle, run_profile_t* profile, uint32_t* error) {
    ++sets;
    assert(handle == 123);
    assert((profile->memory_blocks & (SRAM0_MASK | SRAM1_MASK | MRAM_MASK)) == (SRAM0_MASK | SRAM1_MASK | MRAM_MASK));
    assert((profile->ip_clock_gating & (CAMERA_MASK | CDC200_MASK | NPU_HP_MASK)) == (CAMERA_MASK | CDC200_MASK | NPU_HP_MASK));
    assert((profile->power_domains & 0x80000000) && (profile->memory_blocks & 0x80000000) &&
           (profile->ip_clock_gating & 0x80000000) && (profile->phy_pwr_gating & 0x80000000));
    assert(profile->cpu_clk_freq == CLOCK_FREQUENCY_400MHZ && profile->dcdc_voltage == 825);
    *error = scenario == "set-service-error" ? 1 : 0;
    return scenario == "set-transport-error" ? 1 : 0;
}
int main(int argc, char** argv) {
    assert(argc == 2);
    scenario = argv[1];
    assert(preview_power_init() == (scenario == "success"));
    assert(sets == ((scenario == "get-service-error" || scenario == "get-transport-error") ? 0u : 1u));
}
