#include "camera_resize.h"

#include <cstdio>

// Binary stdin/stdout harness for independently generated Pillow reference data.
static uint8_t source[480 * 480 * 3];
int main() {
    if (std::fread(source, 1, sizeof(source), stdin) != sizeof(source) || std::fgetc(stdin) != EOF) { return 1; }
    uint8_t row[128];
    for (size_t plane = 0; plane < 3; ++plane) {
        for (size_t y = 0; y < 128; ++y) {
            handgpt::camera_resize_row(source + plane * 480 * 480, y, row);
            if (std::fwrite(row, 1, sizeof(row), stdout) != sizeof(row)) { return 1; }
        }
    }
    return 0;
}
