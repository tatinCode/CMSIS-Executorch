// Driver sequence adapted from Alif Viewfinder camera/camera.c and display/disp.c.
/* Copyright (C) 2024 Alif Semiconductor - All Rights Reserved.
 * Use, distribution and modification of this code is permitted under the
 * Alif Semiconductor Software License Agreement: https://alifsemi.com/license
 */
#include "preview.h"
#include "preview_pixels.h"
#include "smoke_platform.h"
#include "RTE_Components.h"
#include CMSIS_device_header
#include "RTE_Device.h"
#include "Driver_CPI.h"
#include "Driver_ISP.h"
#include "Driver_CDC200.h"
#include "vsi_comm_video.h"
extern "C" {
#include "isp_calibration.h"
extern ARM_DRIVER_CPI Driver_CPI;
extern ARM_DRIVER_CDC200 Driver_CDC200;
}

#include <cstdio>
#include <cstring>

static_assert(RTE_ISP == 1 && RTE_CPI_AXI_PORT == 0 && RTE_ISP_BUFFER_COUNT == 1,
              "This target is the single-buffer ISP path only");
static_assert(RTE_ISP_OUTPUT_FORMAT == 39 && RTE_ISP_OUTPUT_WIDTH == 480 && RTE_ISP_OUTPUT_HEIGHT == 480,
              "Expected baseline 480x480 planar RGB888");
static_assert(RTE_CDC200_PIXEL_FORMAT == 2 && RTE_PANEL_HACTIVE_TIME == 480 && RTE_PANEL_VACTIVE_LINE == 800,
              "Expected baseline 480x800 RGB565 panel");

extern "C" {
__attribute__((section(".bss.handgpt_preview"), aligned(32))) uint8_t g_preview_rgb[480 * 480 * 3];
__attribute__((section(".bss.handgpt_preview"), aligned(32))) uint16_t g_preview_lcd[2][800 * 480];
}

namespace {
constexpr uint32_t kCaptureLimitMs = 5000;
constexpr uint32_t kDisplayLimitMs = 1000;
constexpr uint32_t kCameraErrors = ARM_CPI_EVENT_ERR_HARDWARE | ARM_CPI_EVENT_MIPI_CSI2_ERROR |
    ARM_CPI_EVENT_ERR_CAMERA_INPUT_FIFO_OVERRUN | ARM_CPI_EVENT_ERR_CAMERA_OUTPUT_FIFO_OVERRUN |
    ARM_ISP_EVENT_SIZE_ERR_DETECTED | ARM_ISP_EVENT_DATALOSS_DETECTED;
volatile uint32_t camera_events;
volatile uint32_t scanlines;
volatile bool display_error;
uint32_t frames;
unsigned active_lcd;
bool ready;
VIDEO_BUF_S queued;

void camera_callback(uint32_t events) { camera_events |= events; }
void display_callback(uint32_t events) {
    if (events & ARM_CDC_DSI_ERROR_EVENT) { display_error = true; }
    if (events & ARM_CDC_SCANLINE0_EVENT) { ++scanlines; }
}
bool check(int32_t result, const char* operation) {
    if (result == ARM_DRIVER_OK) { return true; }
    std::printf("COEXIST driver failure: %s result=%ld\n", operation, static_cast<long>(result));
    return false;
}
void clean(void* pointer, size_t bytes) {
    SCB_CleanDCache_by_Addr(pointer, static_cast<int32_t>(bytes));
    __DSB();
}
bool capture() {
    // CPU relinquishes this complete cache-line-isolated allocation before DMA.
    SCB_CleanInvalidateDCache_by_Addr(g_preview_rgb, sizeof(g_preview_rgb));
    __DSB();
    camera_events = 0;
    queued = {};
    queued.index = 0;
    queued.numPlanes = 3;
    queued.imageSize = sizeof(g_preview_rgb);
    for (unsigned i = 0; i < 3; ++i) { queued.planes[i].dmaPhyAddr = reinterpret_cast<vsi_dma_t>(g_preview_rgb + i * 480 * 480); }
    if (!check(Driver_CPI.Control(ISP_CONTROL_QBUF, reinterpret_cast<uintptr_t>(&queued)), "ISP QBUF") ||
        !check(Driver_CPI.Control(CPI_EVENTS_CONFIGURE, ARM_CPI_EVENT_CAMERA_CAPTURE_STOPPED | kCameraErrors), "CPI events") ||
        !check(Driver_CPI.CaptureFrame(reinterpret_cast<void*>(0xABCDABCD)), "capture")) { return false; }
    const uint32_t start = smoke_millis();
    while (!(camera_events & (ARM_ISP_MI_EVENT_MP_FRAME_END_DETECTED | kCameraErrors))) {
        if (handgpt::elapsed(start, smoke_millis(), kCaptureLimitMs)) {
            std::printf("COEXIST camera timeout events=0x%lx\n", static_cast<unsigned long>(camera_events));
            Driver_CPI.Stop(); // Best effort only; the caller will not read/reuse the buffer.
            return false;
        }
        __WFI();
    }
    bool ok = (camera_events & kCameraErrors) == 0;
    // Process only a COMPLETE MI frame, never FILL/WRAP notifications.
    if (ok) { ok = check(Driver_CPI.Control(ISP_PROCESS_FRAME_END, 0), "ISP frame end"); }
    // Ensemble 2.2.0's CMSIS MI ISR only clears/reports the interrupt; it does
    // not move the active buffer into the library's completed VB queue.
    // ISP_PROCESS_FRAME_END processes statistics, not that queue. DQBUF is
    // therefore not a completion/ownership primitive on this single-buffer
    // path. We know the sole queued allocation and require a successful full
    // pipeline Stop before any CPU read (also resets the VB lists for QBUF on
    // the next capture). Do not paper over a failed DQBUF with a retry loop.
    // Stop sensor + CSI + ISP + CPI before granting CPU ownership. Display DMA
    // continues scanning its separate LCD allocation throughout inference.
    const bool stopped = check(Driver_CPI.Stop(), "camera stop");
    if (!ok || !stopped || (camera_events & kCameraErrors)) {
        std::printf("COEXIST camera rejected events=0x%lx processed=%u stopped=%u\n",
                    static_cast<unsigned long>(camera_events), static_cast<unsigned>(ok), static_cast<unsigned>(stopped));
        return false;
    }
    SCB_InvalidateDCache_by_Addr(g_preview_rgb, sizeof(g_preview_rgb));
    __DSB();
#if RTE_ISP_AE_MODULE
    isp_ae_cached_values ae{};
    if (!check(Driver_CPI.Control(ISP_CONTROL_AE_GET_CACHED, reinterpret_cast<uintptr_t>(&ae)), "AE cached values")) { return false; }
    if (ae.int_line) {
        const uint64_t gain = static_cast<uint64_t>(ae.again) * ae.dgain / 16u;
        if (gain > UINT32_MAX ||
            !check(Driver_CPI.Control(CPI_ISP_CAMERA_SENSOR_EXPOSURE, ae.int_line), "AE exposure") ||
            !check(Driver_CPI.Control(CPI_ISP_CAMERA_SENSOR_GAIN, static_cast<uint32_t>(gain)), "AE gain")) { return false; }
    }
#endif
    return true;
}
} // namespace

