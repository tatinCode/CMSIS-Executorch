#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <asl_classes.h>

// Live-only display contract. The legacy TEST UI remains unchanged.
namespace handgpt {
inline AslLetter letter_from_logits(const float* logits, size_t count, float threshold = 0.8f) {
    if (!logits || count != kAslClassCount || !std::isfinite(threshold) || threshold < 0 || threshold > 1) {
        return AslLetter::None;
    }
    size_t best = 0;
    for (size_t i = 0; i < count; ++i) {
        if (!std::isfinite(logits[i])) { return AslLetter::None; }
        if (logits[i] > logits[best]) { best = i; }
    }
    if (best >= kAslLetterCount) { return AslLetter::None; }
    float sum = 0;
    for (size_t i = 0; i < count; ++i) { sum += std::exp(logits[i] - logits[best]); }
    return 1.0f / sum >= threshold ? static_cast<AslLetter>(best + 1) : AslLetter::None;
}

inline bool valid_letter(AslLetter value) {
    return static_cast<unsigned>(value) <= kAslLetterCount;
}

class StableLetter {
public:
    static constexpr uint32_t kStaleMs = 1000;
    AslLetter observe(AslLetter value, uint32_t now) {
        expire(now);
        last_ = now;
        seen_ = true;
        if (!valid_letter(value) || value == AslLetter::None) {
            pending_ = displayed_ = AslLetter::None;
            count_ = 0;
        } else {
            if (pending_ != value) { pending_ = value; count_ = 0; displayed_ = AslLetter::None; }
            if (count_ < 3) { ++count_; }
            if (count_ == 3) { displayed_ = value; }
        }
        return displayed_;
    }
    AslLetter current(uint32_t now) { expire(now); return displayed_; }
private:
    void expire(uint32_t now) {
        if (seen_ && static_cast<uint32_t>(now - last_) >= kStaleMs) {
            pending_ = displayed_ = AslLetter::None;
            count_ = 0;
            seen_ = false;
        }
    }
    AslLetter pending_ = AslLetter::None, displayed_ = AslLetter::None;
    uint32_t last_ = 0;
    unsigned count_ = 0;
    bool seen_ = false;
};

inline uint8_t glyph_row(char c, unsigned y) {
    if (y >= 7) { return 0; }
    switch (c) {
    case 'A': { constexpr uint8_t r[] = {14,17,17,31,17,17,17}; return r[y]; }
    case 'B': { constexpr uint8_t r[] = {30,17,17,30,17,17,30}; return r[y]; }
    case 'C': { constexpr uint8_t r[] = {14,17,16,16,16,17,14}; return r[y]; }
    case 'D': { constexpr uint8_t r[] = {30,17,17,17,17,17,30}; return r[y]; }
    case 'E': { constexpr uint8_t r[] = {31,16,16,30,16,16,31}; return r[y]; }
    case 'F': { constexpr uint8_t r[] = {31,16,16,30,16,16,16}; return r[y]; }
    case 'G': { constexpr uint8_t r[] = {14,17,16,23,17,17,14}; return r[y]; }
    case 'H': { constexpr uint8_t r[] = {17,17,17,31,17,17,17}; return r[y]; }
    case 'I': { constexpr uint8_t r[] = {31,4,4,4,4,4,31}; return r[y]; }
    case 'K': { constexpr uint8_t r[] = {17,18,20,24,20,18,17}; return r[y]; }
    case 'L': { constexpr uint8_t r[] = {16,16,16,16,16,16,31}; return r[y]; }
    case 'M': { constexpr uint8_t r[] = {17,27,21,21,17,17,17}; return r[y]; }
    case 'N': { constexpr uint8_t r[] = {17,25,25,21,19,19,17}; return r[y]; }
    case 'O': { constexpr uint8_t r[] = {14,17,17,17,17,17,14}; return r[y]; }
    case 'P': { constexpr uint8_t r[] = {30,17,17,30,16,16,16}; return r[y]; }
    case 'Q': { constexpr uint8_t r[] = {14,17,17,17,21,18,13}; return r[y]; }
    case 'R': { constexpr uint8_t r[] = {30,17,17,30,20,18,17}; return r[y]; }
    case 'S': { constexpr uint8_t r[] = {15,16,16,14,1,1,30}; return r[y]; }
    case 'T': { constexpr uint8_t r[] = {31,4,4,4,4,4,4}; return r[y]; }
    case 'U': { constexpr uint8_t r[] = {17,17,17,17,17,17,14}; return r[y]; }
    case 'V': { constexpr uint8_t r[] = {17,17,17,17,17,10,4}; return r[y]; }
    case 'W': { constexpr uint8_t r[] = {17,17,17,21,21,21,10}; return r[y]; }
    case 'X': { constexpr uint8_t r[] = {17,17,10,4,10,17,17}; return r[y]; }
    case 'Y': { constexpr uint8_t r[] = {17,17,10,4,4,4,4}; return r[y]; }
    case '-': return y == 3 ? 31 : 0;
    default: return 0;
    }
}

inline void draw_glyph(uint16_t* lcd, char c, unsigned x, unsigned y, unsigned scale, uint16_t color) {
    if (!lcd || !scale || scale > 20 || x > 480 - 5 * scale || y > 800 - 7 * scale) { return; }
    for (unsigned row = 0; row < 7; ++row) {
        const uint8_t bits = glyph_row(c, row);
        for (unsigned col = 0; col < 5; ++col) {
            if (!(bits & (1u << (4 - col)))) { continue; }
            for (unsigned dy = 0; dy < scale; ++dy) {
                for (unsigned dx = 0; dx < scale; ++dx) {
                    lcd[(y + row * scale + dy) * 480 + x + col * scale + dx] = color;
                }
            }
        }
    }
}

inline bool draw_asl_panel(uint16_t* lcd, size_t pixels, AslLetter value, bool test_ui) {
    if (!lcd || pixels != 480 * 800 || !valid_letter(value) || test_ui) { return false; }
    for (size_t i = 480 * 480; i < pixels; ++i) { lcd[i] = 0; }
    constexpr char title[] = "ASL";
    for (unsigned i = 0; title[i]; ++i) { draw_glyph(lcd, title[i], 24 + 24 * i, 500, 4, 0xffe0); }
    if (value == AslLetter::None) {
        draw_glyph(lcd, '-', 130, 568, 20, 0xffff);
        draw_glyph(lcd, '-', 250, 568, 20, 0xffff);
    } else {
        draw_glyph(lcd, kAslLetters[static_cast<unsigned>(value)], 190, 568, 20, 0xffff);
    }
    return true;
}
} // namespace handgpt
