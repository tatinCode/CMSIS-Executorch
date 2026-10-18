#pragma once

#include <cstddef>
#include <cstdint>

namespace handgpt {
// Rotate the COMPLETE composed panel, camera and text together, exactly once.
// Call only on the inactive LCD buffer after drawing and before cache clean /
// VSYNC submission. In-place reversal uses one pixel, no extra frame buffer.
inline bool rotate_lcd180(uint16_t* display, size_t pixels) {
    if (!display || pixels != 480 * 800) { return false; }
    for (size_t i = 0; i < pixels / 2; ++i) {
        const uint16_t value = display[i];
        display[i] = display[pixels - 1 - i];
        display[pixels - 1 - i] = value;
    }
    return true;
}

} // namespace handgpt
