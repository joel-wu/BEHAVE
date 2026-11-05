"""Source-query capture exposes storage, not all HDL parameters and helpers."""

import json

import pytest

from behave.sim.harness import generate_makefile


def test_boundary_build_has_an_explicit_storage_manifest(tmp_path):
    generate_makefile(tmp_path, "M", coverage=False, boundary_signals=("z", "a", "z"))
    makefile = (tmp_path / "Makefile").read_text()
    assert "--no-public-flat-rw $(CURDIR)/visibility.vlt" in makefile
    assert (tmp_path / "visibility.vlt").read_text() == (
        '`verilator_config\npublic_flat_rw -module "*" -port "*"\n'
        'public_flat_rw -module "*" -var "a"\n'
        'public_flat_rw -module "*" -var "z"\n')


def test_explicit_state_audits_keep_full_visibility(tmp_path):
    generate_makefile(tmp_path, "M", coverage=False, expose_internal=True,
                      boundary_signals=("x",))
    assert "EXTRA_ARGS += --public-flat-rw" in (tmp_path / "Makefile").read_text()
    assert not (tmp_path / "visibility.vlt").exists()


def test_score_only_build_needs_ports_but_external_tb_keeps_default(tmp_path):
    generate_makefile(tmp_path, "M", coverage=False)
    assert not (tmp_path / "visibility.vlt").exists()
    generate_makefile(tmp_path, "M", coverage=False, boundary_signals=())
    assert (tmp_path / "visibility.vlt").read_text() == (
        '`verilator_config\npublic_flat_rw -module "*" -port "*"\n')
    assert "--no-public-flat-rw" in (tmp_path / "Makefile").read_text()


@pytest.mark.isolation
@pytest.mark.eda("verilator")
@pytest.mark.parametrize("escaped", [False, True])
def test_boundary_handles_and_parameter_names_survive_native_build(tmp_path, escaped):
    from behave.sim.rtl.tool_isolation import run_verilator_workspace
    from behave.sim.rtl.tool_protocol import command_result
    from tests.sim.test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    identifier = r"\dotted.name " if escaped else "named"
    # Parameter names collide with C++ macros only in the old global profile.
    # Escaped VPI names retain that pre-existing profile, without new guessing.
    parameter = "constant" if escaped else "NAN"
    source = r'''
module child(input [3:0] a,output [3:0] q);
    localparam logic [31:0] PARAMETER=32'h7fc00000;
    assign q=a+PARAMETER[3:0];
endmodule
module M(input [3:0] a,output [3:0] q);
    logic [3:0] memory[1:2];
    logic [3:0] IDENTIFIER;
    for(genvar i=0;i<2;i++) begin:g
        logic [3:0] held;
        always_comb held=a+i;
    end
    always_comb begin
        memory[1]=a+1; memory[2]=a+2; IDENTIFIER=a+3;
    end
    child c(a,q);
endmodule
'''
    (tmp_path / "rtl.sv").write_text(source.replace("PARAMETER", parameter).replace("IDENTIFIER", identifier))
    (tmp_path / "visibility_tb.py").write_text('''
import json
from pathlib import Path
import cocotb
from cocotb.triggers import Timer
from behave.sim.rtl.source_handles import exact_handle, path_handle, read_handle

@cocotb.test()
async def observe(dut):
    samples=[]
    paths=[("memory",1),("memory",2),("IDENTIFIER",),("g",0,"held"),("g",1,"held")]
    for value in (0,1,7,15):
        dut.a.value=value
        await Timer(1,unit="ns")
        samples.append([int(dut.q.value),*[read_handle(path_handle(dut,path)) for path in paths]])
    Path("visibility.json").write_text(json.dumps(samples))
'''.replace("IDENTIFIER", "dotted.name" if escaped else "named"))
    generate_makefile(tmp_path, "M", tb_module="visibility_tb", coverage=False,
                      boundary_signals=("memory", "dotted.name" if escaped else "named", "held"))
    built, ran = run_verilator_workspace(
        tmp_path, top_module="M", test_module="visibility_tb", role="boundary-visibility",
        outputs=("visibility.json",), compile_timeout_s=120, simulate_timeout_s=30)
    assert all(row["returncode"] == 0 for row in built.commands), built.as_dict()
    assert command_result(ran, "boundary-visibility:simulate")["returncode"] == 0, ran.as_dict()
    # The locked cocotb/VPI binding does not resolve this literal dotted name,
    # even with full visibility. Preserve that explicit unavailable sample;
    # this optimization must not claim to repair its separate name mapping.
    assert json.loads((tmp_path / "visibility.json").read_text()) == [
        [a,(a+1)%16,(a+2)%16,None if escaped else (a+3)%16,a,(a+1)%16]
        for a in (0,1,7,15)]
