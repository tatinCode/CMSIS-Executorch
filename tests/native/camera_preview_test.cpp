#include "fake_sdk.h"
#include "preview.h"
#include "smoke_platform.h"

#include <cassert>
#include <cstring>
#include <string>

extern "C" {
volatile SmokeStatus g_smoke_status{};
extern uint8_t g_preview_rgb[480 * 480 * 3];
extern uint16_t g_preview_lcd[2][480 * 800];
}
static std::string scenario;
static Callback camera_cb, display_cb;
static uint32_t now, stops, flips, invalidations, clean_calls, queues, dequeues, capture_ticks;
static bool capturing, completed, processed, display_running, buffer_pending;
static VIDEO_BUF_S queued;
uint32_t smoke_millis() { return now; }

void fake_idle() {
    ++now;
    if (capturing && !completed) {
        ++capture_ticks;
        if (scenario == "capture-timeout") { return; }
        if (scenario == "fill-not-complete") { camera_cb(ARM_ISP_MI_EVENT_FILL_MP_Y_DETECTED); return; }
        if (scenario == "wrap-not-complete") { camera_cb(ARM_ISP_MI_EVENT_MP_Y_WRAP_DETECTED); return; }
        if (scenario == "capture-stopped-not-complete") { camera_cb(ARM_CPI_EVENT_CAMERA_CAPTURE_STOPPED); return; }
        if (scenario == "partial-then-complete" && capture_ticks == 1) {
            camera_cb(ARM_ISP_MI_EVENT_FILL_MP_Y_DETECTED | ARM_ISP_MI_EVENT_MP_Y_WRAP_DETECTED);
            return;
        }
        completed = true;
        uint32_t event = ARM_ISP_MI_EVENT_MP_FRAME_END_DETECTED | ARM_ISP_EVENT_AWB_DONE;
        if (scenario == "camera-error") { event |= ARM_ISP_EVENT_DATALOSS_DETECTED; }
        camera_cb(event); // Multiple simultaneous callback bits, not a switch enum.
    }
    if (display_running && flips && scenario != "display-timeout") {
        display_cb(scenario == "display-error" ? ARM_CDC_DSI_ERROR_EVENT : ARM_CDC_SCANLINE0_EVENT);
    }
}
void fake_clean(void*, size_t) { ++clean_calls; }
void fake_invalidate(void* pointer, size_t bytes) {
    assert(pointer == g_preview_rgb && bytes == sizeof(g_preview_rgb));
    assert(!capturing && completed && processed && stops && !buffer_pending);
    ++invalidations;
}
void fake_clean_invalidate(void* pointer, size_t bytes) {
    assert(pointer == g_preview_rgb && bytes == sizeof(g_preview_rgb) && !capturing);
}
extern "C" void isp_set_user_configuration() {}
extern "C" void isp_param_set_square_crop() {}

