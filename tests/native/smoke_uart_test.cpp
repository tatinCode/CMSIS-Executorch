#include "smoke_uart.h"

#include <cassert>
#include <cstdint>

using handgpt::UartSendResult;

struct FakeUart {
    volatile bool complete = false;
    bool busy = false;
    bool reject_send = false;
    bool synchronous = false;
    bool deliver_event = true;
    bool abort_succeeds = true;
    uint32_t ticks = 0;
    uint32_t queued = 0;
    uint32_t waits = 0;
    uint32_t sends = 0;
    uint32_t aborts = 0;
    uint32_t event_at = 3;

    bool send(const char*, uint32_t length) {
        ++sends;
        // Even a stale completion must have been cleared before Send.
        assert(!complete);
        if (busy || reject_send) { return false; }
        busy = true;
        // Model Alif's race: the whole payload is queued before SEND_COMPLETE.
        queued = length;
        waits = 0;
        if (synchronous) {
            busy = false;
            complete = true;
        }
        return true;
    }

    bool abort_send() {
        ++aborts;
        if (!abort_succeeds) { return false; }
        busy = false;
        queued = 0;
        return true;
    }

    void idle() {
        ++ticks;
        ++waits;
        if (deliver_event && waits == event_at) {
            // As in Driver_USART.c, clear tx_busy before the callback.
            busy = false;
            complete = true;
        }
        // Bound the test itself, including if firmware timeout math regresses.
        assert(waits <= 1001);
    }

    UartSendResult write(uint32_t timeout = 1000) {
        return handgpt::uart_send_and_wait(
            *this, complete, "test", 4, [this] { return ticks; },
            [this] { idle(); }, timeout);
    }
};

int main() {
    {
        FakeUart uart;
        assert(uart.write() == UartSendResult::Complete);
        assert(uart.queued == 4 && uart.waits == 3 && !uart.busy);
        // A second write immediately after the first must not encounter BUSY.
        assert(uart.write() == UartSendResult::Complete);
        assert(uart.waits == 3 && uart.sends == 2 && uart.aborts == 0);
    }
    {
        FakeUart uart;
        uart.complete = true; // Stale event from a previous transfer.
        assert(uart.write() == UartSendResult::Complete);
        assert(uart.waits == 3);
    }
    {
        FakeUart uart;
        uart.synchronous = true;
        assert(uart.write() == UartSendResult::Complete);
        assert(uart.waits == 0 && uart.aborts == 0);
    }
    {
        FakeUart uart;
        uart.reject_send = true;
        assert(uart.write() == UartSendResult::StartFailed);
        assert(uart.waits == 0 && uart.aborts == 0);
    }
    {
        FakeUart uart;
        uart.busy = true;
        assert(uart.write() == UartSendResult::StartFailed);
        assert(uart.waits == 0 && uart.aborts == 0);
    }
    {
        FakeUart uart;
        uart.deliver_event = false;
        assert(uart.write() == UartSendResult::TimedOut);
        assert(uart.waits == 1000 && uart.aborts == 1 && !uart.busy);
        // A cancelled write must not poison a subsequent valid transfer.
        uart.deliver_event = true;
        assert(uart.write() == UartSendResult::Complete);
        assert(uart.sends == 2 && uart.aborts == 1);
    }
    {
        FakeUart uart;
        uart.deliver_event = false;
        uart.abort_succeeds = false;
        assert(uart.write() == UartSendResult::AbortFailed);
        assert(uart.waits == 1000 && uart.aborts == 1);
    }
    {
        FakeUart uart;
        uart.ticks = UINT32_MAX - 2;
        uart.deliver_event = false;
        assert(uart.write(5) == UartSendResult::TimedOut);
        assert(uart.waits == 5 && uart.ticks == 2 && uart.aborts == 1);
    }
    {
        FakeUart uart;
        uart.ticks = UINT32_MAX - 1;
        assert(uart.write() == UartSendResult::Complete);
        assert(uart.ticks == 1 && uart.waits == 3);
    }
    {
        FakeUart uart;
        uart.deliver_event = false;
        assert(uart.write(0) == UartSendResult::TimedOut);
        assert(uart.waits == 0 && uart.aborts == 1);
    }
}
