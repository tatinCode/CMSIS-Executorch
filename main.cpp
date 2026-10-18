#include "RTE_Components.h"
#include CMSIS_device_header

#include "Driver_IO.h"
#include "board_config.h"
#include "SEGGER_RTT.h"
#include "ethosu_driver.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ExecuTorch headers
#include <executorch/runtime/platform/runtime.h>
#include <executorch/runtime/platform/platform.h>
#include <executorch/runtime/core/data_loader.h>
#include <executorch/runtime/core/memory_allocator.h>
#include <executorch/runtime/core/hierarchical_allocator.h>
#include <executorch/runtime/executor/program.h>
#include <executorch/runtime/executor/method.h>
#include <executorch/runtime/executor/memory_manager.h>
#include <executorch/runtime/executor/method_meta.h>
#include <executorch/runtime/core/evalue.h>

// Embedded model and image data
#include "assets/mv2_ethosu85_256_pte.h"
#include "assets/input_image.h"

using namespace executorch::runtime;

// ---------------------------------------------------------------------------
// Driver macros
// ---------------------------------------------------------------------------
#define _GET_DRIVER_REF(ref, peri, chan) \
    extern ARM_DRIVER_##peri Driver_##peri##chan; \
    static ARM_DRIVER_##peri * ref = &Driver_##peri##chan;
#define GET_DRIVER_REF(ref, peri, chan) _GET_DRIVER_REF(ref, peri, chan)

#if defined(BOARD_RGB_LED_INSTANCE) && (BOARD_RGB_LED_INSTANCE == 0)
    GET_DRIVER_REF(gpio_b, GPIO, BOARD_LEDRGB0_B_GPIO_PORT);
    GET_DRIVER_REF(gpio_g, GPIO, BOARD_LEDRGB0_G_GPIO_PORT);
    GET_DRIVER_REF(gpio_r, GPIO, BOARD_LEDRGB0_R_GPIO_PORT);
    #define BOARD_LEDRGB_B_GPIO_PIN BOARD_LEDRGB0_B_GPIO_PIN
    #define BOARD_LEDRGB_G_GPIO_PIN BOARD_LEDRGB0_G_GPIO_PIN
    #define BOARD_LEDRGB_R_GPIO_PIN BOARD_LEDRGB0_R_GPIO_PIN
#else
    GET_DRIVER_REF(gpio_b, GPIO, BOARD_LEDRGB1_B_GPIO_PORT);
    GET_DRIVER_REF(gpio_g, GPIO, BOARD_LEDRGB1_G_GPIO_PORT);
    GET_DRIVER_REF(gpio_r, GPIO, BOARD_LEDRGB1_R_GPIO_PORT);
    #define BOARD_LEDRGB_B_GPIO_PIN BOARD_LEDRGB1_B_GPIO_PIN
    #define BOARD_LEDRGB_G_GPIO_PIN BOARD_LEDRGB1_G_GPIO_PIN
    #define BOARD_LEDRGB_R_GPIO_PIN BOARD_LEDRGB1_R_GPIO_PIN
#endif

// ---------------------------------------------------------------------------
// Fault handlers — print stacked PC/LR to identify exact crash location
// ---------------------------------------------------------------------------
extern "C" void Fault_Print(uint32_t *frame, uint32_t fault_type) {
    const char* names[] = {"HARDFAULT", "MEMMANAGE", "BUSFAULT"};
    SEGGER_RTT_printf(0, "*** %s ***\n", names[fault_type]);
    volatile uint32_t cfsr  = *(volatile uint32_t *)0xE000ED28;
    volatile uint32_t hfsr  = *(volatile uint32_t *)0xE000ED2C;
    volatile uint32_t mmfar = *(volatile uint32_t *)0xE000ED34;
    volatile uint32_t bfar  = *(volatile uint32_t *)0xE000ED38;
    SEGGER_RTT_printf(0, "CFSR=0x%08x HFSR=0x%08x\n", cfsr, hfsr);
    SEGGER_RTT_printf(0, "MMFAR=0x%08x BFAR=0x%08x\n", mmfar, bfar);
    SEGGER_RTT_printf(0, "PC=0x%08x LR=0x%08x\n", frame[6], frame[5]);
    SEGGER_RTT_printf(0, "R0=0x%08x R1=0x%08x R2=0x%08x R3=0x%08x\n",
                      frame[0], frame[1], frame[2], frame[3]);
    while (1) { __WFI(); }
}

