#include "smoke_platform.h"
#include "smoke_uart.h"

#include "RTE_Components.h"
#include CMSIS_device_header
#include "Driver_IO.h"
#include "Driver_USART.h"
#include "board_config.h"
#include "board_defs.h"
#include "ethosu_driver.h"
#include "se_services_port.h"

#include <executorch/runtime/platform/platform.h>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <limits.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>

static_assert(sizeof(void*) == 4, "This platform port targets 32-bit Cortex-M55 only");
static_assert(sizeof(ethosu_job_state) == 1 && sizeof(ethosu_job_result) == 1,
              "Match the pinned Arm runtime/driver's small-enum ABI");

extern "C" {
volatile SmokeStatus g_smoke_status = {
    0x48534750u, 1, SmokeStage::Boot, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
};
extern uint8_t __end__;
extern uint8_t __HeapLimit;
}

namespace {
constexpr IRQn_Type kNpuIrq = static_cast<IRQn_Type>(366);
constexpr uint32_t kWaitLimitMs = 5000;
volatile uint64_t ticks;
ethosu_driver npu;
bool npu_ready;
bool leds_ready;
bool uart_ready;
volatile bool uart_send_complete;
bool passed;

#if defined(BOARD_UART2_INSTANCE)
#define SMOKE_UART_INSTANCE BOARD_UART2_INSTANCE
#elif defined(BOARD_UARTB_UART_INSTANCE)
#define SMOKE_UART_INSTANCE BOARD_UARTB_UART_INSTANCE
#else
#error "No HP application UART is defined by the board configuration"
#endif
extern "C" ARM_DRIVER_USART ARM_Driver_USART_(SMOKE_UART_INSTANCE);
ARM_DRIVER_USART* uart = &ARM_Driver_USART_(SMOKE_UART_INSTANCE);
extern "C" ARM_DRIVER_GPIO ARM_Driver_GPIO_(BOARD_LEDRGB0_G_GPIO_PORT);
ARM_DRIVER_GPIO* green = &ARM_Driver_GPIO_(BOARD_LEDRGB0_G_GPIO_PORT);
#if BOARD_LEDRGB_COUNT > 1
#define SMOKE_RED_PORT BOARD_LEDRGB1_R_GPIO_PORT
#define SMOKE_RED_PIN BOARD_LEDRGB1_R_GPIO_PIN
#else
#define SMOKE_RED_PORT BOARD_LEDRGB0_R_GPIO_PORT
#define SMOKE_RED_PIN BOARD_LEDRGB0_R_GPIO_PIN
#endif
extern "C" ARM_DRIVER_GPIO ARM_Driver_GPIO_(SMOKE_RED_PORT);
ARM_DRIVER_GPIO* red = &ARM_Driver_GPIO_(SMOKE_RED_PORT);

struct Semaphore { volatile uint32_t count; bool used; };
Semaphore semaphores[4];

bool init_led(ARM_DRIVER_GPIO* driver, uint32_t pin) {
    return driver->Initialize(pin, nullptr) == ARM_DRIVER_OK &&
           driver->PowerControl(pin, ARM_POWER_FULL) == ARM_DRIVER_OK &&
           driver->SetDirection(pin, GPIO_PIN_DIRECTION_OUTPUT) == ARM_DRIVER_OK &&
           driver->SetValue(pin, GPIO_PIN_OUTPUT_STATE_LOW) == ARM_DRIVER_OK;
}

void uart_callback(uint32_t events) {
    if ((events & ARM_USART_EVENT_SEND_COMPLETE) != 0) {
        // Alif clears tx_busy before invoking this callback. Do not log in ISR.
        uart_send_complete = true;
    }
}

struct UartTransport {
    int32_t start_status = ARM_DRIVER_OK;

    bool send(const char* data, uint32_t length) {
        start_status = uart->Send(data, length);
        return start_status == ARM_DRIVER_OK;
    }

