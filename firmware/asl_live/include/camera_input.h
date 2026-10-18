#pragma once

#include "camera_resize.h"

namespace handgpt {
constexpr size_t kAslInputElements = 3 * kInputSide * kInputSide;

// Camera ownership must already be acquired by full MI frame end, pipeline
// Stop and cache invalidation. Use the ORIGINAL RGB planes, never the rotated
// LCD or RGB565 pixels. Saved v4 PNGs and ML training inputs remain upright.
inline bool asl_camera_input(const uint8_t* rgb, size_t rgb_bytes, float* output, size_t elements) {
    if (!rgb || !output || rgb_bytes != 3 * kCameraSide * kCameraSide || elements != kAslInputElements) {
        return false;
    }
    constexpr float mean[] = {0.485f, 0.456f, 0.406f};
    constexpr float deviation[] = {0.229f, 0.224f, 0.225f};
    uint8_t row[kInputSide];
    for (size_t plane = 0; plane < 3; ++plane) {
        for (size_t y = 0; y < kInputSide; ++y) {
            camera_resize_row(rgb + plane * kCameraSide * kCameraSide, y, row);
            for (size_t x = 0; x < kInputSide; ++x) {
                output[plane * kInputSide * kInputSide + y * kInputSide + x] =
                    (static_cast<float>(row[x]) / 255.0f - mean[plane]) / deviation[plane];
            }
        }
    }
    return true;
}
} // namespace handgpt
