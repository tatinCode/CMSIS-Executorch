#include "smoke_check.h"
#include "smoke_platform.h"
#include "reference_model_pte.h"

#include <executorch/runtime/core/data_loader.h>
#include <executorch/runtime/core/evalue.h>
#include <executorch/runtime/core/hierarchical_allocator.h>
#include <executorch/runtime/core/memory_allocator.h>
#include <executorch/runtime/executor/memory_manager.h>
#include <executorch/runtime/executor/method.h>
#include <executorch/runtime/executor/program.h>
#include <executorch/runtime/platform/runtime.h>

#include <cstdio>
#include <utility>

using namespace executorch::runtime;

// Method metadata is CPU-only and can stay in DTCM. The pinned Ethos-U
// backend allocates NPU scratch (including its input/output) from the TEMPORARY
// allocator, not the planned arena: both pools below must be in shared SRAM0.
alignas(32) static uint8_t method_pool[128 * 1024];
extern "C" {
__attribute__((section(".bss.handgpt_npu"), aligned(32)))
uint8_t g_smoke_npu_arena[4096];
__attribute__((section(".bss.handgpt_npu"), aligned(32)))
uint8_t g_smoke_npu_temporary_pool[32 * 1024];
}

class ModelLoader final : public DataLoader {
public:
    Result<FreeableBuffer> load(size_t offset, size_t bytes,
                              const SegmentInfo&) const override {
        if (!handgpt::valid_range(offset, bytes, reference_model_pte_len)) {
            return Error::InvalidArgument;
        }
        return FreeableBuffer(reference_model_pte + offset, bytes, nullptr);
    }
    Result<size_t> size() const override { return reference_model_pte_len; }
};

static bool fail(uint32_t error) {
    g_smoke_status.error = error;
    return false;
}

