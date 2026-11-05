"""Compiler qualification must preserve source effects as well as native hits."""

import pytest

from .test_rtl_source_activation_scheduling import _assert_entries

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


@pytest.mark.parametrize("condition", ["choose(a[1])", "choose(a[1]) && choose(a[2])"])
def test_equal_conditional_arms_do_not_erase_source_effects(tmp_path, condition):
    # Even without instrumentation, discarding this predicate changes calls.
    # The second form also requires retaining the original short-circuit rule.
    statement = "q=(calls<<8)|selected;"
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      int unsigned calls=0;
      logic [7:0] selected;
      function automatic logic choose(input logic value);
        calls=calls+1;
        choose=value;
      endfunction
      always @(posedge a[0]) begin
        selected=({condition}) ? 8'd7 : 8'd7;
        {statement}
      end
    endmodule'''
    _assert_entries(tmp_path, source, [0, 1, 0, 3, 2, 7, 6, 5], (statement,))
