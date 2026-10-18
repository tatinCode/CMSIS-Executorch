#include "live_asl.h"
#include "live_checks.h"
#include "camera_input.h"
#include "image_check.h"
#include "image_config.h"
#include "reference_model_pte.h"
#include "reference_input.h"
#include "reference_logits.h"
#include "reference_gate.h"
#include "smoke_platform.h"

#include <executorch/runtime/core/data_loader.h>
#include <executorch/runtime/core/evalue.h>
#include <executorch/runtime/core/hierarchical_allocator.h>
#include <executorch/runtime/core/memory_allocator.h>
#include <executorch/runtime/executor/memory_manager.h>
#include <executorch/runtime/executor/method.h>
#include <executorch/runtime/executor/program.h>
#include <executorch/runtime/platform/runtime.h>

#include <cmath>
#include <cstdio>
#include <cstring>
#include <optional>
#include <utility>

using namespace executorch::runtime;

// Keep ALL measured reservations and the existing layout-verifier symbols.
// Only the first 128x128x3 inputs / kAslClassCount outputs are used by ASL.
extern "C" {
alignas(32) uint8_t g_image_method_pool[IMAGE_METHOD_BYTES];
__attribute__((section(".bss.handgpt_npu"), aligned(32))) uint8_t g_image_arena[IMAGE_ARENA_BYTES];
__attribute__((section(".bss.handgpt_npu"), aligned(32))) uint8_t g_image_temporary[IMAGE_TEMPORARY_BYTES];
__attribute__((section(".bss.handgpt_npu"), aligned(32))) float g_image_input[handgpt::kImageElements];
__attribute__((section(".bss.handgpt_npu"), aligned(32))) float g_image_output[handgpt::kClasses];
}
static_assert(sizeof(reference_input) == sizeof(g_image_input), "Reference reservation changed");
static_assert(sizeof(reference_logits) == sizeof(g_image_output), "Output reservation changed");
static_assert(handgpt::kAslClassCount <= handgpt::kClasses, "ASL exceeds preserved output reservation");
static_assert(handgpt::kReferenceGateRevision == 2 &&
              handgpt::kReferenceMaxOutputSteps == handgpt::kAslMaxOutputSteps &&
              handgpt::kReferenceMinimumCosine == handgpt::kAslMinimumCosine &&
              handgpt::kReferenceThreshold == handgpt::kAslSelftestThreshold,
              "Generated reference and firmware self-test policies disagree");