bool run_smoke_model() {
    const uintptr_t model_address = reinterpret_cast<uintptr_t>(reference_model_pte);
    const uintptr_t arena_address = reinterpret_cast<uintptr_t>(g_smoke_npu_arena);
    const uintptr_t temporary_address = reinterpret_cast<uintptr_t>(g_smoke_npu_temporary_pool);
    if (model_address < 0x80200000u || model_address >= 0x80400000u ||
        (model_address & 15u) != 0 ||
        !handgpt::valid_sram0_buffer(arena_address, sizeof(g_smoke_npu_arena)) ||
        !handgpt::valid_sram0_buffer(temporary_address, sizeof(g_smoke_npu_temporary_pool))) {
        return fail(10);
    }
    if (arena_address < temporary_address + sizeof(g_smoke_npu_temporary_pool) &&
        temporary_address < arena_address + sizeof(g_smoke_npu_arena)) {
        return fail(10);
    }
    std::printf("model=%p bytes=%u arena=%p bytes=%u\n",
                static_cast<const void*>(reference_model_pte), reference_model_pte_len,
                static_cast<void*>(g_smoke_npu_arena), static_cast<unsigned>(sizeof(g_smoke_npu_arena)));
    std::printf("temporary_pool=%p bytes=%u (NPU scratch allocated here)\n",
                static_cast<void*>(g_smoke_npu_temporary_pool),
                static_cast<unsigned>(sizeof(g_smoke_npu_temporary_pool)));
    runtime_init();
    g_smoke_status.stage = SmokeStage::RuntimeReady;
    ModelLoader loader;
    // The pinned bare-metal runtime disables optional full FlatBuffer verification.
    // Host manifest/metadata checks precede embedding; request the supported mode.
    auto loaded = Program::load(&loader, Program::Verification::Minimal);
    if (!loaded.ok()) { return fail(11); }
    Program program(std::move(loaded.get()));
    auto metadata = program.method_meta("forward");
    if (!metadata.ok()) { return fail(12); }
    auto meta = metadata.get();
    if (meta.num_inputs() != 1 || meta.num_outputs() != 1 || !meta.uses_backend("EthosUBackend")) {
        return fail(13);
    }
    constexpr size_t kMaxBuffers = 4;
    const size_t count = meta.num_memory_planned_buffers();
    if (count > kMaxBuffers) { return fail(14); }
    Span<uint8_t> buffers[kMaxBuffers];
    size_t used = 0;
    for (size_t i = 0; i < count; ++i) {
        auto bytes = meta.memory_planned_buffer_size(i);
        if (!bytes.ok() || bytes.get() < 0) { return fail(15); }
        const size_t offset = (used + 31u) & ~size_t(31);
        if (!handgpt::valid_range(offset, static_cast<size_t>(bytes.get()), sizeof(g_smoke_npu_arena))) {
            return fail(16);
        }
        buffers[i] = Span<uint8_t>(g_smoke_npu_arena + offset, static_cast<size_t>(bytes.get()));
        used = offset + static_cast<size_t>(bytes.get());
    }
    std::printf("planned_buffers=%u used_bytes=%u\n", static_cast<unsigned>(count), static_cast<unsigned>(used));
    HierarchicalAllocator planned(Span<Span<uint8_t>>(buffers, count));
    MemoryAllocator method_allocator(sizeof(method_pool), method_pool);
    MemoryAllocator temporary_allocator(sizeof(g_smoke_npu_temporary_pool), g_smoke_npu_temporary_pool);
    MemoryManager memory(&method_allocator, &planned, &temporary_allocator);
    g_smoke_status.stage = SmokeStage::Loading;
    auto loaded_method = program.load_method("forward", &memory);
    if (!loaded_method.ok()) { return fail(17); }
    Method method(std::move(loaded_method.get()));

    float input[handgpt::kSmokeElements];
    float reference_input[handgpt::kSmokeElements];
    for (size_t i = 0; i < handgpt::kSmokeElements; ++i) {
        input[i] = -1.0f + 2.0f * static_cast<float>(i) / 15.0f;
        reference_input[i] = input[i];
    }
    executorch::aten::TensorImpl::SizesType sizes[] = {1, 16};
    executorch::aten::TensorImpl::DimOrderType order[] = {0, 1};
    executorch::aten::TensorImpl::StridesType strides[] = {16, 1};
    executorch::aten::TensorImpl implementation(
        executorch::aten::ScalarType::Float, 2, sizes, input, order, strides);
    executorch::aten::Tensor tensor(&implementation);
    if (method.set_input(EValue(tensor), 0) != Error::Ok) { return fail(18); }

    const uint32_t irq_before = g_smoke_status.npu_irqs;
    const uint32_t jobs_before = g_smoke_status.npu_jobs_started;
    const uint32_t ends_before = g_smoke_status.npu_jobs_finished;
    const uint32_t start = smoke_millis();
    g_smoke_status.stage = SmokeStage::Executing;
    const Error executed = method.execute();
    g_smoke_status.elapsed_ms = smoke_millis() - start;
    if (executed != Error::Ok) { return fail(19); }
    if (g_smoke_status.semaphore_timeouts != 0 ||
        g_smoke_status.npu_irqs == irq_before ||
        g_smoke_status.npu_jobs_started == jobs_before ||
        g_smoke_status.npu_jobs_finished - ends_before != g_smoke_status.npu_jobs_started - jobs_before) {
        return fail(20);
    }
    const EValue& result = method.get_output(0);
    if (!result.isTensor()) { return fail(21); }
    const auto& output = result.toTensor();
    if (output.scalar_type() != executorch::aten::ScalarType::Float ||
        output.dim() != 2 || output.size(0) != 1 || output.size(1) != 16) {
        return fail(22);
    }
    float max_error = 0.0f;
    const float* values = output.const_data_ptr<float>();
    for (size_t i = 0; i < handgpt::kSmokeElements; ++i) {
        if (input[i] != reference_input[i]) { return fail(24); }
    }
    const bool correct = handgpt::check_doubled_input(reference_input, values, output.numel(), &max_error);
    if (std::isfinite(max_error) && max_error <= 4294.0f) {
        g_smoke_status.max_error_millionths = static_cast<uint32_t>(max_error * 1000000.0f);
    } else {
        g_smoke_status.max_error_millionths = UINT32_MAX;
    }
    for (size_t i = 0; i < handgpt::kSmokeElements; ++i) {
        std::printf("[%u] input=%f output=%f expected=%f\n", static_cast<unsigned>(i),
                    static_cast<double>(input[i]), static_cast<double>(values[i]), static_cast<double>(2.0f * input[i]));
    }
    g_smoke_status.checked_values = handgpt::kSmokeElements;
    return correct ? true : fail(23);
}
