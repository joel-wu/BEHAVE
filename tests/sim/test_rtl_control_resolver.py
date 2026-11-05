"""Compiler visibility controls retain literal and wildcard signal selection."""

import json

import pytest

from behave.sim.harness import generate_makefile


@pytest.mark.isolation
@pytest.mark.eda("verilator")
@pytest.mark.parametrize("count", [8, 2048])
def test_many_literal_controls_keep_exact_visibility_and_wildcard_union(tmp_path, count):
    from behave.sim.rtl.tool_isolation import run_verilator_workspace
    from behave.sim.rtl.tool_protocol import command_result
    from tests.sim.test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    source = "module M(input [7:0] a, output [7:0] q);\n"
    source += "\n".join(f"wire [7:0] net_{i} = a + 8'd{i % 256};" for i in range(count))
    source += "\nwire [7:0] wildcard_a = a ^ 8'h55; wire [7:0] wildcard_b = a ^ 8'haa;\n"
    source += "wire [7:0] hidden = a ^ 8'h33; assign q = hidden; endmodule\n"
    (tmp_path / "rtl.sv").write_text(source)
    (tmp_path / "visibility_tb.py").write_text('''
import json
from pathlib import Path
import cocotb
from cocotb.triggers import Timer

@cocotb.test()
async def observe(dut):
    samples=[]
    for a in (0, 1, 127, 255):
        dut.a.value=a
        await Timer(1,unit="ns")
        values=[int(getattr(dut,f"net_{i}").value) for i in range(COUNT)]
        assert values == [(a+i)%256 for i in range(COUNT)]
        assert int(dut.wildcard_a.value) == a^0x55
        assert int(dut.wildcard_b.value) == a^0xaa
        assert int(dut.q.value) == a^0x33
        assert not hasattr(dut,"hidden")
        samples.append([a,values[0],values[-1]])
    Path("visibility.json").write_text(json.dumps(samples))
'''.replace("COUNT", str(count)))
    generate_makefile(tmp_path, "M", tb_module="visibility_tb", coverage=False,
                      boundary_signals=tuple(f"net_{i}" for i in range(count)))
    controls = tmp_path / "visibility.vlt"
    # Overlap a wildcard with a literal and retain an unrelated wildcard group.
    controls.write_text(controls.read_text() +
        'public_flat_rw -module "M" -var "net_?"\n'
        'public_flat_rw -module "M" -var "wildcard_*"\n')
    built, ran = run_verilator_workspace(tmp_path, top_module="M", test_module="visibility_tb",
        role="control-resolver", outputs=("visibility.json",),
        compile_timeout_s=180, simulate_timeout_s=30)
    assert all(row["returncode"] == 0 for row in built.commands), built.as_dict()
    assert command_result(ran, "control-resolver:simulate")["returncode"] == 0, ran.as_dict()
    assert json.loads((tmp_path / "visibility.json").read_text()) == [
        [a, a, (a+count-1)%256] for a in (0, 1, 127, 255)]
