"""Physical execution strategies; protocol, observations and records belong to the runner.

The free-running strategy preserves inactive edges and half-period advancement.
The scheduled strategy preserves timestamped batches and precision-tick settling.
Clockless execution only settles driven inputs. No strategy interprets transactions
or goal predicates.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time


class FreeRunningClock:
    def __init__(self, dut, clock, period_ns):
        self.handle = dut[clock.clock]
        self.clock = clock
        self.period_ns = period_ns
        self.trigger = RisingEdge if clock.edge == "rising" else FallingEdge
        self.inactive_trigger = FallingEdge if clock.edge == "rising" else RisingEdge

    def start(self):
        # No unrecorded active edge before the first slot's observation.
        self.task = cocotb.start_soon(Clock(
            self.handle, self.period_ns, units="ns").start(
                start_high=self.clock.edge == "falling"))

    def begin(self, stimulus):
        pass

    def events(self, index):
        return {(self.clock.edge, self.clock.clock)}

    async def settle_controls(self, index):
        await ReadOnly()

    async def commit(self, index):
        await self.trigger(self.handle)
        await ReadOnly()

    async def advance(self, index):
        # A time-step wakeup precedes evaluation of its clock transition. Wait
        # for that transition to settle before the runner closes its interval.
        await self.inactive_trigger(self.handle)
        await ReadOnly()
        await Timer(1, unit="ps")  # Leave read-only before the next input drive.

    def stop(self):
        self.task.cancel()

    async def between_traces(self):
        pass


class SettledInputs:
    """Test steps have no DUT edge: drive, settle, observe, then advance time."""

    def start(self):
        pass

    def begin(self, stimulus):
        pass

    def events(self, index):
        return set()

    async def settle_controls(self, index):
        # A precision tick wakes even an otherwise eventless combinational DUT.
        await Timer(1, unit="ps")
        await ReadOnly()

    async def commit(self, index):
        pass  # There is no clock transition to commit.

    async def advance(self, index):
        await Timer(1, unit="ps")

    def stop(self):
        pass

    async def between_traces(self):
        await Timer(1, unit="ps")


class ScheduledClocks:
    def __init__(self, dut):
        self.dut = dut

    def start(self):
        pass

    def begin(self, stimulus, *, timings=None, slots=None):
        self.slots = stimulus.clock_schedule.slots if slots is None else slots
        timings = stimulus.clock_schedule.timings if timings is None else timings
        self.timings = {timing.name: timing for timing in timings}
        for timing in self.timings.values():
            self.dut[timing.clock].value = int(timing.edge == "falling")
        self.base_ps = int(get_sim_time(unit="ps")) + 1

    def events(self, index):
        return {(self.timings[name].edge, self.timings[name].clock)
                for name in self.slots[index].events}

    async def settle_controls(self, index):
        target = self.base_ps + self.slots[index].time * 1000
        now = int(get_sim_time(unit="ps"))
        if target < now:
            raise RuntimeError("the simulator passed the next recorded global event time")
        # Explicit timers also wake a fully combinational, otherwise eventless design.
        await Timer(target - now if target > now else 1, unit="ps")
        await ReadOnly()

    async def commit(self, index):
        await Timer(1, unit="ps")
        for name in self.slots[index].events:
            timing = self.timings[name]
            self.dut[timing.clock].value = int(timing.edge == "rising")
        await Timer(1, unit="ps")
        await ReadOnly()

    async def advance(self, index):
        await Timer(1, unit="ps")
        for name in self.slots[index].events:
            timing = self.timings[name]
            self.dut[timing.clock].value = int(timing.edge == "falling")
        await ReadOnly()
        await Timer(1, unit="ps")

    def stop(self):
        pass

    async def between_traces(self):
        await Timer(1, unit="ns")
