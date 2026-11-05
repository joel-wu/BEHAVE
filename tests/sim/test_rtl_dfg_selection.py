"""Many disjoint drivers of shared variables retain native behavior."""

import pytest

from behave.sim.harness import generate_makefile


@pytest.mark.isolation
@pytest.mark.eda("verilator")
@pytest.mark.parametrize("count", [8, 1024])
def test_shared_variable_driver_closure_preserves_each_latch_lane(tmp_path, count):
    from behave.sim.rtl.tool_isolation import run_verilator_workspace
    from behave.sim.rtl.tool_protocol import command_result
    from tests.sim.test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    source = f"module M(input en, a, output logic [{count-1}:0] q, r);\n"
    for i in range(count):
        source += (f"always_latch if (en) begin q[{i}] = a ^ 1'b{i % 2}; "
                   f"r[{i}] = ~q[{i}]; end\n")
    (tmp_path / "rtl.sv").write_text(source + "endmodule\n")
    (tmp_path / "lanes_tb.py").write_text('''
import cocotb
from cocotb.triggers import Timer

@cocotb.test()
async def lanes(dut):
    previous = None
    for en, a in ((1,0), (0,1), (1,1), (0,0), (1,0)):
        dut.en.value, dut.a.value = en, a
        await Timer(1, unit="ns")
        if en:
            previous = sum((a ^ (i % 2)) << i for i in range(COUNT))
        assert int(dut.q.value) == previous
        assert int(dut.r.value) == previous ^ ((1 << COUNT)-1)
'''.replace("COUNT", str(count)))
    generate_makefile(tmp_path, "M", tb_module="lanes_tb", coverage=False)
    built, ran = run_verilator_workspace(
        tmp_path, top_module="M", test_module="lanes_tb", role="dfg-selection",
        outputs=(), compile_timeout_s=180, simulate_timeout_s=30)
    assert all(row["returncode"] == 0 for row in built.commands), built.as_dict()
    assert command_result(ran, "dfg-selection:simulate")["returncode"] == 0, ran.as_dict()
