#include "frame_export.h"
#include "camera_resize.h"
#include "preview.h"
#include "smoke_platform.h"

#include <cstdio>

extern "C" uint8_t g_preview_rgb[480 * 480 * 3];

namespace {
// Eight output rows per write; reuse this bounded stack scratch for CRC and TX.
// Resize in two passes over the frozen source, never modifying the camera/LCD
// allocations or adding another frame-sized buffer to the memory reservations.
constexpr size_t kChunkRows = 8;
void resize_chunk(size_t plane, size_t y, uint8_t* chunk) {
    const uint8_t* source = g_preview_rgb + plane * 480 * 480;
    for (size_t row = 0; row < kChunkRows; ++row) {
        handgpt::camera_resize_row(source, y + row, chunk + row * handgpt::kInputSide);
    }
}
} // namespace

bool camera_export_frame() {
    // Foreground only. preview_frame() has completed full MI frame end, pipeline
    // Stop and cache invalidation. No new capture is started until this returns.
    // The separate LCD buffer keeps scanning the same frozen picture.
    if (preview_frames() == 0 || g_smoke_status.uart_timeouts != 0) { return false; }
    const uint32_t frame = preview_frames();
    uint8_t chunk[kChunkRows * handgpt::kInputSide];
    uint32_t crc = 0xffffffffu;
    for (size_t plane = 0; plane < 3; ++plane) {
        for (size_t y = 0; y < handgpt::kInputSide; y += kChunkRows) {
            resize_chunk(plane, y, chunk);
            crc = handgpt::frame_crc32_update(crc, chunk, sizeof(chunk));
        }
    }
    crc ^= 0xffffffffu;
    if (std::printf("HGFRAME 2 128 128 RGB888P 49152 %lu %08lx\n",
                    static_cast<unsigned long>(frame), static_cast<unsigned long>(crc)) < 0) { return false; }
    // 1024 bytes takes ~89 ms at 115200 8N1, within the platform's 1 s TX bound.
    // No log may be interleaved with this length-delimited binary payload.
    for (size_t plane = 0; plane < 3; ++plane) {
        for (size_t y = 0; y < handgpt::kInputSide; y += kChunkRows) {
            resize_chunk(plane, y, chunk);
            if (std::fwrite(chunk, 1, sizeof(chunk), stdout) != sizeof(chunk) ||
                g_smoke_status.uart_timeouts != 0) { return false; }
        }
    }
    return std::printf("\nHGFRAME END %lu %08lx\n", static_cast<unsigned long>(frame),
                       static_cast<unsigned long>(crc)) > 0 && g_smoke_status.uart_timeouts == 0;
}
