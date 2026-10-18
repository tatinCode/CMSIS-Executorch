#pragma once
// Post-hoc gate revision approved after first hardware failure.
namespace handgpt {
constexpr unsigned kReferenceGateRevision = 2;
constexpr double kReferenceOutputScale = 0.22613441944122314;
constexpr unsigned kReferenceMaxOutputSteps = 8;
constexpr double kReferenceMinimumCosine = 0.999;
constexpr float kReferenceThreshold = 0.8f;
}
