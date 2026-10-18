#include "live_checks.h"
#include "live_display.h"
#include "camera_rotation.h"

#include <algorithm>
#include <cassert>
#include <cstdio>
#include <limits>
#include <vector>

int main(int argc, char**) {
    if (argc == 2) {
        float input[15];
        assert(std::fread(input, sizeof(float), 15, stdin) == 15);
        double error, cosine;
        const bool match = handgpt::asl_reference_matches(input, input + 7, 7, input[14], error, cosine);
        std::printf("%u\n", match ? 1u : 0u);
        return 0;
    }
    float reference[] = {3, -12, -9, -13, -4, -17, -4};
    float values[] = {3, -12, -9, -13, -4, -17, -4};
    double error, cosine;
    assert(handgpt::asl_reference_matches(values, reference, 7, 0.125, error, cosine));
    values[0] = 5;
    assert(!handgpt::asl_reference_matches(values, reference, 7, 0.125, error, cosine));
    values[0] = std::numeric_limits<float>::quiet_NaN();
    assert(!handgpt::asl_reference_matches(values, reference, 7, 0.125, error, cosine));
    assert(!handgpt::asl_reference_matches(nullptr, reference, 7, 0.125, error, cosine));
    assert(!handgpt::asl_reference_matches(reference, reference, 6, 0.125, error, cosine));
    for (double scale : {0.0, -0.1, std::numeric_limits<double>::quiet_NaN(), std::numeric_limits<double>::infinity()}) {
        assert(!handgpt::asl_reference_matches(reference, reference, 7, scale, error, cosine));
    }
    constexpr size_t count = 480 * 800;
    std::vector<uint16_t> guarded(count + 2, 0x1234);
    for (unsigned letter = 0; letter < 6; ++letter) {
        std::fill(guarded.begin(), guarded.end(), 0x1234);
        auto* lcd = guarded.data() + 1;
        assert(handgpt::draw_live_asl_panel(lcd, count, static_cast<handgpt::AslLetter>(letter)));
        assert(guarded.front() == 0x1234 && guarded.back() == 0x1234);
        assert(std::all_of(lcd, lcd + 480 * 480, [](uint16_t v) { return v == 0x1234; }));
        // The live panel must not add any badge/footer to the ordinary ASL panel.
        std::vector<uint16_t> expected(count, 0x1234);
        assert(handgpt::draw_asl_panel(expected.data(), count, static_cast<handgpt::AslLetter>(letter), false));
        assert(std::equal(lcd, lcd + count, expected.begin()));
        assert(handgpt::rotate_lcd180(lcd, count));
        assert(std::all_of(lcd + 480 * 320, lcd + count, [](uint16_t v) { return v == 0x1234; }));
    }
    assert(!handgpt::draw_live_asl_panel(nullptr, count, handgpt::AslLetter::A));
    assert(!handgpt::draw_live_asl_panel(guarded.data() + 1, count - 1, handgpt::AslLetter::A));
    for (uint32_t code : {0u, 325u, UINT32_MAX}) {
        std::fill(guarded.begin(), guarded.end(), 0x1234);
        auto* lcd = guarded.data() + 1;
        assert(handgpt::draw_selftest_failure(lcd, count, code));
        assert(guarded.front() == 0x1234 && guarded.back() == 0x1234);
        assert(std::all_of(lcd, lcd + 480 * 480, [](uint16_t v) { return v == 0x1234; }));
        // SELFTEST / FAIL / numeric code all have visible red pixels.
        for (unsigned y : {568u, 612u, 656u}) {
            assert(std::count(lcd + y * 480, lcd + (y + 28) * 480, 0xf800) > 0);
        }
    }
    const char* symbols = "SELFTEST FAIL 0123456789";
    for (unsigned i = 0; symbols[i]; ++i) {
        if (symbols[i] == ' ') { continue; }
        unsigned bits = 0;
        for (unsigned y = 0; y < 7; ++y) { bits |= handgpt::failure_glyph_row(symbols[i], y); }
        assert(bits);
    }
    assert(!handgpt::draw_selftest_failure(nullptr, count, 325));
    assert(!handgpt::draw_selftest_failure(guarded.data() + 1, count - 1, 325));
}
