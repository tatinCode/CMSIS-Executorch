#pragma once

#include <stdint.h>

bool preview_power_init();
bool preview_init();
bool preview_frame();
uint32_t preview_frames();
uint32_t preview_scanlines();