namespace {
class MeasuredAllocator final : public MemoryAllocator {
public:
    MeasuredAllocator(uint32_t size, uint8_t* data) : MemoryAllocator(size, data) {}
    void* allocate(size_t bytes, size_t alignment = kDefaultAlignment) override {
        size_t padded, aligned;
        if (!handgpt::cache_allocation_request(bytes, alignment, padded, aligned)) { return nullptr; }
        void* result = MemoryAllocator::allocate(padded, aligned);
        if (result) {
            const size_t end = static_cast<uint8_t*>(result) - base_address() + padded;
            if (end > high_water) { high_water = end; }
        }
        return result;
    }
    size_t high_water = 0;
};

class ModelLoader final : public DataLoader {
public:
    Result<FreeableBuffer> load(size_t offset, size_t bytes, const SegmentInfo&) const override {
        if (offset > reference_model_pte_len || bytes > reference_model_pte_len - offset) { return Error::InvalidArgument; }
        return FreeableBuffer(reference_model_pte + offset, bytes, nullptr);
    }
    Result<size_t> size() const override { return reference_model_pte_len; }
};

ModelLoader loader;
MeasuredAllocator method_allocator(sizeof(g_image_method_pool), g_image_method_pool);
MeasuredAllocator temporary_allocator(sizeof(g_image_temporary), g_image_temporary);
Span<uint8_t> buffers[4];
std::optional<HierarchicalAllocator> planned;
std::optional<MemoryManager> memory;
std::optional<Program> program;
std::optional<Method> method;
executorch::aten::TensorImpl::SizesType sizes[] = {1, 3, 128, 128};
executorch::aten::TensorImpl::DimOrderType order[] = {0, 1, 2, 3};
executorch::aten::TensorImpl::StridesType strides[] = {49152, 16384, 128, 1};
executorch::aten::TensorImpl implementation(executorch::aten::ScalarType::Float, 4, sizes, g_image_input, order, strides);
executorch::aten::Tensor tensor(&implementation);
handgpt::StableLetter stable;
bool ready, fault;
uint32_t predictions, latency;

bool reject(uint32_t code) {
    stable.observe(handgpt::AslLetter::None, smoke_millis());
    if (!fault) { std::printf("LIVE ASL INFERENCE FAULT error=%lu; letters disabled until reset\n", static_cast<unsigned long>(code)); }
    fault = true;
    if (!g_smoke_status.error) { g_smoke_status.error = code; }
    return false;
}

bool execute() {
    if (!method || method->set_input(EValue(tensor), 0) != Error::Ok) { return reject(310); }
    const auto irqs = g_smoke_status.npu_irqs;
    const auto starts = g_smoke_status.npu_jobs_started;
    const auto ends = g_smoke_status.npu_jobs_finished;
    const auto start = smoke_millis();
    const auto error = method->execute();
    latency = smoke_millis() - start;
    if (error != Error::Ok || g_smoke_status.semaphore_timeouts || g_smoke_status.uart_timeouts ||
        g_smoke_status.npu_irqs == irqs || g_smoke_status.npu_jobs_started == starts ||
        g_smoke_status.npu_jobs_finished - ends != g_smoke_status.npu_jobs_started - starts) { return reject(311); }
    const auto& result = method->get_output(0);
    if (!result.isTensor()) { return reject(312); }
    const auto& out = result.toTensor();
    if (out.scalar_type() != executorch::aten::ScalarType::Float || out.dim() != 2 ||
        out.size(0) != 1 || out.size(1) != handgpt::kAslClassCount) { return reject(313); }
    std::memcpy(g_image_output, out.const_data_ptr<float>(), handgpt::kAslClassCount * sizeof(float));
    for (unsigned i = 0; i < handgpt::kAslClassCount; ++i) { if (!std::isfinite(g_image_output[i])) { return reject(314); } }
    return true;
}
} // namespace

