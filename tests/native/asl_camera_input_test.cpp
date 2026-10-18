#include "camera_input.h"
#include "frame_export.h"

#include <cassert>
#include <cstdio>
#include <vector>

int main() {
    std::vector<uint8_t> rgb(480 * 480 * 3);
    assert(std::fread(rgb.data(), 1, rgb.size(), stdin) == rgb.size());
    assert(std::fgetc(stdin) == EOF);
    std::vector<float> guarded(handgpt::kAslInputElements + 2, 123.0f);
    float* output = guarded.data() + 1;
    const auto before = handgpt::frame_crc32(rgb.data(), rgb.size());
    assert(!handgpt::asl_camera_input(nullptr, rgb.size(), output, handgpt::kAslInputElements));
    assert(!handgpt::asl_camera_input(rgb.data(), rgb.size(), nullptr, handgpt::kAslInputElements));
    assert(!handgpt::asl_camera_input(rgb.data(), rgb.size() - 1, output, handgpt::kAslInputElements));
    assert(!handgpt::asl_camera_input(rgb.data(), rgb.size(), output, handgpt::kAslInputElements - 1));
    assert(handgpt::asl_camera_input(rgb.data(), rgb.size(), output, handgpt::kAslInputElements));
    assert(guarded.front() == 123.0f && guarded.back() == 123.0f);
    assert(handgpt::frame_crc32(rgb.data(), rgb.size()) == before);
    assert(std::fwrite(output, sizeof(float), handgpt::kAslInputElements, stdout) == handgpt::kAslInputElements);
}
