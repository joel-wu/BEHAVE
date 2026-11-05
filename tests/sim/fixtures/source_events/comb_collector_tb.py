"""Fixed combinational fixture; bracket all simulator yields in the collector."""

import json
from pathlib import Path

import cocotb
from cocotb.triggers import ReadOnly, Timer

from behave.sim.rtl.native_events import NativeEventCollector, NativeEventPlan


@cocotb.test()
async def native_intervals(dut):
    plan = NativeEventPlan.from_dict(json.loads(Path("collector-plan.json").read_text()))
    collector = NativeEventCollector(plan)
    collector.begin()
    rows, idle = [], []
    for index in range(32):
        dut.a.value = index // 2
        # This input is not read by the combinational process. A change here
        # must not manufacture a process activation when a is unchanged.
        dut.noise.value = index % 2
        await Timer(1, unit="ns")
        await ReadOnly()
        interval = collector.end()
        rows.append([int(dut.q.value), sorted(interval.events), interval.callbacks])
        if index != 31:
            collector.begin()
            await Timer(1, unit="ns")
            await ReadOnly()
            interval = collector.end()
            idle.append([int(dut.q.value), sorted(interval.events), interval.callbacks])
            collector.begin()
            await Timer(1, unit="ps")  # Leave ReadOnly before driving the next input.
    collector.finish()
    Path("native-trace.json").write_text(json.dumps({"samples": rows, "idle": idle}))
