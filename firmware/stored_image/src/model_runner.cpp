#include "image_check.h"
#include "image_config.h"
#include "smoke_platform.h"
#include "reference_model_pte.h"
#include "reference_input.h"
#include "reference_logits.h"

#include <executorch/runtime/core/data_loader.h>
#include <executorch/runtime/core/evalue.h>
#include <executorch/runtime/core/hierarchical_allocator.h>
#include <executorch/runtime/core/memory_allocator.h>
#include <executorch/runtime/executor/memory_manager.h>
#include <executorch/runtime/executor/method.h>
#include <executorch/runtime/executor/program.h>
#include <executorch/runtime/platform/runtime.h>

#include <cstdio>
#include <cstring>
#include <utility>

using namespace executorch::runtime;

extern "C" {
alignas(32) uint8_t g_image_method_pool[IMAGE_METHOD_BYTES];
__attribute__((section(".bss.handgpt_npu"), aligned(32))) uint8_t g_image_arena[IMAGE_ARENA_BYTES];
__attribute__((section(".bss.handgpt_npu"), aligned(32))) uint8_t g_image_temporary[IMAGE_TEMPORARY_BYTES];
__attribute__((section(".bss.handgpt_npu"), aligned(32))) float g_image_input[handgpt::kImageElements];
__attribute__((section(".bss.handgpt_npu"), aligned(32))) float g_image_output[handgpt::kClasses];
}

static_assert(sizeof(reference_input) == sizeof(g_image_input), "Image contract changed");
static_assert(sizeof(reference_logits) == sizeof(g_image_output), "Output contract changed");