static int32_t camera_init(Callback cb) { camera_cb = cb; return scenario == "init-error" ? -1 : 0; }
static int32_t power(int) { return 0; }
static int32_t camera_control(uint32_t operation, uintptr_t argument) {
    if (operation == ISP_CONTROL_QBUF) {
        assert(!capturing && !buffer_pending);
        queued = *reinterpret_cast<VIDEO_BUF_S*>(argument);
        assert(queued.index == 0 && queued.numPlanes == 3 && queued.imageSize == sizeof(g_preview_rgb));
        for (unsigned i = 0; i < 3; ++i) {
            assert(queued.planes[i].dmaPhyAddr == reinterpret_cast<uintptr_t>(g_preview_rgb + i * 480 * 480));
        }
        ++queues;
        if (scenario != "queue-error") { buffer_pending = true; }
        return scenario == "queue-error" ? -1 : 0;
    }
    if (operation == ISP_PROCESS_FRAME_END) {
        assert(completed && capturing);
        if (scenario == "partial-then-complete") { assert(capture_ticks >= 2); }
        if (scenario == "process-error") { return -1; }
        processed = true;
        // Matches the pinned SDK: statistics processing does NOT populate a
        // completed VB queue. The old code would fail at DQBUF even on success.
    }
    if (operation == ISP_CONTROL_DQBUF) {
        ++dequeues;
        return -1;
    }
    if (operation == ISP_CONTROL_AE_GET_CACHED) {
        assert(!capturing);
        *reinterpret_cast<isp_ae_cached_values*>(argument) = {10, 16, 16};
        if (scenario == "gain-overflow") {
            *reinterpret_cast<isp_ae_cached_values*>(argument) = {10, UINT32_MAX, UINT32_MAX};
        }
    }
    if (operation == CPI_ISP_CAMERA_SENSOR_EXPOSURE) { assert(argument == 10); }
    if (operation == CPI_ISP_CAMERA_SENSOR_GAIN) { assert(argument == 16); }
    return 0;
}
static int32_t capture(void* dummy) {
    assert(dummy == reinterpret_cast<void*>(0xABCDABCD));
    assert(buffer_pending && !capturing);
    capturing = true;
    capture_ticks = 0;
    completed = processed = false;
    std::memset(g_preview_rgb, 0, sizeof(g_preview_rgb));
    std::memset(g_preview_rgb, 255, 480 * 480); // Red plane only.
    return 0;
}
static int32_t stop() {
    ++stops;
    if (scenario == "stop-error") { return -1; }
    capturing = false;
    buffer_pending = false; // SDK DisableChn -> VSI_VB_StreamOff clears VB lists.
    if (scenario == "late-camera-error") { camera_cb(ARM_ISP_EVENT_DATALOSS_DETECTED); }
    return 0;
}
static int32_t display_init(Callback cb) { display_cb = cb; return 0; }
static int32_t display_control(uint32_t operation, uintptr_t argument) {
    if (operation == CDC200_FRAMEBUF_UPDATE_VSYNC) {
        assert(!capturing && invalidations);
        const auto* pixels = reinterpret_cast<uint16_t*>(argument);
        assert(pixels == g_preview_lcd[(flips + 1) % 2]);
        assert(pixels[0] == 0xf800 && pixels[479 * 480 + 479] == 0xf800);
        assert(pixels[480 * 480] == 0 && pixels[800 * 480 - 1] == 0);
        ++flips;
    }
    return 0;
}
static int32_t start_display() { display_running = true; return 0; }
extern "C" {
ARM_DRIVER_CPI Driver_CPI = {camera_init, power, camera_control, capture, stop};
ARM_DRIVER_CDC200 Driver_CDC200 = {display_init, power, display_control, start_display};
}

int main(int argc, char** argv) {
    assert(argc == 2);
    scenario = argv[1];
    if (scenario == "not-initialized") { assert(!preview_frame() && preview_frames() == 0); return 0; }
    if (scenario == "init-error") { assert(!preview_init()); return 0; }
    assert(preview_init());
    assert(!preview_init()); // Reinitialisation must not clear active LCD DMA buffers.
    if (scenario == "success" || scenario == "partial-then-complete") {
        for (unsigned i = 0; i < 3; ++i) {
            const uint32_t previous = preview_scanlines();
            assert(preview_frame());
            assert(preview_scanlines() - previous >= 2);
        }
        assert(preview_frames() == 3 && stops == 3 && queues == 3 && dequeues == 0 && flips == 3 && invalidations == 3 && clean_calls >= 4);
    } else {
        assert(!preview_frame());
        assert(preview_frames() == 0);
        if (scenario == "capture-timeout" || scenario == "fill-not-complete" || scenario == "wrap-not-complete" ||
            scenario == "capture-stopped-not-complete") { assert(now >= 5000 && stops == 1 && invalidations == 0); }
        if (scenario == "display-timeout") { assert(now >= 1000 && invalidations == 1); }
        if (scenario == "camera-error" || scenario == "late-camera-error" || scenario == "process-error" || scenario == "stop-error") {
            assert(flips == 0 && invalidations == 0 && stops == 1);
        }
        if (scenario == "gain-overflow") { assert(flips == 0 && invalidations == 1 && stops == 1); }
    }
    assert(dequeues == 0);
}
