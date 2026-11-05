"""Finite SV bit queries share source execution, goal predicates and witnesses."""

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_bounded_source_loops import _assert_closure, _lower

pytestmark = [pytest.mark.eda("slang", "verilator", "make"), pytest.mark.solver]


@pytest.mark.parametrize("expression,expected", [
    ("$countones(a)", lambda a: a.bit_count()),
    ("$countones($signed(a))", lambda a: a.bit_count()),
    ("$countones({4'hf,a})", lambda a: 4 + a.bit_count()),
    ("$countones(~a)", lambda a: 4 - a.bit_count()),
    ("$countones(a[2:1])", lambda a: ((a >> 1) & 3).bit_count()),
    ("$onehot($signed(a))", lambda a: int(a.bit_count() == 1)),
    ("$onehot0($signed(a))", lambda a: int(a.bit_count() <= 1)),
])
def test_every_bit_query_goal_matches_native_and_replays_its_witness(
        tmp_path, record_property, expression, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q={expression};
        if(q==1) q=q;
        if(1'b0) q=99;
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.parametrize("query", ["$countones", "$onehot", "$onehot0"])
def test_bit_query_evaluates_effectful_signed_argument_once(tmp_path, record_property, query):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic signed [3:0] take(input [3:0] v);
        q=q+1; return v;
      endfunction
      always @(posedge clk) begin
        q=10;
        if({query}(take(a))==1) q=q+2;
        if(1'b0) q=99;
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 11 + 2 * (a.bit_count() <= 1 if query == "$onehot0"
                                        else a.bit_count() == 1))


def test_bit_query_does_not_discard_operand_definedness(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) q=$countones(a[a]);
    endmodule"""
    with pytest.raises(Refused, match="rtl-source-ir-packed-read-domain-unproved"):
        _lower(tmp_path, source)
