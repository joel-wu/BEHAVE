"""Original packed/unpacked ROMs share catalogue, observer and exact semantics."""

import pytest

from behave.sim.rtl.catalogue import catalogue_manifest_of

WORDS = (0, 5, 2, 7, 1, 3)
BODY = """module M(input clk, rst, input [2:0] a, output wire [2:0] q, output reg seen);
DECLARATION
assign q = a < 6 ? READ : 3'd0;
always @(posedge clk) if (rst) seen <= 0; else if (a < 6) seen <= 1; else seen <= 0;
endmodule
"""
UNPACKED = BODY.replace(
    "DECLARATION", "localparam logic [2:0] ROM [0:5] = '{3'd0,3'd5,3'd2,3'd7,3'd1,3'd3};"
).replace("READ", "ROM[a]")
PACKED = BODY.replace(
    "DECLARATION", "localparam logic [17:0] ROM = {3'd3,3'd1,3'd7,3'd2,3'd5,3'd0};"
).replace("READ", "ROM[3*a +: 3]")


@pytest.mark.eda("yosys", "slang", "verilator")
@pytest.mark.isolation
def test_valid_unsupported_rom_remains_a_capability_refusal(tmp_path):
    from behave.sim.core.refusal import Refused

    from .test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    source = "module M(input a, output q); localparam logic TABLE[0:1]='{0,1}; assign q=TABLE[a]; endmodule\n"
    path = tmp_path / "scalar.sv"
    path.write_text(source)
    with pytest.raises(Refused) as refusal:
        catalogue_manifest_of(path, "M")
    assert refusal.value.refusal.kind == "rtl-structural-frontend-unsupported"
    assert "rtl-constant-array-unsupported" in refusal.value.refusal.as_dict()["detail"]["diagnostic"]
    assert path.read_text() == source