extern "C" __attribute__((naked)) void HardFault_Handler(void) {
    __asm volatile(
        "TST LR, #4\n"
        "ITE EQ\n"
        "MRSEQ R0, MSP\n"
        "MRSNE R0, PSP\n"
        "MOV R1, #0\n"
        "B Fault_Print\n"
    );
}

extern "C" __attribute__((naked)) void MemManage_Handler(void) {
    __asm volatile(
        "TST LR, #4\n"
        "ITE EQ\n"
        "MRSEQ R0, MSP\n"
        "MRSNE R0, PSP\n"
        "MOV R1, #1\n"
        "B Fault_Print\n"
    );
}

extern "C" __attribute__((naked)) void BusFault_Handler(void) {
    __asm volatile(
        "TST LR, #4\n"
        "ITE EQ\n"
        "MRSEQ R0, MSP\n"
        "MRSNE R0, PSP\n"
        "MOV R1, #2\n"
        "B Fault_Print\n"
    );
}

// ---------------------------------------------------------------------------
// Ethos-U NPU driver — using NPU_HG (Ethos-U85) at 0x49042000
// ---------------------------------------------------------------------------
static struct ethosu_driver ethos_drv;

// NPU_HG STATUS register: base + 0x04, bit 1 = irq_raised
#define NPU_HG_STATUS (*(volatile uint32_t *)(NPU_HG_BASE + 0x04))

// Override weak ethosu semaphore to poll NPU_HG status directly.
// The baremetal default uses __WFE() + IRQ, but NPU_HG has no IRQ to M55_HP.
struct ethosu_sem_t { uint8_t count; };
static volatile uint32_t sem_take_count = 0;

extern "C" int ethosu_semaphore_take(void *sem, uint64_t timeout) {
    (void)timeout;
    struct ethosu_sem_t *s = (struct ethosu_sem_t *)sem;
    sem_take_count++;
    SEGGER_RTT_printf(0, "[sem_take #%u] count=%u\n", sem_take_count, s->count);
    uint32_t polls = 0;
    while (s->count == 0) {
        // Poll NPU_HG STATUS: bit 1 = irq_raised
        if (NPU_HG_STATUS & 0x2) {
            ethosu_irq_handler(&ethos_drv);
        }
        polls++;
        if (polls % 2000000 == 0) {
            SEGGER_RTT_printf(0, "[sem_take] polls=%u status=0x%08x\n",
                              polls, NPU_HG_STATUS);
        }
        __NOP();
    }
    s->count--;
    return 0;
}

// NPU_HP IRQ handler (not used for NPU_HG, but kept for safety)
extern "C" void NPU_HP_IRQHandler(void) {
}

static int npu_init(void) {
    if (ethosu_init(&ethos_drv,
                    (void*)NPU_HG_BASE,   // Ethos-U85 base address
                    0,    // no fast memory
                    0,    // fast memory size
                    1,    // secure
                    1))   // privileged
    {
        SEGGER_RTT_printf(0, "ERROR: ethosu_init failed\n");
        return -1;
    }
    // No NVIC_EnableIRQ — NPU_HG has no IRQ routed to M55_HP NVIC.
    // Completion is polled via SysTick instead.
    SEGGER_RTT_printf(0, "Ethos-U85 NPU initialized (polling mode)\n");
    return 0;
}

// ---------------------------------------------------------------------------
// PAL (Platform Abstraction Layer) implementations
// ---------------------------------------------------------------------------
extern "C" {

void et_pal_init(void) {}

ET_NORETURN void et_pal_abort(void) {
    SEGGER_RTT_printf(0, "et_pal_abort() called!\n");
    __BKPT(0);
    while (1) {}
}

et_timestamp_t et_pal_current_ticks(void) {
    return 0;
}

et_tick_ratio_t et_pal_ticks_to_ns_multiplier(void) {
    return {1, 1};
}

void et_pal_emit_log_message(
    et_timestamp_t timestamp,
    et_pal_log_level_t level,
    const char* filename,
    const char* function,
    size_t line,
    const char* message,
    size_t length) {
    (void)timestamp;
    (void)filename;
    (void)function;
    (void)line;
    SEGGER_RTT_printf(0, "[%c] %s\n", (char)level, message);
}

void* et_pal_allocate(size_t size) {
    return malloc(size);
}

void et_pal_free(void* ptr) {
    free(ptr);
}

} // extern "C"

