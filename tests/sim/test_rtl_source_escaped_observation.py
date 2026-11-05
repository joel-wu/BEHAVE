"""Observer punctuation must not become part of an escaped HDL identifier."""

import pytest

from .test_rtl_source_activation_scheduling import _assert_entries

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


def test_escaped_nba_source_preserves_values_and_activations(tmp_path):
    source = r"""module M(input [7:0] a,output logic [31:0] q);
      wire [7:0] \cell.D = a;
      logic [7:0] \cell.Q = 0;
      always @(posedge a[0]) \cell.Q <= \cell.D ;
      always_comb q=\cell.Q ;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,3,0,5], (r"q=\cell.Q ;",))
    assert [value for value, _ in rows] == [0,1,1,3,3,5]


def test_escaped_condition_preserves_choices_and_values(tmp_path):
    source = r"""module M(input [7:0] a,output logic [31:0] q);
      wire \cell.enable = a[0];
      always_comb if (\cell.enable ) q=a; else q=0;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,2,3,4,5], ("q=a;", "q=0;"))
    assert [value for value, _ in rows] == [0,1,0,3,0,5]
