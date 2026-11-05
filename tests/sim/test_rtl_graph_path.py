"""Shared timing helpers retain suspension and output ordering."""

import pytest

from behave.sim.harness import generate_makefile


@pytest.mark.isolation
@pytest.mark.eda("verilator")
def test_shared_helper_timing_and_native_graph_self_tests(tmp_path):
    from behave.sim.rtl.tool_isolation import run_verilator_workspace
    from behave.sim.rtl.tool_protocol import command_result
    from tests.sim.test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    count = 256
    source = f"""`timescale 1ns/1ps
module M(input clk, a, output logic [{count-1}:0] q);
function automatic bit invert(input bit value);
    invert = ~value;
endfunction
task automatic delayed(input bit value, output bit result);
    #2;
    result = invert(value);
endtask
"""
    for i in range(count):
        source += f"always @(posedge clk) delayed(a ^ 1'b{i % 2}, q[{i}]);\n"
    (tmp_path / "rtl.sv").write_text(source + "endmodule\n")
    (tmp_path / "timing_tb.py").write_text('''
import cocotb
from cocotb.triggers import Timer

@cocotb.test()
async def timing(dut):
    dut.clk.value = 0
    dut.a.value = 0
    await Timer(1, unit="ns")
    previous = None
    for a in (0, 1, 0, 1):
        dut.a.value = a
        dut.clk.value = 1
        await Timer(1, unit="ns")
        if previous is not None:
            assert int(dut.q.value) == previous
        await Timer(2, unit="ns")
        previous = sum((1 ^ a ^ (i % 2)) << i for i in range(COUNT))
        assert int(dut.q.value) == previous
        dut.clk.value = 0
        await Timer(1, unit="ns")
'''.replace("COUNT", str(count)))
    generate_makefile(tmp_path, "M", tb_module="timing_tb", coverage=False)
    makefile = tmp_path / "Makefile"
    makefile.write_text(makefile.read_text() + "\nEXTRA_ARGS += --timing --debug-self-test\n")
    built, ran = run_verilator_workspace(
        tmp_path, top_module="M", test_module="timing_tb", role="graph-path",
        outputs=(), compile_timeout_s=180, simulate_timeout_s=30)
    assert all(row["returncode"] == 0 for row in built.commands), built.as_dict()
    assert command_result(ran, "graph-path:simulate")["returncode"] == 0, ran.as_dict()