// Isolate temporary allocations on cache-line boundaries, including tail padding:
// the backend requests only 16-byte alignment, but U85 cache hooks operate on 32.
class MeasuredAllocator final : public MemoryAllocator {
public:
    MeasuredAllocator(uint32_t size, uint8_t* data) : MemoryAllocator(size, data) {}
    void* allocate(size_t bytes, size_t alignment = kDefaultAlignment) override {
        size_t rounded, aligned;
        if (!handgpt::cache_allocation_request(bytes, alignment, rounded, aligned)) { return nullptr; }
        void* pointer = MemoryAllocator::allocate(rounded, aligned);
        if (pointer) {
            const size_t end = static_cast<uint8_t*>(pointer) - base_address() + rounded;
            if (end > high_water) { high_water = end; }
        }
        return pointer;
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

static bool fail(uint32_t error) { g_smoke_status.error = error; return false; }

static bool region(const void* pointer, size_t bytes, uintptr_t begin, uintptr_t end, size_t alignment) {
    return handgpt::in_region(reinterpret_cast<uintptr_t>(pointer), bytes, begin, end, alignment);
}

bool run_stored_image() {
    if (!region(reference_model_pte, sizeof(reference_model_pte), IMAGE_MRAM_BASE, IMAGE_MRAM_END, 16) ||
        !region(reference_input, sizeof(reference_input), IMAGE_MRAM_BASE, IMAGE_MRAM_END, 16) ||
        !region(reference_logits, sizeof(reference_logits), IMAGE_MRAM_BASE, IMAGE_MRAM_END, 16) ||
        !region(g_image_arena, sizeof(g_image_arena), 0x02000000, 0x02400000, 32) ||
        !region(g_image_temporary, sizeof(g_image_temporary), 0x02000000, 0x02400000, 32) ||
        !region(g_image_input, sizeof(g_image_input), 0x02000000, 0x02400000, 32) ||
        !region(g_image_output, sizeof(g_image_output), 0x02000000, 0x02400000, 32)) { return fail(10); }
    std::printf("model=%p bytes=%u arena=%p bytes=%u temporary=%p bytes=%u\n",
                static_cast<const void*>(reference_model_pte), reference_model_pte_len,
                static_cast<void*>(g_image_arena), static_cast<unsigned>(sizeof(g_image_arena)),
                static_cast<void*>(g_image_temporary), static_cast<unsigned>(sizeof(g_image_temporary)));
    runtime_init();
    g_smoke_status.stage = SmokeStage::RuntimeReady;
    ModelLoader loader;
    auto loaded = Program::load(&loader, Program::Verification::Minimal);
    if (!loaded.ok()) { return fail(11); }
    Program program(std::move(loaded.get()));
    auto metadata = program.method_meta("forward");
    if (!metadata.ok()) { return fail(12); }
    auto meta = metadata.get();
    if (meta.num_inputs() != 1 || meta.num_outputs() != 1 || !meta.uses_backend("EthosUBackend")) { return fail(13); }
    constexpr size_t kMaxBuffers = 4;
    const size_t count = meta.num_memory_planned_buffers();
    if (count > kMaxBuffers) { return fail(14); }
    Span<uint8_t> buffers[kMaxBuffers];
    size_t used = 0;
    for (size_t i = 0; i < count; ++i) {
        auto size = meta.memory_planned_buffer_size(i);
        if (!size.ok() || size.get() < 0) { return fail(15); }
        const size_t offset = (used + 31u) & ~size_t(31);
        const size_t bytes = static_cast<size_t>(size.get());
        if (offset > sizeof(g_image_arena) || bytes > sizeof(g_image_arena) - offset) { return fail(16); }
        buffers[i] = Span<uint8_t>(g_image_arena + offset, bytes);
        used = offset + bytes;
    }
    std::printf("planned_buffers=%u used_bytes=%u\n", static_cast<unsigned>(count), static_cast<unsigned>(used));
    HierarchicalAllocator planned(Span<Span<uint8_t>>(buffers, count));
    MeasuredAllocator method_allocator(sizeof(g_image_method_pool), g_image_method_pool);
    MeasuredAllocator temporary_allocator(sizeof(g_image_temporary), g_image_temporary);
    MemoryManager memory(&method_allocator, &planned, &temporary_allocator);
    g_smoke_status.stage = SmokeStage::Loading;
    std::printf("Loading forward method\n");
    auto loaded_method = program.load_method("forward", &memory);
    std::printf("method_high_water=%u/%u\n", static_cast<unsigned>(method_allocator.high_water), IMAGE_METHOD_BYTES);
    if (!loaded_method.ok()) { return fail(17); }
    Method method(std::move(loaded_method.get()));
    std::memcpy(g_image_input, reference_input, sizeof(g_image_input));
    executorch::aten::TensorImpl::SizesType sizes[] = {1, 3, 224, 224};
    executorch::aten::TensorImpl::DimOrderType order[] = {0, 1, 2, 3};
    executorch::aten::TensorImpl::StridesType strides[] = {150528, 50176, 224, 1};
    executorch::aten::TensorImpl implementation(executorch::aten::ScalarType::Float, 4, sizes, g_image_input, order, strides);
    executorch::aten::Tensor tensor(&implementation);
    if (method.set_input(EValue(tensor), 0) != Error::Ok) { return fail(18); }
    const uint32_t irq_before = g_smoke_status.npu_irqs;
    const uint32_t jobs_before = g_smoke_status.npu_jobs_started;
    const uint32_t ends_before = g_smoke_status.npu_jobs_finished;
    const uint32_t start = smoke_millis();
    g_smoke_status.stage = SmokeStage::Executing;
    const Error executed = method.execute();
    g_smoke_status.elapsed_ms = smoke_millis() - start;
    std::printf("temporary_high_water=%u/%u method_high_water=%u/%u execute_error=0x%x\n",
                static_cast<unsigned>(temporary_allocator.high_water), IMAGE_TEMPORARY_BYTES,
                static_cast<unsigned>(method_allocator.high_water), IMAGE_METHOD_BYTES, static_cast<unsigned>(executed));
    if (executed != Error::Ok) { return fail(19); }
    if (g_smoke_status.semaphore_timeouts || g_smoke_status.uart_timeouts ||
        g_smoke_status.npu_irqs == irq_before || g_smoke_status.npu_jobs_started == jobs_before ||
        g_smoke_status.npu_jobs_finished - ends_before != g_smoke_status.npu_jobs_started - jobs_before) { return fail(20); }
    const EValue& result = method.get_output(0);
    if (!result.isTensor()) { return fail(21); }
    const auto& output = result.toTensor();
    if (output.scalar_type() != executorch::aten::ScalarType::Float || output.dim() != 2 ||
        output.size(0) != 1 || output.size(1) != 1000) { return fail(22); }
    if (std::memcmp(g_image_input, reference_input, sizeof(g_image_input)) != 0) { return fail(24); }
    std::memcpy(g_image_output, output.const_data_ptr<float>(), sizeof(g_image_output));
    handgpt::LogitComparison comparison;
    const bool correct = handgpt::compare_logits(g_image_output, reference_logits, handgpt::kClasses,
                                                 IMAGE_MAX_LOGIT_ERROR, IMAGE_MIN_COSINE, comparison);
    g_smoke_status.checked_values = comparison.finite ? handgpt::kClasses : 0;
    g_smoke_status.max_error_millionths = comparison.finite && comparison.max_abs_error < 4294.0f
        ? static_cast<uint32_t>(comparison.max_abs_error * 1000000.0f) : UINT32_MAX;
    std::printf("comparison: finite=%u max_abs_error=%f rmse=%f cosine=%f board_top1=%u host_top1=%u\n",
                comparison.finite ? 1u : 0u, static_cast<double>(comparison.max_abs_error), comparison.rmse,
                comparison.cosine, static_cast<unsigned>(comparison.actual_top1), static_cast<unsigned>(comparison.reference_top1));
    bool selected[handgpt::kClasses] = {};
    if (comparison.finite) {
        for (size_t rank = 0; rank < 5; ++rank) {
            size_t best = 0;
            while (selected[best]) { ++best; }
            for (size_t i = 0; i < handgpt::kClasses; ++i) {
                if (!selected[i] && g_image_output[i] > g_image_output[best]) { best = i; }
            }
            selected[best] = true;
            const char* label = "(not in host top-5)";
            for (size_t i = 0; i < 5; ++i) { if (reference_top5_indices[i] == best) { label = reference_top5_labels[i]; } }
            std::printf("top%u index=%u label=%s logit=%f host_logit=%f\n", static_cast<unsigned>(rank + 1),
                        static_cast<unsigned>(best), label, static_cast<double>(g_image_output[best]),
                        static_cast<double>(reference_logits[best]));
        }
    }
#if IMAGE_DUMP_LOGITS
    for (size_t i = 0; i < handgpt::kClasses; ++i) {
        std::printf("LOGIT %u %f %f\n", static_cast<unsigned>(i), static_cast<double>(g_image_output[i]), static_cast<double>(reference_logits[i]));
    }
#endif
    if (g_smoke_status.uart_timeouts != 0) { return fail(20); }
    return correct ? true : fail(23);
}
