#include "preview_pixels.h"
#include <cassert>

int main() {
    const uint8_t rgb[] = {255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255};
    uint16_t output[12];
    for (auto& pixel : output) { pixel = 0x1234; }
    assert(handgpt::preview_rgb565(rgb, sizeof(rgb), 2, 2, output, 3, 4));
    assert(output[0] == 0xf800 && output[1] == 0x07e0 && output[2] == 0);
    assert(output[3] == 0x001f && output[4] == 0xffff && output[5] == 0);
    for (unsigned i = 6; i < 12; ++i) { assert(output[i] == 0); }
    assert(!handgpt::preview_rgb565(nullptr, sizeof(rgb), 2, 2, output, 3, 4));
    assert(!handgpt::preview_rgb565(rgb, sizeof(rgb) - 1, 2, 2, output, 3, 4));
    assert(!handgpt::preview_rgb565(rgb, sizeof(rgb), 2, 2, output, 1, 4));
    assert(!handgpt::preview_rgb565(rgb, sizeof(rgb), 0, 2, output, 3, 4));
    assert(!handgpt::preview_rgb565(rgb, sizeof(rgb), SIZE_MAX, 2, output, SIZE_MAX, 4));
    assert(!handgpt::preview_rgb565(rgb, sizeof(rgb), 2, 2, output, SIZE_MAX / 2, 2));
    assert(!handgpt::elapsed(100, 109, 10));
    assert(handgpt::elapsed(100, 110, 10));
    assert(handgpt::elapsed(UINT32_MAX - 5, 4, 10));
}
