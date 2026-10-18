#include "live_checks.h"
#include "live_display.h"
#include "camera_rotation.h"

#include <algorithm>
#include <cassert>
#include <cstdio>
#include <limits>
#include <vector>

int main(int argc, char**) {
    using namespace handgpt;
    static_assert(kAslClassCount == 26 && kAslLetterCount == 24);
    if (argc == 2) {
        float input[53];
        assert(std::fread(input, sizeof(float), 53, stdin) == 53);
        double error, cosine;
        std::printf("%u\n", asl_reference_matches(input, input + 26, 26, input[52], error, cosine) ? 1u : 0u);
        return 0;
    }
    const size_t pixels = 480 * 800;
    std::vector<uint16_t> guarded(pixels + 2);
    float logits[kAslClassCount];
    for (size_t best = 0; best < kAslClassCount; ++best) {
        std::fill(std::begin(logits), std::end(logits), -20.f);
        logits[best] = 20.f;
        const AslLetter expected = best < kAslLetterCount ? static_cast<AslLetter>(best + 1) : AslLetter::None;
        assert(letter_from_logits(logits, kAslClassCount) == expected);
        StableLetter stable;
        if (expected != AslLetter::None) {
            assert(stable.observe(expected, 1) == AslLetter::None);
            assert(stable.observe(expected, 2) == AslLetter::None);
            assert(stable.observe(expected, 3) == expected);
            assert(stable.current(1003) == AslLetter::None);
            unsigned bits = 0;
            for (unsigned y = 0; y < 7; ++y) { bits |= glyph_row(kAslLetters[best + 1], y); }
            assert(bits);
        }
        std::fill(guarded.begin(), guarded.end(), 0x1234);
        assert(draw_live_asl_panel(guarded.data() + 1, pixels, expected));
        assert(guarded.front() == 0x1234 && guarded.back() == 0x1234);
        assert(std::all_of(guarded.begin() + 1, guarded.begin() + 1 + 480 * 480,
                           [](uint16_t v) { return v == 0x1234; }));
        auto original = guarded;
        assert(rotate_lcd180(guarded.data() + 1, pixels));
        assert(rotate_lcd180(guarded.data() + 1, pixels));
        assert(original == guarded);
    }
    std::fill(std::begin(logits), std::end(logits), 0.f);
    assert(letter_from_logits(logits, 26) == AslLetter::None);
    assert(letter_from_logits(logits, 7) == AslLetter::None);
    assert(letter_from_logits(nullptr, 26) == AslLetter::None);
    logits[0] = std::numeric_limits<float>::infinity();
    assert(letter_from_logits(logits, 26) == AslLetter::None);
    assert(!draw_live_asl_panel(guarded.data()+1, pixels, static_cast<AslLetter>(255)));
    assert(!draw_live_asl_panel(guarded.data()+1, pixels-1, AslLetter::A));
    assert(glyph_row('J', 0) == 0 && glyph_row('Z', 0) == 0);
    StableLetter stable;
    stable.observe(AslLetter::N, 10); stable.observe(AslLetter::N, 20);
    assert(stable.observe(AslLetter::N, 30) == AslLetter::N);
    assert(stable.observe(AslLetter::None, 40) == AslLetter::None);
}