    bool abort_send() {
        return uart->Control(ARM_USART_ABORT_SEND, 0) == ARM_DRIVER_OK;
    }
};

void maintain_cache(uint32_t* address, size_t bytes, bool invalidate) {
    if (!address) {
        if (invalidate) { SCB_InvalidateDCache(); }
        else { SCB_CleanDCache(); }
        __DSB();
        return;
    }
    if (bytes == 0) { return; }
    const uintptr_t start = reinterpret_cast<uintptr_t>(address) & ~uintptr_t(31);
    const uintptr_t end = (reinterpret_cast<uintptr_t>(address) + bytes + 31u) & ~uintptr_t(31);
    if (end < start || end - start > INT32_MAX) { et_pal_abort(); }
    if (invalidate) {
        SCB_InvalidateDCache_by_Addr(reinterpret_cast<void*>(start), static_cast<int32_t>(end - start));
    } else {
        SCB_CleanDCache_by_Addr(reinterpret_cast<void*>(start), static_cast<int32_t>(end - start));
    }
    __DSB();
}
} // namespace

uint32_t smoke_millis() { return static_cast<uint32_t>(ticks); }

bool smoke_board_init() {
    if (board_pins_config() != 0 || board_gpios_config() != 0) { return false; }
    se_services_port_init();
    if (board_clocks_config(CLKEN_HFOSC_MASK | CLKEN_CLK_100M_MASK) != 0) { return false; }
    SystemCoreClockUpdate();
    if (SystemCoreClock < 1000 || SysTick_Config(SystemCoreClock / 1000) != 0) { return false; }
    leds_ready = init_led(green, BOARD_LEDRGB0_G_GPIO_PIN) && init_led(red, SMOKE_RED_PIN);
    if (!leds_ready) { return false; }
    if (uart->Initialize(uart_callback) != ARM_DRIVER_OK ||
        uart->PowerControl(ARM_POWER_FULL) != ARM_DRIVER_OK ||
        uart->Control(ARM_USART_MODE_ASYNCHRONOUS | ARM_USART_DATA_BITS_8 |
                      ARM_USART_PARITY_NONE | ARM_USART_STOP_BITS_1 |
                      ARM_USART_FLOW_CONTROL_NONE, 115200) != ARM_DRIVER_OK ||
        uart->Control(ARM_USART_CONTROL_TX, 1) != ARM_DRIVER_OK) { return false; }
    uart_ready = true;
    setvbuf(stdout, nullptr, _IONBF, 0);
    setvbuf(stderr, nullptr, _IONBF, 0);
    g_smoke_status.stage = SmokeStage::BoardReady;
    return true;
}

bool smoke_npu_init() {
    // Same base, security and IRQ choice as Ensemble 2.2.0's E8-HP example.
    NVIC_DisableIRQ(kNpuIrq);
    std::printf("NPU init: starting base=0x%08lx IRQ=366\n", static_cast<unsigned long>(NPU_HG_BASE));
    const int initialized = ethosu_init(&npu, reinterpret_cast<void*>(NPU_HG_BASE), nullptr, 0, 1, 1);
    if (initialized != 0) {
        std::printf("NPU init failed: ethosu_init returned %d\n", initialized);
        return false;
    }
    ethosu_hw_info info{};
    ethosu_get_hw_info(&npu, &info);
    if (info.cfg.macs_per_cc >= 31) {
        std::printf("NPU init failed: invalid MAC encoding %lu\n", static_cast<unsigned long>(info.cfg.macs_per_cc));
        return false;
    }
    g_smoke_status.hardware_macs = 1u << info.cfg.macs_per_cc;
    std::printf("NPU arch=%u.%u.%u MACs=%lu\n", static_cast<unsigned>(info.version.arch_major_rev),
                static_cast<unsigned>(info.version.arch_minor_rev), static_cast<unsigned>(info.version.arch_patch_rev),
                static_cast<unsigned long>(g_smoke_status.hardware_macs));
    if (g_smoke_status.hardware_macs != 256) {
        std::printf("NPU init failed: expected 256 MACs, detected %lu\n",
                    static_cast<unsigned long>(g_smoke_status.hardware_macs));
        return false;
    }
    NVIC_ClearPendingIRQ(kNpuIrq);
    NVIC_SetPriority(kNpuIrq, 2);
    npu_ready = true;
    NVIC_EnableIRQ(kNpuIrq);
    g_smoke_status.stage = SmokeStage::NpuReady;
    return true;
}

