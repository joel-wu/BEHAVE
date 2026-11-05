"""The native simulator, not just its scheduler, must honor a one-CPU replay."""

import json

import pytest

from behave.sim.harness import generate_makefile


def test_shared_makefile_selects_the_owned_single_thread_startup(tmp_path):
    generate_makefile(tmp_path, "M", coverage=False)
    source = (tmp_path / "Makefile").read_text()
    assert "--threads 1" in source
    assert "override VERILATOR_CPP := /runtime/cocotb-main.cpp" in source
    assert 'cocotb-config --share)/lib/verilator' in source


@pytest.mark.isolation
@pytest.mark.eda("verilator")
def test_runtime_context_and_native_threads_match_the_replay_grant(tmp_path):
    from behave.sim.rtl.tool_isolation import run_verilator_workspace
    from behave.sim.rtl.tool_protocol import command_result
    from tests.sim.test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    observations = {}
    for configured in (False, True):
        work = tmp_path / ("owned" if configured else "upstream")
        work.mkdir()
        (work / "rtl.sv").write_text("""
module M(input [7:0] a, output [7:0] q, output int context_threads);
  import "DPI-C" function int replay_context_threads();
  assign q = a + 8'd1;
  assign context_threads = replay_context_threads();
endmodule
""")
        (work / "context.cpp").write_text("""
#include "verilated.h"
extern "C" int replay_context_threads() {
    return Verilated::threadContextp()->threads();
}
""")
        (work / "thread_tb.py").write_text("""
import json
import os
from pathlib import Path
import cocotb
from cocotb.triggers import Timer

@cocotb.test()
async def observe(dut):
    samples = []
    for value in (0, 1, 127, 255):
        dut.a.value = value
        await Timer(1, unit="ns")
        samples.append(int(dut.q.value))
    Path("threads.json").write_text(json.dumps({
        "context": int(dut.context_threads.value),
        "affinity": len(os.sched_getaffinity(0)),
        "native_threads": len(list(Path("/proc/self/task").iterdir())),
        "samples": samples,
    }))
""")
        generate_makefile(work, "M", tb_module="thread_tb", coverage=False)
        makefile = work / "Makefile"
        source = makefile.read_text()
        if not configured:
            source = "\n".join(line for line in source.splitlines()
                               if not line.startswith("override VERILATOR_CPP :=")) + "\n"
        makefile.write_text(source + "\nEXTRA_ARGS += /work/context.cpp\n")
        built, ran = run_verilator_workspace(
            work, top_module="M", test_module="thread_tb", role="replay-threads",
            outputs=("threads.json",), compile_timeout_s=120, simulate_timeout_s=30)
        assert all(row["returncode"] == 0 for row in built.commands), built.as_dict()
        assert command_result(ran, "replay-threads:simulate")["returncode"] == 0, ran.as_dict()
        observations[configured] = json.loads((work / "threads.json").read_text())

    old, new = observations[False], observations[True]
    assert old["samples"] == new["samples"] == [1, 2, 128, 0]
    assert old["context"] == old["affinity"]
    assert new["context"] == 1
    assert old["native_threads"] - new["native_threads"] == old["context"] - 1
    print(json.dumps({"upstream": old, "owned": new}, sort_keys=True))
