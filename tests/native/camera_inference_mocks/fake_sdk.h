#pragma once
#include <stddef.h>
#include <stdint.h>

#define CMSIS_device_header "fake_sdk.h"
#define RTE_ISP 1
#define RTE_CPI_AXI_PORT 0
#define RTE_ISP_BUFFER_COUNT 1
#define RTE_ISP_OUTPUT_FORMAT 39
#define RTE_ISP_OUTPUT_WIDTH 480
#define RTE_ISP_OUTPUT_HEIGHT 480
#define RTE_CDC200_PIXEL_FORMAT 2
#define RTE_PANEL_HACTIVE_TIME 480
#define RTE_PANEL_VACTIVE_LINE 800
#define RTE_ISP_AE_MODULE 1
#define ARM_DRIVER_OK 0
#define ARM_POWER_FULL 2

#define CPI_CONFIGURE 1
#define CPI_CAMERA_SENSOR_CONFIGURE 2
#define CPI_EVENTS_CONFIGURE 3
#define CPI_ISP_CAMERA_SENSOR_EXPOSURE 4
#define CPI_ISP_CAMERA_SENSOR_GAIN 5
#define ISP_CONTROL_QBUF 0x10
#define ISP_CONTROL_DQBUF 0x11
#define ISP_PROCESS_FRAME_END 0x12
#define ISP_CONTROL_AE_GET_CACHED 0x15
#define CDC200_CONFIGURE_DISPLAY 1
#define CDC200_SCANLINE0_EVENT 8
#define CDC200_FRAMEBUF_UPDATE_VSYNC 4
#define ARM_CDC_DSI_ERROR_EVENT 1
#define ARM_CDC_SCANLINE0_EVENT 2
#define ARM_CPI_EVENT_CAMERA_CAPTURE_STOPPED (1u << 0)
#define ARM_CPI_EVENT_ERR_HARDWARE (1u << 1)
#define ARM_CPI_EVENT_MIPI_CSI2_ERROR (1u << 2)
#define ARM_CPI_EVENT_ERR_CAMERA_INPUT_FIFO_OVERRUN (1u << 3)
#define ARM_CPI_EVENT_ERR_CAMERA_OUTPUT_FIFO_OVERRUN (1u << 4)
#define ARM_ISP_EVENT_AWB_DONE (1u << 11)
#define ARM_ISP_EVENT_SIZE_ERR_DETECTED (1u << 12)
#define ARM_ISP_EVENT_DATALOSS_DETECTED (1u << 13)
#define ARM_ISP_MI_EVENT_MP_FRAME_END_DETECTED (1u << 14)
#define ARM_ISP_MI_EVENT_FILL_MP_Y_DETECTED (1u << 16)
#define ARM_ISP_MI_EVENT_MP_Y_WRAP_DETECTED (1u << 17)

using vsi_dma_t = uintptr_t;
struct VIDEO_BUF_S {
    unsigned index;
    unsigned numPlanes;
    unsigned imageSize;
    struct { vsi_dma_t dmaPhyAddr; } planes[3];
};
struct isp_ae_cached_values { uint32_t int_line, again, dgain; };
using Callback = void (*)(uint32_t);
struct ARM_DRIVER_CPI {
    int32_t (*Initialize)(Callback);
    int32_t (*PowerControl)(int);
    int32_t (*Control)(uint32_t, uintptr_t);
    int32_t (*CaptureFrame)(void*);
    int32_t (*Stop)();
};
struct ARM_DRIVER_CDC200 {
    int32_t (*Initialize)(Callback);
    int32_t (*PowerControl)(int);
    int32_t (*Control)(uint32_t, uintptr_t);
    int32_t (*Start)();
};
extern "C" {
extern ARM_DRIVER_CPI Driver_CPI;
extern ARM_DRIVER_CDC200 Driver_CDC200;
void isp_set_user_configuration();
void isp_param_set_square_crop();
}
void fake_idle();
void fake_clean(void*, size_t);
void fake_invalidate(void*, size_t);
void fake_clean_invalidate(void*, size_t);
inline void __WFI() { fake_idle(); }
inline void __DSB() {}
inline void SCB_CleanDCache_by_Addr(void* p, int32_t n) { fake_clean(p, n); }
inline void SCB_InvalidateDCache_by_Addr(void* p, int32_t n) { fake_invalidate(p, n); }
inline void SCB_CleanInvalidateDCache_by_Addr(void* p, int32_t n) { fake_clean_invalidate(p, n); }
