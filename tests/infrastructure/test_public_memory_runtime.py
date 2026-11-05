"""Real Verilator timing in the public shell and fresh submitted-TB runtime."""

from importlib.resources import files

import pytest

from behave.agent.local_workspace import LocalWorkspace
from behave.core.artifacts import BlobRef, FileData, FileRef, SourceArtifact
from behave.core.storage import DirectoryStore
from behave.sim import api
from behave.sim.core.memory import (
    MemoryInterface,
    MemoryPort,
    MemoryRun,
    MemoryState,
    advance_memory_state,
    drive_memory_state,
    memory_junk_value,
)
from behave.sim.rtl.tool_isolation import default_limits
from behave.task_execution.shell import development_shell

RTL = '''module top(
input clk, other_clk, rst,
input a_valid, a_we, b_valid, b_we,
input [3:0] a_addr, b_addr,
input [7:0] a_data, b_data,
output ram__a_req_valid, ram__a_req_we, ram__b_req_valid, ram__b_req_we,
output [3:0] ram__a_req_addr, ram__b_req_addr,
output [7:0] ram__a_req_wdata, ram__b_req_wdata,
input ram__a_req_ready, ram__b_req_ready, ram__a_resp_valid, ram__b_resp_valid,
input [7:0] ram__a_resp_rdata, ram__b_resp_rdata,
output auto_req_valid,
output reg [3:0] auto_req_addr = 0,
input auto_req_ready, auto_resp_valid,
input [7:0] auto_resp_rdata,
output reg [7:0] seen = 0
);
assign ram__a_req_valid = a_valid;
assign ram__a_req_we = a_we;
assign ram__a_req_addr = a_addr;
assign ram__a_req_wdata = a_data;
assign ram__b_req_valid = b_valid;
assign ram__b_req_we = b_we;
assign ram__b_req_addr = b_addr;
assign ram__b_req_wdata = b_data;
assign auto_req_valid = !rst;
always @(posedge clk) begin
    if (rst) begin
        auto_req_addr <= 0;
        seen <= 0;
    end else begin
        if (auto_req_ready) auto_req_addr <= auto_req_addr + 1'b1;
        if (auto_resp_valid) seen <= auto_resp_rdata;
    end
end
endmodule
'''


TB = '''import importlib.util
from pathlib import Path
import cocotb
from cocotb.triggers import RisingEdge, FallingEdge, ReadOnly, Timer
import memory_driver
from memory_driver import MemoryDriver
from expected import ROWS, EDGE

@cocotb.test()
async def check(dut):
    assert importlib.util.find_spec("behave") is None
    assert memory_driver.__file__ == "/runtime/site/memory_driver/__init__.py"
    assert "def advance(" in Path(memory_driver.__file__).with_name("_protocol.py").read_text()
    driver = MemoryDriver(dut.clk, period=10, unit="ns", edge=EDGE, reset=dut.rst)
    ready = lambda c, p: int((c + (p == "b")) % 4 != 1)
    initial = {1: 42, 5: 9}
    ram = driver.add_memory(dut, "ram", ports={"a": "rw", "b": "rw"},
                            initial=initial, read_latency=3, ready=ready)
    driver.add_memory(dut, "auto", ports={None: "r"},
                      initial={i: 80 + i for i in range(16)}, read_latency=1)
    initial[1] = 200
    trigger = RisingEdge if EDGE == "rising" else FallingEdge
    async with driver:
        for cycle, row in enumerate(ROWS):
            for signal, value in row["inputs"].items():
                dut[signal].value = value
            await trigger(dut.clk)
            await ReadOnly()
            # These are the memory inputs RTL consumed at this edge, not the
            # request pins already changed by the registered address counter.
            for signal, value in row["driven"].items():
                assert int(dut[signal].value) == value, (cycle, signal, value, dut[signal].value)
            assert int(dut.seen.value) == row["seen"], (cycle, dut.seen.value, row["seen"])
            assert driver.cycle == cycle + 1
            assert [ram.read(i) for i in range(16)] == row["cells"], cycle
            await Timer(1, unit="ns")
    stopped = driver.cycle
    await Timer(30, unit="ns")
    assert driver.cycle == stopped
    assert driver._task.cancelled()
    if EDGE == "rising":
        from guide_example import run_with_memory

        async def one_write(dut, memory):
            dut.rst.value = 1
            dut.a_valid.value = 1
            dut.a_we.value = 1
            dut.a_addr.value = 1
            dut.a_data.value = 77
            dut.b_valid.value = 0
            assert memory.read(1) == 42
            await RisingEdge(dut.clk)
            await ReadOnly()
            assert memory.read(1) == 42
            await Timer(1, unit="ns")
            dut.rst.value = 0
            await RisingEdge(dut.clk)
            await ReadOnly()
            assert memory.read(1) == 77

        await run_with_memory(dut, one_write)
'''