uint32_t preview_frames() { return frames; }
uint32_t preview_scanlines() { return scanlines; }

bool preview_init() {
    if (ready) { return false; } // Never zero buffers belonging to active scanout.
    // The SE run profile has now powered SRAM1. Do not depend on contents left
    // by another firmware or access this bank from the startup zero table.
    std::memset(g_preview_rgb, 0, sizeof(g_preview_rgb));
    std::memset(g_preview_lcd, 0, sizeof(g_preview_lcd));
    isp_set_user_configuration();
    isp_param_set_square_crop();
    if (!check(Driver_CPI.Initialize(camera_callback), "camera init") ||
        !check(Driver_CPI.PowerControl(ARM_POWER_FULL), "camera power") ||
        !check(Driver_CPI.Control(CPI_CONFIGURE, 0), "CPI configure") ||
        !check(Driver_CPI.Control(CPI_CAMERA_SENSOR_CONFIGURE, 0), "sensor configure")) { return false; }
    clean(g_preview_lcd, sizeof(g_preview_lcd));
    if (!check(Driver_CDC200.Initialize(display_callback), "display init") ||
        !check(Driver_CDC200.PowerControl(ARM_POWER_FULL), "display power") ||
        !check(Driver_CDC200.Control(CDC200_CONFIGURE_DISPLAY, reinterpret_cast<uintptr_t>(g_preview_lcd[0])), "display configure") ||
        !check(Driver_CDC200.Control(CDC200_SCANLINE0_EVENT, 1), "display scanline event") ||
        !check(Driver_CDC200.Start(), "display start")) { return false; }
    std::printf("COEXIST preview configured: RGB888p 480x480 -> RGB565 480x800; CPU copy, no GPU/graphics heap\n");
    std::printf("COEXIST camera ownership: full MI frame-end + pipeline Stop; single known buffer, no DQBUF\n");
    ready = true;
    return true;
}

bool preview_frame() {
    if (!ready || display_error || !capture()) { return false; }
    const unsigned next = active_lcd ^ 1u;
    if (!handgpt::preview_rgb565(g_preview_rgb, sizeof(g_preview_rgb), 480, 480, g_preview_lcd[next], 480, 800)) { return false; }
    clean(g_preview_lcd[next], sizeof(g_preview_lcd[next]));
    if (!check(Driver_CDC200.Control(CDC200_FRAMEBUF_UPDATE_VSYNC, reinterpret_cast<uintptr_t>(g_preview_lcd[next])), "display flip")) { return false; }
    const uint32_t before = scanlines;
    const uint32_t start = smoke_millis();
    // Two complete scanline events after the reload request are deliberately
    // conservative: even a request racing one edge must cross the next VSYNC.
    while (static_cast<uint32_t>(scanlines - before) < 2u) {
        if (display_error || handgpt::elapsed(start, smoke_millis(), kDisplayLimitMs)) {
            std::printf("COEXIST display error/timeout\n");
            return false;
        }
        __WFI();
    }
    active_lcd = next;
    ++frames;
    return !display_error && g_smoke_status.uart_timeouts == 0;
}