// ---------------------------------------------------------------------------
// BufferDataLoader — loads from an in-memory buffer (embedded .pte)
// ---------------------------------------------------------------------------
class BufferDataLoader : public DataLoader {
public:
    BufferDataLoader(const void* data, size_t size)
        : data_(static_cast<const uint8_t*>(data)), size_(size) {}

    Result<FreeableBuffer> load(
        size_t offset,
        size_t size,
        const SegmentInfo& segment_info) const override {
        (void)segment_info;
        if (offset + size > size_) {
            return Error::InvalidArgument;
        }
        return FreeableBuffer(data_ + offset, size, /*free_fn=*/nullptr);
    }

    Result<size_t> size() const override {
        return size_;
    }

private:
    const uint8_t* data_;
    size_t size_;
};

// ---------------------------------------------------------------------------
// Memory pools in SRAM0 for ExecuTorch
// ---------------------------------------------------------------------------
#define METHOD_ALLOC_POOL_SIZE (1536 * 1024)       // 1.5 MB for Method loading
#define TEMP_ALLOC_POOL_SIZE   (1536 * 1024)       // 1.5 MB for temp/scratch
#define PLANNED_POOL_SIZE      (4 * 1024 * 1024)  // 4 MB for planned memory
#define MAX_PLANNED_BUFFERS    16

// SRAM0 (4 MB): method allocator + temp allocator = 3 MB
__attribute__((section(".bss.at_sram0"), aligned(16)))
static uint8_t method_alloc_pool[METHOD_ALLOC_POOL_SIZE];

__attribute__((section(".bss.at_sram0"), aligned(16)))
static uint8_t temp_alloc_pool[TEMP_ALLOC_POOL_SIZE];

// Float input buffer for model (model expects float, image stored as int8)
// 3*224*224 = 150528 floats = 602112 bytes
__attribute__((section(".bss.at_sram0"), aligned(16)))
static float input_float_buf[3 * 224 * 224];

// SRAM1 (4 MB): planned memory buffers
__attribute__((section(".bss.at_sram1"), aligned(16)))
static uint8_t planned_buffer_pool[PLANNED_POOL_SIZE];