def expected_rows():
    ram = MemoryInterface("ram", 4, 8, "clk", [MemoryPort("rw", p) for p in ("a", "b")])
    auto = MemoryInterface("auto", 4, 8, "clk", [MemoryPort("r")])
    run = MemoryRun("ram", initial={1: 42, 5: 9}, read_latency=3,
                    ready={p: [int((c + (p == "b")) % 4 != 1) for c in range(40)] for p in ("a", "b")})
    auto_run = MemoryRun("auto", initial={i: 80 + i for i in range(16)}, ready={None: [1] * 40})
    state, auto_state = MemoryState.initial(ram, run), MemoryState.initial(auto, auto_run)
    rows, address, seen = [], 0, 0
    for c in range(40):
        inputs = {"rst": int(c in (0, 12, 13)), "a_valid": int(c < 32), "b_valid": int(c < 32),
                  "a_we": int(c % 3 == 0), "b_we": int(c % 3 != 0),
                  "a_addr": c % 16, "b_addr": c % 16, "a_data": 100 + c, "b_data": 200 + c}
        requests = {}
        for port in ("a", "b"):
            for suffix, key in (("valid", "valid"), ("we", "we"), ("addr", "addr"), ("wdata", "data")):
                requests[f"ram__{port}_req_{suffix}"] = inputs[f"{port}_{key}"]
        reset = bool(inputs["rst"])
        driven = drive_memory_state(ram, run, state, c, reset=reset)
        driven.update(drive_memory_state(auto, auto_run, auto_state, c, reset=reset))
        state = advance_memory_state(ram, run, state, c, requests, reset=reset)
        auto_state = advance_memory_state(auto, auto_run, auto_state, c,
                                         {"auto_req_valid": 1 - inputs["rst"], "auto_req_addr": address},
                                         reset=reset)
        if inputs["rst"]:
            address, seen = 0, 0
        else:
            address = (address + 1) % 16
            if driven["auto_resp_valid"]:
                seen = driven["auto_resp_rdata"]
        cells = dict(state.cells)
        rows.append({"inputs": inputs, "driven": driven, "seen": seen,
                     "cells": [cells.get(i, memory_junk_value("ram", i, 8)) for i in range(16)]})
    return rows


def selected_files(edge, tb=TB):
    source = RTL if edge == "rising" else RTL.replace("posedge", "negedge")
    guide = files("behave.agent").joinpath("prompts/shared/guides/memory.md").read_text()
    example = guide.split("```python\n", 1)[1].split("\n```", 1)[0]
    documents = (("rtl.sv", source), ("testbench.py", tb),
                 ("expected.py", f"EDGE = {edge!r}\nROWS = {expected_rows()!r}\n"),
                 ("guide_example.py", example))
    return tuple(FileData(FileRef(name, BlobRef.of(text.encode())), text.encode()) for name, text in documents)