void smoke_set_result(bool value) {
    passed = value;
    g_smoke_status.stage = value ? SmokeStage::Pass : SmokeStage::Fail;
    if (leds_ready) {
        red->SetValue(SMOKE_RED_PIN, value ? GPIO_PIN_OUTPUT_STATE_LOW : GPIO_PIN_OUTPUT_STATE_HIGH);
        green->SetValue(BOARD_LEDRGB0_G_GPIO_PIN, value ? GPIO_PIN_OUTPUT_STATE_HIGH : GPIO_PIN_OUTPUT_STATE_LOW);
    }
}

[[noreturn]] void smoke_idle() {
    uint32_t previous = smoke_millis();
    while (true) {
        if (passed && leds_ready && smoke_millis() - previous >= 500) {
            green->SetValue(BOARD_LEDRGB0_G_GPIO_PIN, GPIO_PIN_OUTPUT_STATE_TOGGLE);
            previous = smoke_millis();
        }
        __WFI();
    }
}

extern "C" void SysTick_Handler() { ++ticks; }
extern "C" void IRQ366_Handler() {
    if (npu_ready) {
        ++g_smoke_status.npu_irqs;
        ethosu_irq_handler(&npu);
    }
}

extern "C" void* ethosu_semaphore_create() {
    for (auto& semaphore : semaphores) {
        if (!semaphore.used) {
            semaphore.count = 0;
            semaphore.used = true;
            return &semaphore;
        }
    }
    return nullptr;
}
extern "C" void ethosu_semaphore_destroy(void* pointer) {
    if (pointer) { static_cast<Semaphore*>(pointer)->used = false; }
}
extern "C" int ethosu_semaphore_take(void* pointer, uint64_t timeout) {
    if (!pointer || __get_IPSR() != 0 || __get_PRIMASK() != 0) { return -1; }
    auto* semaphore = static_cast<Semaphore*>(pointer);
    const uint32_t limit = timeout < kWaitLimitMs ? static_cast<uint32_t>(timeout) : kWaitLimitMs;
    const uint32_t start = smoke_millis();
    while (true) {
        __disable_irq();
        if (semaphore->count != 0) {
            --semaphore->count;
            __enable_irq();
            return 0;
        }
        __enable_irq();
        if (smoke_millis() - start >= limit) {
            ++g_smoke_status.semaphore_timeouts;
            return -1;
        }
        // SysTick bounds sleep even if the NPU completion interrupt never arrives.
        __WFI();
    }
}
extern "C" int ethosu_semaphore_give(void* pointer) {
    if (!pointer) { return -1; }
    const uint32_t mask = __get_PRIMASK();
    __disable_irq();
    ++static_cast<Semaphore*>(pointer)->count;
    __set_PRIMASK(mask);
    __SEV();
    return 0;
}
extern "C" void ethosu_inference_begin(ethosu_driver*, void*) {
    ++g_smoke_status.npu_jobs_started;
}
extern "C" void ethosu_inference_end(ethosu_driver* driver, void*) {
    // Refresh only NPU-owned scratch ranges AFTER completion. Do not invalidate
    // unrelated CPU tensors that happen to share the larger planned arena.
    for (int i = 0; i < driver->job.num_base_addr && i < 8; ++i) {
        if ((driver->basep_invalidate_mask & (1u << i)) != 0) {
            maintain_cache(reinterpret_cast<uint32_t*>(static_cast<uintptr_t>(driver->job.base_addr[i])),
                           driver->job.base_addr_size[i], true);
        }
    }
    ++g_smoke_status.npu_jobs_finished;
}
extern "C" void ethosu_flush_dcache(uint32_t* pointer, size_t bytes) { maintain_cache(pointer, bytes, false); }
extern "C" void ethosu_invalidate_dcache(uint32_t* pointer, size_t bytes) { maintain_cache(pointer, bytes, true); }

