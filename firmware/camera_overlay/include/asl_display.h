#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>

namespace handgpt {
enum class AslLetter : uint8_t { None, A, B, C, L, Y };

// Schema-2 float logits: A/B/C/L/Y/NO_HAND/OTHER. Invalid or negative output
// must never become a letter. Quantized output must be dequantized first.
inline AslLetter letter_from_logits(const float* logits, size_t count, float threshold = 0.8f) {
    if (!logits || count != 7 || !std::isfinite(threshold) || threshold < 0 || threshold > 1) {
        return AslLetter::None;
    }
    size_t best = 0;
    for (size_t i = 0; i < count; ++i) {
        if (!std::isfinite(logits[i])) { return AslLetter::None; }
        if (logits[i] > logits[best]) { best = i; }
    }
    if (best >= 5) { return AslLetter::None; }
    float sum = 0;
    for (size_t i = 0; i < count; ++i) { sum += std::exp(logits[i] - logits[best]); }
    if (1.0f / sum < threshold) { return AslLetter::None; }
    return static_cast<AslLetter>(best + 1);
}

inline bool valid_letter(AslLetter value) {
    return static_cast<unsigned>(value) <= static_cast<unsigned>(AslLetter::Y);
}

// Three consecutive matching observations are required to display a letter.
// A different/negative observation clears immediately (no stale hold/hysteresis).
// The caller must call current(now) each display update to expire stalled input.
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

// Compact 5x7 bitmap font, no graphics heap. Only the required UI alphabet.
inline uint8_t glyph_row(char c, unsigned y) {
    if (y >= 7) { return 0; }
    switch (c) {
    case 'A': { constexpr uint8_t r[] = {14,17,17,31,17,17,17}; return r[y]; }
    case 'B': { constexpr uint8_t r[] = {30,17,17,30,17,17,30}; return r[y]; }
    case 'C': { constexpr uint8_t r[] = {14,17,16,16,16,17,14}; return r[y]; }
    case 'E': { constexpr uint8_t r[] = {31,16,16,30,16,16,31}; return r[y]; }
    case 'I': { constexpr uint8_t r[] = {31,4,4,4,4,4,31}; return r[y]; }
    case 'L': { constexpr uint8_t r[] = {16,16,16,16,16,16,31}; return r[y]; }
    case 'N': { constexpr uint8_t r[] = {17,25,25,21,19,19,17}; return r[y]; }
    case 'O': { constexpr uint8_t r[] = {14,17,17,17,17,17,14}; return r[y]; }
    case 'S': { constexpr uint8_t r[] = {15,16,16,14,1,1,30}; return r[y]; }
    case 'T': { constexpr uint8_t r[] = {31,4,4,4,4,4,4}; return r[y]; }
    case 'U': { constexpr uint8_t r[] = {17,17,17,17,17,17,14}; return r[y]; }
    case 'V': { constexpr uint8_t r[] = {17,17,17,17,17,10,4}; return r[y]; }
    case 'Y': { constexpr uint8_t r[] = {17,17,10,4,4,4,4}; return r[y]; }
    case '-': return y == 3 ? 31 : 0;
    default: return 0;
    }
}

inline void draw_glyph(uint16_t* lcd, char c, unsigned x, unsigned y, unsigned scale, uint16_t color) {
    // The bounds are fixed to the proven RGB565 panel; reject instead of clipping.
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
    if (!lcd || pixels != 480 * 800 || !valid_letter(value)) { return false; }
    // Camera occupies rows 0..479; touch only the existing unused bottom band.
    for (size_t i = 480 * 480; i < pixels; ++i) { lcd[i] = 0; }
    const char* title = test_ui ? "TEST UI" : "ASL";
    for (unsigned i = 0; title[i]; ++i) { draw_glyph(lcd, title[i], 24 + 24 * i, 500, 4, 0xffe0); }
    const char* footer = test_ui ? "NOT LIVE ASL" : "";
    for (unsigned i = 0; footer[i]; ++i) { draw_glyph(lcd, footer[i], 24 + 18 * i, 756, 3, 0xffe0); }
    if (value == AslLetter::None) {
        draw_glyph(lcd, '-', 130, 568, 20, 0xffff);
        draw_glyph(lcd, '-', 250, 568, 20, 0xffff);
    } else {
        constexpr char letters[] = " ABCLY";
        draw_glyph(lcd, letters[static_cast<unsigned>(value)], 190, 568, 20, 0xffff);
    }
    return true;
}
} // namespace handgpt