// ---------------------------------------------------------------------------
// run_inference — full ExecuTorch MobileNetV2 inference
// ---------------------------------------------------------------------------
static void run_inference(void) {
    SEGGER_RTT_printf(0, "Model bytes: %u\n", (unsigned)mv2_ethosu85_256_pte_len);
    SEGGER_RTT_printf(0, "Image bytes: %u\n", (unsigned)input_image_len);

    // 1. Initialize ExecuTorch runtime
    runtime_init();
    SEGGER_RTT_printf(0, "ExecuTorch runtime initialized\n");

    // 2. Create data loader from embedded model
    BufferDataLoader loader(mv2_ethosu85_256_pte, mv2_ethosu85_256_pte_len);

    // 3. Load the program
    auto program_result = Program::load(&loader, Program::Verification::Minimal);
    if (!program_result.ok()) {
        SEGGER_RTT_printf(0, "ERROR: Failed to load program (error %d)\n",
                          (int)program_result.error());
        return;
    }
    Program program(std::move(program_result.get()));
    SEGGER_RTT_printf(0, "Program loaded successfully\n");

    // 4. Query method metadata to size planned buffers
    auto meta_result = program.method_meta("forward");
    if (!meta_result.ok()) {
        SEGGER_RTT_printf(0, "ERROR: Failed to get method meta (error %d)\n",
                          (int)meta_result.error());
        return;
    }
    MethodMeta meta = meta_result.get();
    SEGGER_RTT_printf(0, "Method 'forward': %u inputs, %u outputs, %u planned buffers\n",
                      (unsigned)meta.num_inputs(),
                      (unsigned)meta.num_outputs(),
                      (unsigned)meta.num_memory_planned_buffers());

    // 5. Set up planned memory buffers
    size_t num_planned = meta.num_memory_planned_buffers();
    if (num_planned > MAX_PLANNED_BUFFERS) {
        SEGGER_RTT_printf(0, "ERROR: Too many planned buffers: %u\n", (unsigned)num_planned);
        return;
    }

    Span<uint8_t> planned_spans[MAX_PLANNED_BUFFERS];
    size_t planned_offset = 0;
    for (size_t i = 0; i < num_planned; i++) {
        auto size_result = meta.memory_planned_buffer_size(i);
        if (!size_result.ok()) {
            SEGGER_RTT_printf(0, "ERROR: Failed to get planned buffer size %u\n", (unsigned)i);
            return;
        }
        int64_t buf_size = size_result.get();
        SEGGER_RTT_printf(0, "  Planned buffer[%u]: %u bytes\n", (unsigned)i, (unsigned)buf_size);

        // Align to 16 bytes
        size_t aligned_offset = (planned_offset + 15) & ~(size_t)15;
        if (aligned_offset + (size_t)buf_size > sizeof(planned_buffer_pool)) {
            SEGGER_RTT_printf(0, "ERROR: Planned buffer pool overflow at buffer %u\n", (unsigned)i);
            return;
        }
        planned_spans[i] = Span<uint8_t>(planned_buffer_pool + aligned_offset, (size_t)buf_size);
        planned_offset = aligned_offset + (size_t)buf_size;
    }

    HierarchicalAllocator planned_memory(
        Span<Span<uint8_t>>(planned_spans, num_planned));

    // 6. Set up method and temp allocators
    MemoryAllocator method_allocator(METHOD_ALLOC_POOL_SIZE, method_alloc_pool);
    MemoryAllocator temp_allocator(TEMP_ALLOC_POOL_SIZE, temp_alloc_pool);

    MemoryManager memory_manager(&method_allocator, &planned_memory, &temp_allocator);

    // 7. Load the method
    SEGGER_RTT_printf(0, "Loading method 'forward'...\n");
    auto method_result = program.load_method("forward", &memory_manager);
    if (!method_result.ok()) {
        SEGGER_RTT_printf(0, "ERROR: Failed to load method (error %d)\n",
                          (int)method_result.error());
        return;
    }
    Method method(std::move(method_result.get()));
    SEGGER_RTT_printf(0, "Method loaded, %u inputs, %u outputs\n",
                      (unsigned)method.inputs_size(),
                      (unsigned)method.outputs_size());

    // 8. Prepare input tensor
    // Model expects float input (it has quantize_per_tensor as first op).
    // Convert int8 image data to float.
    const int8_t* src = (const int8_t*)input_image;
    for (size_t i = 0; i < 3 * 224 * 224; i++) {
        input_float_buf[i] = (float)src[i];
    }
    SEGGER_RTT_printf(0, "Converted input to float32\n");

    executorch::aten::TensorImpl::SizesType input_sizes[] = {1, 3, 224, 224};
    executorch::aten::TensorImpl::DimOrderType input_dim_order[] = {0, 1, 2, 3};
    executorch::aten::TensorImpl::StridesType input_strides[] = {
        3 * 224 * 224,
        224 * 224,
        224,
        1
    };

    executorch::aten::TensorImpl input_impl(
        executorch::aten::ScalarType::Float,  // float32
        4,                                     // ndim
        input_sizes,
        (void*)input_float_buf,
        input_dim_order,
        input_strides);
    executorch::aten::Tensor input_tensor(&input_impl);

    EValue input_evalue(input_tensor);
    Error err = method.set_input(input_evalue, 0);
    if (err != Error::Ok) {
        SEGGER_RTT_printf(0, "ERROR: Failed to set input (error %d)\n", (int)err);
        return;
    }
    SEGGER_RTT_printf(0, "Input tensor set (1x3x224x224 float32)\n");

    // 9. Execute inference
    SEGGER_RTT_printf(0, "Running inference...\n");
    err = method.execute();
    if (err != Error::Ok) {
        SEGGER_RTT_printf(0, "ERROR: Inference failed (error %d)\n", (int)err);
        return;
    }
    SEGGER_RTT_printf(0, "Inference complete!\n");

    // 10. Read output and find argmax
    const EValue& output_evalue = method.get_output(0);
    if (!output_evalue.isTensor()) {
        SEGGER_RTT_printf(0, "ERROR: Output is not a tensor\n");
        return;
    }
    const executorch::aten::Tensor& output_tensor = output_evalue.toTensor();
    ssize_t num_classes = output_tensor.numel();
    SEGGER_RTT_printf(0, "Output tensor: %d elements\n", (int)num_classes);

    // Find top-1 class — output could be int8 or float, handle both
    int best_idx = 0;
    if (output_tensor.scalar_type() == executorch::aten::ScalarType::Char) {
        const int8_t* scores = output_tensor.const_data_ptr<int8_t>();
        int8_t best_score = scores[0];
        for (ssize_t i = 1; i < num_classes; i++) {
            if (scores[i] > best_score) {
                best_score = scores[i];
                best_idx = (int)i;
            }
        }
        SEGGER_RTT_printf(0, "Top-1 class: %d (score: %d)\n", best_idx, (int)best_score);
    } else if (output_tensor.scalar_type() == executorch::aten::ScalarType::Float) {
        const float* scores = output_tensor.const_data_ptr<float>();
        float best_score = scores[0];
        for (ssize_t i = 1; i < num_classes; i++) {
            if (scores[i] > best_score) {
                best_score = scores[i];
                best_idx = (int)i;
            }
        }
        SEGGER_RTT_printf(0, "Top-1 class: %d\n", best_idx);
    } else {
        SEGGER_RTT_printf(0, "Output scalar type: %d (unsupported for argmax)\n",
                          (int)output_tensor.scalar_type());
    }

    // Common ImageNet cat classes: 281=tabby, 282=tiger cat, 283=Persian cat,
    // 284=Siamese cat, 285=Egyptian cat
    if (best_idx >= 280 && best_idx <= 285) {
        SEGGER_RTT_printf(0, "Detected: cat! (ImageNet class %d)\n", best_idx);
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
int main(void)
{
    int32_t ret = board_pins_config();
    if (ret != 0) {
        while(1);
    }

    gpio_b->Initialize(BOARD_LEDRGB_B_GPIO_PIN, NULL);
    gpio_b->PowerControl(BOARD_LEDRGB_B_GPIO_PIN, ARM_POWER_FULL);
    gpio_b->SetDirection(BOARD_LEDRGB_B_GPIO_PIN, GPIO_PIN_DIRECTION_OUTPUT);
    gpio_b->SetValue(BOARD_LEDRGB_B_GPIO_PIN, GPIO_PIN_OUTPUT_STATE_LOW);

    gpio_r->Initialize(BOARD_LEDRGB_R_GPIO_PIN, NULL);
    gpio_r->PowerControl(BOARD_LEDRGB_R_GPIO_PIN, ARM_POWER_FULL);
    gpio_r->SetDirection(BOARD_LEDRGB_R_GPIO_PIN, GPIO_PIN_DIRECTION_OUTPUT);
    gpio_r->SetValue(BOARD_LEDRGB_R_GPIO_PIN, GPIO_PIN_OUTPUT_STATE_LOW);

#ifdef CORE_M55_HE
    SysTick_Config(SystemCoreClock/10);
#else
    SysTick_Config(SystemCoreClock/25);
#endif

    SEGGER_RTT_printf(0, "mv2_runner booted\n");

    if (npu_init() != 0) {
        SEGGER_RTT_printf(0, "NPU init failed, halting\n");
        while (1) { __WFI(); }
    }

    run_inference();

    while (1) {
        __WFI();
    }
}

void SysTick_Handler(void)
{
#ifdef CORE_M55_HE
    gpio_b->SetValue(BOARD_LEDRGB_B_GPIO_PIN, GPIO_PIN_OUTPUT_STATE_TOGGLE);
#else
    gpio_r->SetValue(BOARD_LEDRGB_R_GPIO_PIN, GPIO_PIN_OUTPUT_STATE_TOGGLE);
#endif

    // Poll NPU_HG (Ethos-U85) for completion of any NPU operation.
    // STATUS bit 1 = irq_raised. When set, call the driver's IRQ handler
    // to signal the semaphore and unblock the waiting thread.
    if (NPU_HG_STATUS & 0x2) {
        ethosu_irq_handler(&ethos_drv);
    }
}