extern "C" int _write(int file, const char* data, int length) {
    if (file != 1 && file != 2) { errno = EBADF; return -1; }
    if (length < 0 || (!data && length != 0)) { errno = EINVAL; return -1; }
    if (length == 0) { return 0; }
    if (!uart_ready || __get_IPSR() != 0 || __get_PRIMASK() != 0 ||
        __get_BASEPRI() != 0 || __get_FAULTMASK() != 0) {
        errno = EIO;
        return -1;
    }
    UartTransport transport;
    const auto result = handgpt::uart_send_and_wait(
        transport, uart_send_complete, data, static_cast<uint32_t>(length),
        smoke_millis, [] { __WFI(); });
    if (result == handgpt::UartSendResult::Complete) { return length; }
    if (result == handgpt::UartSendResult::StartFailed) {
        errno = transport.start_status == ARM_DRIVER_ERROR_BUSY ? EBUSY : EIO;
        return -1;
    }
    ++g_smoke_status.uart_timeouts;
    if (result == handgpt::UartSendResult::AbortFailed) {
        // Do not start further writes on a driver whose transfer could not be
        // cancelled. In this target DMA is disabled; power-off disables UART IRQ.
        uart_ready = false;
        uart->PowerControl(ARM_POWER_OFF);
        errno = EIO;
    } else {
        errno = ETIMEDOUT;
    }
    return -1;
}

// Explicit, bounded bare-metal newlib syscalls; no semihosting or filesystem.
extern "C" void* _sbrk(ptrdiff_t increment) {
    static uintptr_t current = reinterpret_cast<uintptr_t>(&__end__);
    const uintptr_t begin = reinterpret_cast<uintptr_t>(&__end__);
    const uintptr_t limit = reinterpret_cast<uintptr_t>(&__HeapLimit);
    const uintptr_t previous = current;
    if (increment >= 0) {
        if (current > limit || static_cast<uintptr_t>(increment) > limit - current) {
            errno = ENOMEM;
            return reinterpret_cast<void*>(-1);
        }
        current += static_cast<uintptr_t>(increment);
    } else {
        const uintptr_t amount = static_cast<uintptr_t>(-(increment + 1)) + 1u;
        if (current < begin || amount > current - begin) {
            errno = ENOMEM;
            return reinterpret_cast<void*>(-1);
        }
        current -= amount;
    }
    return reinterpret_cast<void*>(previous);
}
extern "C" int _close(int) { errno = EBADF; return -1; }
extern "C" int _read(int, char*, int) { errno = ENOSYS; return -1; }
extern "C" off_t _lseek(int, off_t, int) { errno = ESPIPE; return -1; }
extern "C" int _isatty(int file) { return file == 1 || file == 2; }
extern "C" int _fstat(int file, struct stat* status) {
    if (!status || (file != 1 && file != 2)) { errno = EBADF; return -1; }
    *status = {};
    status->st_mode = S_IFCHR;
    return 0;
}
extern "C" int _getpid() { return 1; }
extern "C" int _kill(int, int) { errno = ENOSYS; return -1; }
extern "C" int _gettimeofday(struct timeval*, void*) { errno = ENOSYS; return -1; }
extern "C" [[noreturn]] void _exit(int) {
    g_smoke_status.error = 101;
    smoke_set_result(false);
    smoke_idle();
}

extern "C" void et_pal_init() {}
extern "C" ET_NORETURN void et_pal_abort() {
    g_smoke_status.error = 100;
    smoke_set_result(false);
    smoke_idle();
}
extern "C" et_timestamp_t et_pal_current_ticks() {
    const uint32_t mask = __get_PRIMASK();
    __disable_irq();
    const uint64_t value = ticks;
    __set_PRIMASK(mask);
    return value;
}
extern "C" et_tick_ratio_t et_pal_ticks_to_ns_multiplier() { return {1000000, 1}; }
extern "C" void et_pal_emit_log_message(et_timestamp_t, et_pal_log_level_t level,
    const char*, const char*, size_t, const char* message, size_t length) {
    std::printf("[%c] %.*s\n", static_cast<char>(level), static_cast<int>(length), message);
}
extern "C" void* et_pal_allocate(size_t size) { return std::malloc(size); }
extern "C" void et_pal_free(void* pointer) { std::free(pointer); }

[[noreturn]] static void capture_fault() {
    g_smoke_status.fault_cfsr = SCB->CFSR;
    g_smoke_status.fault_hfsr = SCB->HFSR;
    g_smoke_status.stage = SmokeStage::Fault;
    while (true) { __WFI(); }
}
extern "C" void HardFault_Handler() { capture_fault(); }
extern "C" void MemManage_Handler() { capture_fault(); }
extern "C" void BusFault_Handler() { capture_fault(); }
extern "C" void UsageFault_Handler() { capture_fault(); }
extern "C" void SecureFault_Handler() { capture_fault(); }
