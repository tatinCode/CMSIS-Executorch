#pragma once
#include "asl_display.h"

extern uint32_t g_collection_sequence;

namespace handgpt {
inline uint8_t collect_row(char c, unsigned y) {
    if (y >= 7) { return 0; }
    constexpr uint8_t digits[10][7] = {
        {14,17,19,21,25,17,14},{4,12,4,4,4,4,14},{14,17,1,2,4,8,31},
        {30,1,1,14,1,1,30},{2,6,10,18,31,2,2},{31,16,16,30,1,1,30},
        {14,16,16,30,17,17,14},{31,1,2,4,8,8,8},{14,17,17,14,17,17,14},
        {14,17,17,15,1,1,14}
    };
    return c >= '0' && c <= '9' ? digits[c - '0'][y] : glyph_row(c, y);
}
inline void collect_text(uint16_t* lcd, const char* text, unsigned x, unsigned y, unsigned scale) {
    if (!lcd || !scale || scale > 10) { return; }
    for (unsigned i = 0; text[i]; ++i) {
        const unsigned left = x + i * 6 * scale;
        if (left > 480 - 5 * scale || y > 800 - 7 * scale) { return; }
        for (unsigned row = 0; row < 7; ++row) {
            for (unsigned col = 0; col < 5; ++col) {
                if (!(collect_row(text[i], row) & (1u << (4 - col)))) { continue; }
                for (unsigned dy = 0; dy < scale; ++dy) {
                    for (unsigned dx = 0; dx < scale; ++dx) {
                        lcd[(y + row * scale + dy) * 480 + left + col * scale + dx] = 0xffe0;
                    }
                }
            }
        }
    }
}
inline bool draw_collection_panel(uint16_t* lcd, size_t pixels, uint32_t sequence) {
    if (!lcd || pixels != 480 * 800) { return false; }
    for (size_t i = 480 * 480; i < pixels; ++i) { lcd[i] = 0; }
    collect_text(lcd, "COLLECT", 24, 500, 4);
    char digits[11]{};
    unsigned start = 10;
    do { digits[--start] = static_cast<char>('0' + sequence % 10); sequence /= 10; } while (sequence);
    collect_text(lcd, digits + start, 24, 580, 6);
    collect_text(lcd, "NOT ASL", 24, 756, 3);
    return true;
}
} // namespace handgpt
