#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace handgpt {
// Fixed full-field 480 -> 128 antialiased bilinear resize for 8-bit RGB planes.
// Match PIL's horizontal-then-vertical filtering, including 8-bit rounding
// between passes and 22-bit normalized coefficients. No crop, mirror, heap or
// frame-sized intermediate. Test byte-for-byte against the installed Pillow.
constexpr size_t kCameraSide = 480;
constexpr size_t kInputSide = 128;
constexpr unsigned kResizeBits = 22;

struct ResizeKernel {
    uint16_t first;
    uint16_t count;
    std::array<uint32_t, 8> weights;
};

constexpr ResizeKernel resize_kernel(int output) {
    // Scale = 15/4. Express pixel-center distances in eighths so coefficient
    // construction uses exact integer arithmetic, not target floating point.
    int first = (30 * output - 11) / 8;
    if (first < 0) { first = 0; }
    int end = (30 * output + 49) / 8;
    if (end > static_cast<int>(kCameraSide)) { end = kCameraSide; }
    ResizeKernel kernel{static_cast<uint16_t>(first), static_cast<uint16_t>(end - first), {}};
    uint32_t sum = 0;
    for (int source = first; source < end; ++source) {
        int distance = 8 * source + 4 - (30 * output + 15);
        if (distance < 0) { distance = -distance; }
        const uint32_t weight = distance < 30 ? 30 - distance : 0;
        kernel.weights[source - first] = weight;
        sum += weight;
    }
    for (size_t i = 0; i < kernel.count; ++i) {
        kernel.weights[i] = (kernel.weights[i] * (1u << kResizeBits) * 2u + sum) / (2u * sum);
    }
    return kernel;
}

constexpr std::array<ResizeKernel, kInputSide> resize_kernels() {
    std::array<ResizeKernel, kInputSide> kernels{};
    for (size_t i = 0; i < kInputSide; ++i) { kernels[i] = resize_kernel(i); }
    return kernels;
}

inline constexpr auto kResizeKernels = resize_kernels(); // Read-only MRAM.

inline uint8_t resize_round(uint32_t value) {
    value >>= kResizeBits;
    return static_cast<uint8_t>(value < 255 ? value : 255);
}

// Caller supplies one 480x480 plane and space for one 128-pixel output row.
// Rows are independent; the source allocation is never changed.
inline void camera_resize_row(const uint8_t* plane, size_t y, uint8_t* output) {
    const auto& vertical = kResizeKernels[y];
    for (size_t x = 0; x < kInputSide; ++x) {
        const auto& horizontal = kResizeKernels[x];
        uint32_t value = 1u << (kResizeBits - 1);
        for (size_t row = 0; row < vertical.count; ++row) {
            const uint8_t* source = plane + (vertical.first + row) * kCameraSide + horizontal.first;
            uint32_t partial = 1u << (kResizeBits - 1);
            for (size_t col = 0; col < horizontal.count; ++col) {
                partial += source[col] * horizontal.weights[col];
            }
            value += resize_round(partial) * vertical.weights[row];
        }
        output[x] = resize_round(value);
    }
}
} // namespace handgpt
