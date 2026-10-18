#pragma once

#include <stddef.h>
#include <stdint.h>

namespace handgpt {
// Explicit planar R, G, B input, RGB565 output. No inference preprocessing is
// performed here: this is only the display path for the coexistence test.
inline bool preview_rgb565(const uint8_t* rgb, size_t rgb_bytes, size_t width, size_t height,
                           uint16_t* display, size_t display_width, size_t display_height) {
    if (!rgb || !display || !width || !height || width > display_width || height > display_height ||
        width > SIZE_MAX / height || width * height > SIZE_MAX / 3 ||
        rgb_bytes != width * height * 3 || !display_height || display_width > SIZE_MAX / display_height ||
        display_width * display_height > SIZE_MAX / sizeof(uint16_t)) {
        return false;
    }
    const size_t plane = width * height;
    for (size_t y = 0; y < display_height; ++y) {
        for (size_t x = 0; x < display_width; ++x) {
            uint16_t pixel = 0;
            if (y < height && x < width) {
                const size_t i = y * width + x;
                pixel = static_cast<uint16_t>(((rgb[i] & 0xf8u) << 8) |
                    ((rgb[plane + i] & 0xfcu) << 3) | (rgb[2 * plane + i] >> 3));
            }
            display[y * display_width + x] = pixel;
        }
    }
    return true;
}

inline bool elapsed(uint32_t start, uint32_t now, uint32_t limit) {
    return static_cast<uint32_t>(now - start) >= limit;
}
} // namespace handgpt
