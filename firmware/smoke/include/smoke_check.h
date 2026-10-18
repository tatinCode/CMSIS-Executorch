#pragma once

#include <cmath>
#include <stddef.h>
#include <stdint.h>
#include <limits>

namespace handgpt {

constexpr size_t kSmokeElements = 16;
// Two int8 quantization boundaries: a bring-up tolerance, not an accuracy metric.
constexpr float kSmokeTolerance = 0.04f;

inline bool valid_range(size_t offset, size_t size, size_t capacity) {
    return offset <= capacity && size <= capacity - offset;
}

inline bool valid_sram0_buffer(uintptr_t address, size_t bytes) {
    constexpr uintptr_t kStart = 0x02000000u;
    constexpr uintptr_t kEnd = 0x02400000u;
    return address >= kStart && address < kEnd && (address & 31u) == 0 &&
           bytes != 0 && bytes <= kEnd - address;
}

inline bool check_doubled_input(
    const float* input, const float* output, size_t count, float* max_error) {
    if (!input || !output || !max_error || count != kSmokeElements) {
        return false;
    }
    *max_error = 0.0f;
    for (size_t i = 0; i < count; ++i) {
        if (!std::isfinite(input[i]) || !std::isfinite(output[i])) {
            *max_error = std::numeric_limits<float>::infinity();
            return false;
        }
        const float error = std::fabs(output[i] - 2.0f * input[i]);
        if (error > *max_error) {
            *max_error = error;
        }
    }
    return *max_error <= kSmokeTolerance;
}

} // namespace handgpt
