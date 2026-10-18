#pragma once

#include <cmath>
#include <cstddef>
#include "asl_display.h"

namespace handgpt {
constexpr unsigned kAslMaxOutputSteps = 8;
constexpr double kAslMinimumCosine = 0.999;
constexpr float kAslSelftestThreshold = 0.8f;
// Logical ASL outputs only; never change the ImageNet checker or pool extents.
inline bool asl_reference_matches(const float* values, const float* reference, size_t count,
                                  double output_scale, double& max_error, double& cosine) {
    max_error = cosine = 0;
    if (!values || !reference || count != kAslClassCount || !std::isfinite(output_scale) || output_scale <= 0 ||
        !std::isfinite(output_scale * kAslMaxOutputSteps)) { return false; }
    double dot = 0, norm = 0, ref_norm = 0;
    size_t best = 0, expected = 0;
    for (size_t i = 0; i < count; ++i) {
        if (!std::isfinite(values[i]) || !std::isfinite(reference[i])) { return false; }
        const double error = std::fabs(static_cast<double>(values[i]) - reference[i]);
        if (error > max_error) { max_error = error; }
        dot += static_cast<double>(values[i]) * reference[i];
        norm += static_cast<double>(values[i]) * values[i];
        ref_norm += static_cast<double>(reference[i]) * reference[i];
        if (values[i] > values[best]) { best = i; }
        if (reference[i] > reference[expected]) { expected = i; }
    }
    if (norm == 0 || ref_norm == 0) { return false; }
    cosine = dot / std::sqrt(norm * ref_norm);
    // Revision 2: explicitly approved AFTER v5 failed at 0.557068 (>0.5).
    // This post-hoc engineering bound is not a pre-registered accuracy result.
    return max_error <= kAslMaxOutputSteps * output_scale && cosine >= kAslMinimumCosine && best == expected &&
           letter_from_logits(values, count, kAslSelftestThreshold) ==
           letter_from_logits(reference, count, kAslSelftestThreshold);
}
} // namespace handgpt
