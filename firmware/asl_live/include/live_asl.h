#pragma once

#include "asl_display.h"

// True only after the fixed-input ASL boot test and repeated-run checks pass.
bool live_asl_init();
bool live_asl_ready();
bool live_asl_faulted();
void live_asl_predict(const uint8_t* rgb, size_t bytes);
handgpt::AslLetter live_asl_letter(uint32_t now);
uint32_t live_asl_predictions();
uint32_t live_asl_latency();
// Best effort on camera/runtime fatal errors; cannot fix failed display hardware.
bool live_asl_blank_display();
bool live_asl_selftest_failure(uint32_t code);
