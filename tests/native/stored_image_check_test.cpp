#include "image_check.h"

#include <cassert>
#include <limits>

int main() {
    float reference[1000], actual[1000];
    for (size_t i = 0; i < 1000; ++i) { reference[i] = actual[i] = -5.0f + static_cast<float>(i) * 0.01f; }
    handgpt::LogitComparison result;
    assert(handgpt::compare_logits(actual, reference, 1000, 1, 0.98, result));
    assert(result.finite && result.actual_top1 == 999 && result.reference_top1 == 999);
    actual[0] += 0.1f;
    assert(handgpt::compare_logits(actual, reference, 1000, 1, 0.98, result));
    actual[0] += 2.0f;
    assert(!handgpt::compare_logits(actual, reference, 1000, 1, 0.98, result));
    for (size_t i = 0; i < 1000; ++i) actual[i] = reference[i];
    actual[998] = reference[999];
    actual[999] = reference[998];
    assert(!handgpt::compare_logits(actual, reference, 1000, 1, 0.98, result)); // Top-1 mismatch despite small error.
    for (auto& value : actual) value = 0;
    assert(!handgpt::compare_logits(actual, reference, 1000, 1, 0.98, result));
    assert(result.finite && result.rmse > 0); // Finite but invalid zero-norm output is still a mismatch.
    for (size_t i = 0; i < 1000; ++i) actual[i] = -reference[i];
    assert(!handgpt::compare_logits(actual, reference, 1000, 100, 0.98, result));
    actual[0] = std::numeric_limits<float>::quiet_NaN();
    assert(!handgpt::compare_logits(actual, reference, 1000, 1, 0.98, result));
    assert(!result.finite);
    actual[0] = std::numeric_limits<float>::infinity();
    assert(!handgpt::compare_logits(actual, reference, 1000, 1, 0.98, result));
    assert(!handgpt::compare_logits(nullptr, reference, 1000, 1, 0.98, result));
    assert(!handgpt::compare_logits(actual, reference, 999, 1, 0.98, result));
    assert(!handgpt::compare_logits(actual, reference, 1000, -1, 0.98, result));
    assert(!handgpt::compare_logits(actual, reference, 1000, 1, 1.01, result));
    assert(handgpt::in_region(0x80001000, 5238784, 0x80001000, 0x80500000, 16));
    assert(!handgpt::in_region(0x80001000, 5238785, 0x80001000, 0x80500000, 16));
    assert(!handgpt::in_region(0x80000000, 4, 0x80001000, 0x80500000, 16));
    assert(!handgpt::in_region(0x20000000, 32, 0x02000000, 0x02400000, 32));
    assert(!handgpt::in_region(0x02000001, 32, 0x02000000, 0x02400000, 32));
    size_t padded, alignment;
    assert(handgpt::cache_allocation_request(17, 16, padded, alignment));
    assert(padded == 32 && alignment == 32);
    assert(handgpt::cache_allocation_request(33, 64, padded, alignment));
    assert(padded == 64 && alignment == 64);
    assert(!handgpt::cache_allocation_request(4, 0, padded, alignment));
    assert(!handgpt::cache_allocation_request(4, 3, padded, alignment));
    assert(!handgpt::cache_allocation_request(SIZE_MAX, 16, padded, alignment));
}
