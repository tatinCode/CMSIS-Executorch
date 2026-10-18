#pragma once
#include <stdint.h>
#define PD_VBAT_AON_MASK (1u << 0)
#define PD_SSE700_AON_MASK (1u << 1)
#define PD_SYST_MASK (1u << 2)
#define PD_SESS_MASK (1u << 3)
#define SERAM_MASK (1u << 0)
#define SRAM0_MASK (1u << 1)
#define SRAM1_MASK (1u << 2)
#define MRAM_MASK (1u << 3)
#define FWRAM_MASK (1u << 4)
#define CAMERA_MASK (1u << 0)
#define MIPI_DSI_MASK (1u << 1)
#define MIPI_CSI_MASK (1u << 2)
#define CDC200_MASK (1u << 3)
#define NPU_HP_MASK (1u << 4)
#define LDO_PHY_MASK (1u << 0)
#define MIPI_TX_DPHY_MASK (1u << 1)
#define MIPI_RX_DPHY_MASK (1u << 2)
#define MIPI_PLL_DPHY_MASK (1u << 3)
#define DCDC_MODE_PWM 1
#define CLK_SRC_LFXO 2
#define CLK_SRC_PLL 3
#define SCALED_FREQ_XO_HIGH_DIV_38_4_MHZ 4
#define CLOCK_FREQUENCY_400MHZ 5
#define IOFLEX_LEVEL_1V8 6
struct run_profile_t {
    uint32_t power_domains, memory_blocks, ip_clock_gating, phy_pwr_gating;
    uint32_t dcdc_mode, dcdc_voltage, aon_clk_src, run_clk_src;
    uint32_t scaled_clk_freq, cpu_clk_freq, vdd_ioflex_3V3;
};
extern "C" {
uint32_t SERVICES_get_run_cfg(uint32_t, run_profile_t*, uint32_t*);
uint32_t SERVICES_set_run_cfg(uint32_t, run_profile_t*, uint32_t*);
}
