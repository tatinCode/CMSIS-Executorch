#include "asl_display.h"
#include "camera_rotation.h"

#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <limits>
#include <vector>

using handgpt::AslLetter;

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "render") == 0) {
        // Six RGB565 frames for host visual review, not an inference simulation.
        std::vector<uint16_t> pixels(480 * 800, 0x39e7);
        for (unsigned i = 0; i < 6; ++i) {
            std::fill(pixels.begin(), pixels.end(), 0x39e7);
            assert(handgpt::draw_asl_panel(pixels.data(), pixels.size(), static_cast<AslLetter>(i), true));
            assert(handgpt::rotate_lcd180(pixels.data(), pixels.size()));
            assert(std::fwrite(pixels.data(), sizeof(uint16_t), pixels.size(), stdout) == pixels.size());
        }
        return 0;
    }

    // Verify exact whole-panel reversal, including both regions, and bounds.
    constexpr size_t screen_pixels = 480 * 800;
    std::vector<uint16_t> screen(screen_pixels + 2, 0x1234);
    for (size_t i = 0; i < screen_pixels; ++i) { screen[i + 1] = static_cast<uint16_t>(i * 17 + i / 480); }
    const auto original = screen;
    assert(handgpt::rotate_lcd180(screen.data() + 1, screen_pixels));
    for (size_t i = 0; i < screen_pixels; ++i) {
        assert(screen[i + 1] == original[screen_pixels - i]);
    }
    assert(screen.front() == 0x1234 && screen.back() == 0x1234);
    assert(handgpt::rotate_lcd180(screen.data() + 1, screen_pixels));
    assert(screen == original);
    assert(!handgpt::rotate_lcd180(nullptr, screen_pixels));
    assert(!handgpt::rotate_lcd180(screen.data() + 1, screen_pixels - 1));
    assert(!handgpt::rotate_lcd180(screen.data() + 1, SIZE_MAX));
    assert(screen == original);

    float values[7]{};
    assert(handgpt::letter_from_logits(values, 7) == AslLetter::None);
    for (size_t i = 0; i < 7; ++i) {
        std::fill(values, values + 7, -10000.0f);
        values[i] = 10000.0f; // Numerically stable extreme logits.
        assert(handgpt::letter_from_logits(values, 7) == (i < 5 ? static_cast<AslLetter>(i+1) : AslLetter::None));
    }
    values[0] = std::numeric_limits<float>::quiet_NaN();
    assert(handgpt::letter_from_logits(values, 7) == AslLetter::None);
    values[0] = std::numeric_limits<float>::infinity();
    assert(handgpt::letter_from_logits(values, 7) == AslLetter::None);
    assert(handgpt::letter_from_logits(nullptr, 7) == AslLetter::None);
    assert(handgpt::letter_from_logits(values, 6) == AslLetter::None);
    assert(handgpt::letter_from_logits(values, 7, -1) == AslLetter::None);
    std::fill(values, values + 7, 0.f);
    values[3] = 4.f;
    assert(handgpt::letter_from_logits(values, 7) == AslLetter::L);
    values[3] = 3.f;
    assert(handgpt::letter_from_logits(values, 7) == AslLetter::None);

    handgpt::StableLetter stable;
    assert(stable.current(0) == AslLetter::None);
    assert(stable.observe(AslLetter::L, 100) == AslLetter::None);
    assert(stable.observe(AslLetter::L, 200) == AslLetter::None);
    assert(stable.observe(AslLetter::L, 300) == AslLetter::L);
    assert(stable.current(1299) == AslLetter::L);
    assert(stable.current(1300) == AslLetter::None); // Stalled input expires.
    assert(stable.observe(AslLetter::L, 1400) == AslLetter::None);
    assert(stable.observe(AslLetter::L, 1500) == AslLetter::None);
    assert(stable.observe(AslLetter::L, 1600) == AslLetter::L);
    assert(stable.observe(AslLetter::Y, 1700) == AslLetter::None); // No stale L.
    assert(stable.observe(AslLetter::Y, 1800) == AslLetter::None);
    assert(stable.observe(AslLetter::Y, 1900) == AslLetter::Y);
    assert(stable.observe(AslLetter::None, 1901) == AslLetter::None); // Immediate clear.
    assert(stable.observe(static_cast<AslLetter>(255), 1902) == AslLetter::None);
    handgpt::StableLetter wrap;
    assert(wrap.observe(AslLetter::A, 0xffffff00u) == AslLetter::None);
    assert(wrap.observe(AslLetter::A, 0xffffff20u) == AslLetter::None);
    assert(wrap.observe(AslLetter::A, 0xffffff40u) == AslLetter::A);
    assert(wrap.current(0x20u) == AslLetter::A);
    assert(wrap.current(0x400u) == AslLetter::None);
    // Large gaps between observations cannot count as consecutive.
    assert(wrap.observe(AslLetter::A, 2000) == AslLetter::None);
    assert(wrap.observe(AslLetter::A, 3000) == AslLetter::None);
    assert(wrap.observe(AslLetter::A, 4000) == AslLetter::None);

    constexpr size_t count = 480 * 800;
    std::vector<uint16_t> guarded(count + 2, 0x1234);
    uint16_t* lcd = guarded.data() + 1;
    for (unsigned i = 0; i < 6; ++i) {
        std::fill(lcd, lcd + count, 0x1234);
        assert(handgpt::draw_asl_panel(lcd, count, static_cast<AslLetter>(i), true));
        assert(guarded.front() == 0x1234 && guarded.back() == 0x1234);
        assert(std::all_of(lcd, lcd + 480 * 480, [](uint16_t v) { return v == 0x1234; }));
        assert(std::count(lcd + 480 * 480, lcd + count, 0xffff) > 0);
        assert(std::count(lcd + 480 * 480, lcd + count, 0xffe0) > 0);
        // Redraw clears previous glyphs rather than leaving stale pixels.
        std::vector<uint16_t> fresh(count, 0);
        assert(handgpt::draw_asl_panel(fresh.data(), count, AslLetter::None, true));
        assert(handgpt::draw_asl_panel(lcd, count, AslLetter::None, true));
        assert(std::equal(lcd + 480 * 480, lcd + count, fresh.begin() + 480 * 480));
    }
    std::fill(lcd, lcd + count, 0x1234);
    assert(!handgpt::draw_asl_panel(nullptr, count, AslLetter::A, true));
    assert(!handgpt::draw_asl_panel(lcd, count - 1, AslLetter::A, true));
    assert(!handgpt::draw_asl_panel(lcd, count, static_cast<AslLetter>(255), true));
    handgpt::draw_glyph(lcd, 'A', 479, 799, 20, 0xffff);
    handgpt::draw_glyph(lcd, 'A', 0, 0, 0, 0xffff);
    assert(std::all_of(guarded.begin(), guarded.end(), [](uint16_t v) { return v == 0x1234; }));
}
