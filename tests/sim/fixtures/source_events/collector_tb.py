"""Fixed HDL fixture: exercise the real in-simulator ctypes transport."""

import json
from pathlib import Path

import cocotb
from cocotb.triggers import ReadOnly, Timer

import behave


@cocotb.test()
async def native_intervals(dut):
    from behave.sim.rtl.native_events import NativeEventCollector, NativeEventPlan

    assert Path(behave.__file__).resolve() == Path(Path("expected-package.txt").read_text())
    plan = NativeEventPlan.from_dict(json.loads(Path("collector-plan.json").read_text()))
    collector = NativeEventCollector(plan)
    rows = {}
    phases = [
        ("init", {"clk": 0, "rst": 0, "sel": 3}),
        ("reset", {"rst": 1}), ("held-reset", {}), ("release", {"rst": 0}),
        ("clock-1", {"clk": 1}), ("inactive-1", {"clk": 0}),
        ("clock-2", {"sel": 1, "clk": 1}), ("inactive-2", {"clk": 0}),
        ("clock-3", {"sel": 3, "clk": 1}), ("inactive-3", {"clk": 0}),
        ("coincident-reset", {"sel": 0, "rst": 1, "clk": 1}),
    ]
    for sequence, (phase, updates) in enumerate(phases):
        collector.begin()
        for name, value in updates.items():
            dut[name].value = value
        await Timer(1, unit="ns")
        await ReadOnly()
        interval = collector.end()
        assert interval.sequence == sequence
        rows[phase] = [int(dut.q.value), int(dut.seen.value), sorted(interval.events)]
        await Timer(1, unit="ns")
    collector.finish()
    # Do not publish an apparently complete trace before the final fence.
    Path("native-trace.json").write_text(json.dumps(rows))
