#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>

namespace handgpt {

constexpr size_t kImageElements = 3 * 224 * 224;
constexpr size_t kClasses = 1000;

inline bool cache_allocation_request(size_t bytes, size_t alignment, size_t& padded, size_t& aligned) {
    if (alignment == 0 || (alignment & (alignment - 1)) != 0 || bytes > SIZE_MAX - 31) { return false; }
    padded = (bytes + 31u) & ~size_t(31);
    aligned = alignment < 32 ? 32 : alignment;
    return true;
}

inline bool in_region(uintptr_t address, size_t bytes, uintptr_t begin, uintptr_t end, size_t alignment) {
    return alignment != 0 && (alignment & (alignment - 1)) == 0 && address % alignment == 0 &&
           begin <= address && address < end && bytes != 0 && bytes <= end - address;
}

struct LogitComparison {
    double max_abs_error = 0;
    double rmse = 0;
    double cosine = 0;
    size_t actual_top1 = 0;
    size_t reference_top1 = 0;
    bool finite = false;
};

inline bool compare_logits(const float* actual, const float* reference, size_t count,
                           float max_error, double min_cosine, LogitComparison& result) {
    result = {};
    if (!actual || !reference || count != kClasses || !std::isfinite(max_error) || max_error < 0 ||
        !std::isfinite(min_cosine) || min_cosine < -1 || min_cosine > 1) { return false; }
    double squared_error = 0, dot = 0, actual_norm = 0, reference_norm = 0;
    for (size_t i = 0; i < count; ++i) {
        if (!std::isfinite(actual[i]) || !std::isfinite(reference[i])) { return false; }
        const double error = static_cast<double>(actual[i]) - reference[i];
        if (std::fabs(error) > result.max_abs_error) { result.max_abs_error = std::fabs(error); }
        squared_error += error * error;
        dot += static_cast<double>(actual[i]) * reference[i];
        actual_norm += static_cast<double>(actual[i]) * actual[i];
        reference_norm += static_cast<double>(reference[i]) * reference[i];
        if (actual[i] > actual[result.actual_top1]) { result.actual_top1 = i; }
        if (reference[i] > reference[result.reference_top1]) { result.reference_top1 = i; }
    }
    result.finite = true;
    result.rmse = std::sqrt(squared_error / count);
    if (actual_norm == 0 || reference_norm == 0) { return false; }
    result.cosine = dot / std::sqrt(actual_norm * reference_norm);
    return result.max_abs_error <= max_error && result.cosine >= min_cosine &&
           result.actual_top1 == result.reference_top1;
}

} // namespace handgpt