bool live_asl_init() {
    if (ready || fault || program || method) { return reject(315); }
    std::printf("ASL GATE revision=2 output_scale=%.12f max_output_steps=%u min_cosine=0.999 threshold=0.80 post_hoc=1\n",
                handgpt::kReferenceOutputScale, handgpt::kReferenceMaxOutputSteps);
    runtime_init();
    auto loaded = Program::load(&loader, Program::Verification::Minimal);
    if (!loaded.ok()) { return reject(316); }
    program.emplace(std::move(loaded.get()));
    auto metadata = program->method_meta("forward");
    if (!metadata.ok()) { return reject(317); }
    const auto meta = metadata.get();
    auto input = meta.input_tensor_meta(0);
    auto output = meta.output_tensor_meta(0);
    if (meta.num_inputs() != 1 || meta.num_outputs() != 1 || !meta.uses_backend("EthosUBackend") ||
        !input.ok() || !output.ok()) { return reject(318); }
    const auto in = input.get(), out = output.get();
    if (in.scalar_type() != executorch::aten::ScalarType::Float || in.sizes().size() != 4 ||
        in.dim_order().size() != 4 || out.scalar_type() != executorch::aten::ScalarType::Float ||
        out.sizes().size() != 2 || out.sizes()[0] != 1 || out.sizes()[1] != handgpt::kAslClassCount ||
        out.dim_order().size() != 2 || out.dim_order()[0] != 0 || out.dim_order()[1] != 1) { return reject(319); }
    for (unsigned i = 0; i < 4; ++i) {
        if (in.sizes()[i] != sizes[i] || in.dim_order()[i] != order[i]) { return reject(319); }
    }
    const size_t count = meta.num_memory_planned_buffers();
    if (!count || count > 4) { return reject(320); }
    size_t used = 0;
    for (size_t i = 0; i < count; ++i) {
        auto bytes = meta.memory_planned_buffer_size(i);
        if (!bytes.ok() || bytes.get() <= 0) { return reject(321); }
        const size_t offset = (used + 31u) & ~size_t(31);
        const size_t size = static_cast<size_t>(bytes.get());
        if (offset > sizeof(g_image_arena) || size > sizeof(g_image_arena) - offset) { return reject(322); }
        buffers[i] = Span<uint8_t>(g_image_arena + offset, size);
        used = offset + size;
    }
    planned.emplace(Span<Span<uint8_t>>(buffers, count));
    memory.emplace(&method_allocator, &*planned, &temporary_allocator);
    auto loaded_method = program->load_method("forward", &*memory);
    if (!loaded_method.ok()) { return reject(323); }
    method.emplace(std::move(loaded_method.get()));
    std::memcpy(g_image_input, reference_input, sizeof(g_image_input));
    size_t first_method = 0, first_temporary = 0;
    for (unsigned repeat = 0; repeat < 3; ++repeat) {
        if (!execute() || std::memcmp(g_image_input, reference_input, sizeof(g_image_input)) != 0) { return reject(324); }
        if (repeat == 0) {
            first_method = method_allocator.high_water;
            first_temporary = temporary_allocator.high_water;
        } else if (first_method != method_allocator.high_water || first_temporary != temporary_allocator.high_water) {
            return reject(328); // Detect repeated-run allocator growth before live inference.
        }
        double error, cosine;
        const bool match = handgpt::asl_reference_matches(g_image_output, reference_logits, handgpt::kAslClassCount,
                            handgpt::kReferenceOutputScale, error, cosine);
        std::printf("ASL SELFTEST repeat=%u max_error=%f cosine=%f method_high_water=%u temporary_high_water=%u output_steps=%f\n",
                    repeat + 1, error, cosine, static_cast<unsigned>(method_allocator.high_water),
                    static_cast<unsigned>(temporary_allocator.high_water), error / handgpt::kReferenceOutputScale);
        for (unsigned i = 0; i < handgpt::kAslClassCount; ++i) {
            std::printf("ASL LOGIT %u actual=%f host=%f\n", i, static_cast<double>(g_image_output[i]), static_cast<double>(reference_logits[i]));
        }
        if (!match || g_smoke_status.uart_timeouts) { return reject(325); }
    }
    stable.observe(handgpt::AslLetter::None, smoke_millis());
    ready = true;
    std::printf("ASL SELFTEST PASS checked=%u repeats=3; reference predictions NOT shown as live\n",
                static_cast<unsigned>(handgpt::kAslClassCount));
    return g_smoke_status.uart_timeouts ? reject(327) : true;
}

bool live_asl_ready() { return ready && !fault; }
bool live_asl_faulted() { return fault; }
handgpt::AslLetter live_asl_letter(uint32_t now) { return stable.current(now); }
uint32_t live_asl_predictions() { return predictions; }
uint32_t live_asl_latency() { return latency; }

void live_asl_predict(const uint8_t* rgb, size_t bytes) {
    if (!live_asl_ready()) { stable.observe(handgpt::AslLetter::None, smoke_millis()); return; }
    const uint32_t start = smoke_millis();
    if (!handgpt::asl_camera_input(rgb, bytes, g_image_input, handgpt::kAslInputElements) || !execute()) {
        reject(326);
        return;
    }
    latency = smoke_millis() - start;
    ++predictions;
    stable.observe(handgpt::letter_from_logits(g_image_output, handgpt::kAslClassCount, 0.8f), smoke_millis());
}