def final_test(selected, directory):
    refs = {file.reference.path: file.reference for file in selected}
    return api.run_cocotb_test(
        selected, rtl=SourceArtifact((refs["rtl.sv"],), "rtl.sv", "top"),
        testbench=SourceArtifact((refs["testbench.py"], refs["expected.py"], refs["guide_example.py"]),
                                "testbench.py", "check"),
        workdir=directory)


@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize("edge", ["rising", "falling"])
def test_memory_driver_shell_and_final_tb(tmp_path, edge):
    workspace_dir = tmp_path / "development"
    workspace_dir.mkdir()
    workspace = LocalWorkspace(DirectoryStore(tmp_path / "objects"), workspace_dir)
    selected = selected_files(edge)
    workspace.seed(selected)
    guide = files("behave.agent").joinpath("prompts/shared/guides/development.md").read_text()
    makefile = guide.split("```make\n", 1)[1].split("\n```", 1)[0]
    workspace.write("Makefile", makefile.replace(" $(PWD)/helper.sv", "").encode())
    result = development_shell(default_limits()).execute(workspace, {"command": """set -e
test -r /docs/memory.md
test ! -w /docs/memory.md
grep -q 'from memory_driver import MemoryDriver' /docs/memory.md
test -r /runtime/site/memory_driver/_protocol.py
test ! -w /runtime/site/memory_driver/_protocol.py
make
"""})
    assert result.content["exit_code"] == 0, result.content
    final = final_test(selected, tmp_path / "final")
    assert (final.status, final.comparison) == ("completed", "passed"), final
    assert final.line_coverage is not None


@pytest.mark.eda
@pytest.mark.isolation
def test_background_memory_failure_is_not_silently_lost(tmp_path):
    # Both writes are accepted at the same address in the first active slot.
    broken = TB.replace('ready=ready)', 'ready=None)')
    broken = broken.replace('await trigger(dut.clk)',
                            'dut.a_we.value = 1\n            dut.b_we.value = 1\n            await trigger(dut.clk)')
    final = final_test(selected_files("rising", broken), tmp_path / "collision")
    assert final.comparison == "failed", final
    # The runner returns immutable evidence before removing its scratch files.
    logs = b"\n".join(data for _name, data in final.evidence)
    assert b"memory-simultaneous-write-conflict" in logs


@pytest.mark.eda
@pytest.mark.isolation
def test_independent_memory_clock_domains(tmp_path):
    tb = '''import cocotb
from cocotb.triggers import ReadOnly, Timer
from memory_driver import MemoryDriver

@cocotb.test()
async def check(dut):
    dut.rst.value = 0
    dut.a_valid.value = 1
    dut.a_we.value = 1
    dut.a_addr.value = 1
    dut.a_data.value = 66
    dut.b_valid.value = 0
    dut.b_we.value = 0
    dut.b_addr.value = 0
    dut.b_data.value = 0
    first = MemoryDriver(dut.clk, period=10, unit="ns")
    ram = first.add_memory(dut, "ram", ports={"a": "rw", "b": "rw"})
    second = MemoryDriver(dut.other_clk, period=18, unit="ns")
    auto = second.add_memory(dut, "auto", ports={None: "r"},
                             initial={i: 80+i for i in range(16)})
    async with first, second:
        await Timer(90, unit="ns")
        await ReadOnly()
        assert (first.cycle, second.cycle) == (9, 5)
        assert ram.read(1) == 66
        assert auto.read(1) == 81
        assert int(dut.seen.value) == 83
    assert first._task.cancelled() and second._task.cancelled()
'''
    selected = selected_files("rising", tb)
    rtl = RTL.replace("always @(posedge clk)", "always @(posedge other_clk)").encode()
    selected = (FileData(FileRef("rtl.sv", BlobRef.of(rtl)), rtl), *selected[1:])
    final = final_test(selected, tmp_path / "multiclock")
    assert (final.status, final.comparison) == ("completed", "passed"), final
