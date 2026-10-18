#include "frame_export.h"
#include "smoke_platform.h"
#include <cstdio>
#include <cstring>
#include <cassert>

extern "C" {
volatile SmokeStatus g_smoke_status{};
uint8_t g_preview_rgb[480 * 480 * 3];
}
static uint32_t frames = 7;
uint32_t preview_frames() { return frames; }
#ifdef TEST_WRAP_WRITES
static const char* scenario = "";
static unsigned writes = 0;
extern "C" size_t __real_fwrite(const void*, size_t, size_t, FILE*);
extern "C" size_t __wrap_fwrite(const void* data, size_t size, size_t count, FILE* stream) {
    assert(size == 1 && count <= 1024);
    ++writes;
    if (writes == 2 && std::strcmp(scenario, "write-error") == 0) { return count - 1; }
    const size_t result = __real_fwrite(data, size, count, stream);
    if (writes == 2 && std::strcmp(scenario, "uart-during-export") == 0) { ++g_smoke_status.uart_timeouts; }
    return result;
}
#endif
int main(int argc, char** argv) {
#ifdef TEST_WRAP_WRITES
    if (argc == 2) { scenario = argv[1]; }
#endif
    setvbuf(stdout, nullptr, _IONBF, 0);
    if (argc == 2 && std::strcmp(argv[1], "no-frame") == 0) { frames = 0; }
    if (argc == 2 && std::strcmp(argv[1], "uart-error") == 0) { g_smoke_status.uart_timeouts = 1; }
    for (size_t i = 0; i < sizeof(g_preview_rgb); ++i) { g_preview_rgb[i] = static_cast<uint8_t>(i * 17 + i / 480); }
    if (argc == 2 && std::strcmp(argv[1], "stdin") == 0) {
        if (std::fread(g_preview_rgb, 1, sizeof(g_preview_rgb), stdin) != sizeof(g_preview_rgb) ||
            std::fgetc(stdin) != EOF) { return 2; }
    }
    const uint32_t before = handgpt::frame_crc32(g_preview_rgb, sizeof(g_preview_rgb));
    const bool success = camera_export_frame();
    assert(handgpt::frame_crc32(g_preview_rgb, sizeof(g_preview_rgb)) == before);
#ifdef TEST_WRAP_WRITES
    if (std::strcmp(scenario, "uart-during-export") == 0) { assert(g_smoke_status.uart_timeouts == 1); }
#endif
    return success ? 0 : 1;
}
