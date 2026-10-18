#pragma once

#include <cstddef>
#include <cstdint>

// Exactly one CRC-checked frame export happens per boot; the host triggers it
// only by pressing the board reset. There is no host-to-board UART input.
bool camera_export_frame(); // Call only after a successful preview_frame().

namespace handgpt {
inline uint32_t frame_crc32_update(uint32_t crc, const uint8_t* data, size_t size) {
    for (size_t i = 0; i < size; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1) ^ ((crc & 1u) ? 0xedb88320u : 0u);
        }
    }
    return crc;
}
inline uint32_t frame_crc32(const uint8_t* data, size_t size) {
    return frame_crc32_update(0xffffffffu, data, size) ^ 0xffffffffu;
}
} // namespace handgpt
