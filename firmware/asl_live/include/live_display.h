#pragma once

#include "asl_display.h"

namespace handgpt {
inline uint8_t failure_glyph_row(char c, unsigned y) {
    if (y >= 7) { return 0; }
    switch (c) {
    case 'F': { constexpr uint8_t r[] = {31,16,16,30,16,16,16}; return r[y]; }
    case '0': { constexpr uint8_t r[] = {14,17,19,21,25,17,14}; return r[y]; }
    case '1': { constexpr uint8_t r[] = {4,12,4,4,4,4,14}; return r[y]; }
    case '2': { constexpr uint8_t r[] = {14,17,1,2,4,8,31}; return r[y]; }
    case '3': { constexpr uint8_t r[] = {30,1,1,14,1,1,30}; return r[y]; }
    case '4': { constexpr uint8_t r[] = {2,6,10,18,31,2,2}; return r[y]; }
    case '5': { constexpr uint8_t r[] = {31,16,16,30,1,1,30}; return r[y]; }
    case '6': { constexpr uint8_t r[] = {14,16,16,30,17,17,14}; return r[y]; }
    case '7': { constexpr uint8_t r[] = {31,1,2,4,8,8,8}; return r[y]; }
    case '8': { constexpr uint8_t r[] = {14,17,17,14,17,17,14}; return r[y]; }
    case '9': { constexpr uint8_t r[] = {14,17,17,15,1,1,14}; return r[y]; }
    case 'X': { constexpr uint8_t r[] = {17,17,10,4,10,17,17}; return r[y]; }
    case 'P': { constexpr uint8_t r[] = {30,17,17,30,16,16,16}; return r[y]; }
    case 'R': { constexpr uint8_t r[] = {30,17,17,30,20,18,17}; return r[y]; }
    case 'M': { constexpr uint8_t r[] = {17,27,21,21,17,17,17}; return r[y]; }
    default: return glyph_row(c, y);
    }
}

inline bool draw_live_asl_panel(uint16_t* lcd, size_t pixels, AslLetter value) {
    return draw_asl_panel(lcd, pixels, value, false);
}

inline void draw_failure_text(uint16_t* lcd, const char* text, unsigned x, unsigned y) {
    for (unsigned i = 0; text[i]; ++i) {
        const unsigned left = x + 24 * i;
        if (left > 460 || y > 772) { return; }
        for (unsigned row = 0; row < 7; ++row) {
            for (unsigned col = 0; col < 5; ++col) {
                if (!(failure_glyph_row(text[i], row) & (1u << (4 - col)))) { continue; }
                for (unsigned dy = 0; dy < 4; ++dy) {
                    for (unsigned dx = 0; dx < 4; ++dx) {
                        lcd[(y + 4 * row + dy) * 480 + left + 4 * col + dx] = 0xf800;
                    }
                }
            }
        }
    }
}

inline bool draw_selftest_failure(uint16_t* lcd, size_t pixels, uint32_t code) {
    if (!draw_live_asl_panel(lcd, pixels, AslLetter::None)) { return false; }
    for (size_t i = 552 * 480; i < 740 * 480; ++i) { lcd[i] = 0; }
    draw_failure_text(lcd, "SELFTEST", 24, 568);
    draw_failure_text(lcd, "FAIL", 24, 612);
    char digits[11]{};
    unsigned start = 10;
    do { digits[--start] = static_cast<char>('0' + code % 10); code /= 10; } while (code);
    draw_failure_text(lcd, digits + start, 24, 656);
    return true;
}
} // namespace handgpt
