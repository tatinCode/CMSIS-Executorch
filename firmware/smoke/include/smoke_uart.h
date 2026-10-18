#pragma once

#include <stdint.h>

namespace handgpt {

enum class UartSendResult { Complete, StartFailed, TimedOut, AbortFailed };

// Single foreground writer, ISR completion flag. The platform rejects ISR and
// interrupt-masked callers. Transport owns send()/abort_send(); now() is a
// wrapping millisecond clock, and idle() must wake periodically (SysTick).
template <typename Transport, typename Clock, typename Idle>
UartSendResult uart_send_and_wait(
    Transport& transport, volatile bool& send_complete,
    const char* data, uint32_t length, Clock now, Idle idle,
    uint32_t timeout_ms = 1000) {
    // Clear BEFORE send(): an implementation may complete synchronously, and
    // the previous transfer's event must not satisfy this transfer's wait.
    send_complete = false;
    const uint32_t start = now();
    if (!transport.send(data, length)) {
        return UartSendResult::StartFailed;
    }
    // GetTxCount() is deliberately not used: on Alif it counts bytes queued
    // into the FIFO, while the driver is still busy until SEND_COMPLETE.
    while (!send_complete) {
        if (static_cast<uint32_t>(now() - start) >= timeout_ms) {
            // Release the caller's buffer before returning from a failed write.
            return transport.abort_send() ? UartSendResult::TimedOut : UartSendResult::AbortFailed;
        }
        // Completion between the flag check and WFI is safe: SysTick wakes us.
        idle();
    }
    return UartSendResult::Complete;
}

} // namespace handgpt
