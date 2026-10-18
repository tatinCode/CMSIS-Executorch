// Derived from Alif Viewfinder power_management/power_management.c.
/* Copyright (C) 2024 Alif Semiconductor - All Rights Reserved.
 * Use, distribution and modification of this code is permitted under the
 * Alif Semiconductor Software License Agreement: https://alifsemi.com/license
 */
#include "preview.h"
#include "services_lib_api.h"
#include "services_lib_bare_metal.h"

extern "C" uint32_t se_services_s_handle;

bool preview_power_init() {
    // Preserve existing resources, explicitly retain HP NPU and both SRAM banks.
    // Apply BEFORE UART/SysTick initialisation (see generated platform port).
    run_profile_t profile{};
    uint32_t service_error = UINT32_MAX;
    if (SERVICES_get_run_cfg(se_services_s_handle, &profile, &service_error) != 0 || service_error != 0) {
        return false;
    }
    profile.power_domains |= PD_VBAT_AON_MASK | PD_SSE700_AON_MASK | PD_SYST_MASK | PD_SESS_MASK;
    profile.memory_blocks |= SERAM_MASK | SRAM0_MASK | SRAM1_MASK | MRAM_MASK | FWRAM_MASK;
    profile.ip_clock_gating |= CAMERA_MASK | MIPI_DSI_MASK | MIPI_CSI_MASK | CDC200_MASK | NPU_HP_MASK;
    profile.phy_pwr_gating |= LDO_PHY_MASK | MIPI_TX_DPHY_MASK | MIPI_RX_DPHY_MASK | MIPI_PLL_DPHY_MASK;
    profile.dcdc_mode = DCDC_MODE_PWM;
    profile.dcdc_voltage = 825;
    profile.aon_clk_src = CLK_SRC_LFXO;
    profile.run_clk_src = CLK_SRC_PLL;
    profile.scaled_clk_freq = SCALED_FREQ_XO_HIGH_DIV_38_4_MHZ;
    profile.cpu_clk_freq = CLOCK_FREQUENCY_400MHZ;
    profile.vdd_ioflex_3V3 = IOFLEX_LEVEL_1V8;
    service_error = UINT32_MAX;
    // No off-profile/deep-sleep support: do not inherit the vendor's old VTOR.
    return SERVICES_set_run_cfg(se_services_s_handle, &profile, &service_error) == 0 && service_error == 0;
}
