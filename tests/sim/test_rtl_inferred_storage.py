"""Dead process state may disappear; live/explicitly observed latches may not."""

import json
import subprocess

import pytest

from behave.sim import api
from behave.sim.rtl._yosys_state_ownership import state_ownership_document
from behave.sim.rtl.elaboration import STORAGE_INFERENCE_SCRIPT, elaboration_script
from behave.sim.rtl.state_ownership import read_state_ownership_with_execution

BODY = """integer i;
    logic [3:0] value;
    always_comb begin
        value=0;
        if(enable) begin
            for(i=0;i<4;i=i+1) value=value+a[i];
        end else if(a[0]) begin
            for(i=0;i<3;i=i+1) value=value+a[i];
        end
    end
"""


@pytest.mark.eda("yosys")
@pytest.mark.parametrize("keep_counter", [False, True])
def test_process_lowering_retains_real_storage_before_dead_state_cleanup(tmp_path, keep_counter):
    source = (
        """module M(input enable,input [3:0] a,output [3:0] q,output correct);
    """
        + BODY
        + """
    assign q=value;
    wire [3:0] expected=enable ? (4'd0+a[0]+a[1]+a[2]+a[3]) :
                                (a[0] ? (4'd0+a[0]+a[1]+a[2]) : 4'd0);
    assign correct=q==expected;
    endmodule
    """
    )
    path = tmp_path / "design.sv"
    path.write_text(source)
    script = (
        elaboration_script("M")
        + ("setattr -set keep 1 w:i\n" if keep_counter else "")
        + STORAGE_INFERENCE_SCRIPT
        + "proc\nwrite_json before.json\nopt_expr -keepdc\nopt_clean\n"
        + "write_json after.json\n"
        + ("" if keep_counter else "sat -verify -prove correct 1\n")
    )
    (tmp_path / "model.ys").write_text(script)
    result = subprocess.run(
        ("yosys", "-Q", "-T", "-s", "model.ys"), cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    before = json.loads((tmp_path / "before.json").read_text())
    after = json.loads((tmp_path / "after.json").read_text())

    def latches(document):
        return [cell for cell in document["modules"]["M"]["cells"].values() if cell["type"] == "$dlatch"]

    assert latches(before)  # Inference is preserved, never replaced by don't-care.
    assert bool(latches(after)) == keep_counter
    ownership = state_ownership_document(after, "M")
    assert bool(ownership["issues"]) == keep_counter
    if keep_counter:
        assert "unsupported-state-cell" in {x["kind"] for x in ownership["issues"]}
    assert path.read_text() == source


@pytest.mark.cosim
def test_observable_latch_is_not_admitted_as_clock_owned_storage(tmp_path):
    source = """module M(input clk,rst,enable,input [3:0] a,output logic [3:0] q);
        always_comb if(enable) q=a;
    endmodule
    """
    path = tmp_path / "design.sv"
    path.write_text(source)
    contract = api.TaskContract(endpoints=(), clocks=(api.ClockDomain("main", "clk", "rst", reset_restores=True),))
    with pytest.raises(api.Refused, match="unsupported-state-cell"):
        read_state_ownership_with_execution(path, "M", contract)
    assert path.read_text() == source
