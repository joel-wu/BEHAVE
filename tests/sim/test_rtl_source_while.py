"""Finite while queries preserve source tests, all goals and native witnesses."""

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_bounded_source_loops import _assert_closure, _lower
from .test_rtl_source_ir import _encode, _run_activation

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("declaration,initial,condition,step,expected", [
    ("int i;", "i=0;", "i<a", "i++", lambda a: a),
    ("int i;", "i=a;", "i<15", "i++", lambda a: 15-a),
    ("int i;", "i=0;", "i<=a", "i+=2", lambda a: a//2+1),
    ("int i;", "i=15;", "i>a", "i--", lambda a: 15-a),
    ("int i;", "i=15;", "i>=int'(a)", "i-=2", lambda a: (15-a)//2+1),
    ("logic [4:0] i;", "i=0;", "i<a", "i++", lambda a: a),
    ("logic [3:0] i;", "i=15;", "i>a", "i--", lambda a: 15-a),
    ("logic signed [5:0] i;", "i=-8;", "i<int'(a)", "i++", lambda a: a+8),
    ("int i;", "i=0;", "i<(a&3)", "i=i+1", lambda a: a&3),
    ("int i;", "i=0;", "i<4'(a+1)", "++i", lambda a: (a+1)%16),
    ("int i;", "i=0;", "i<a && a[0]", "i++", lambda a: a if a&1 else 0),
    ("int i;", "i=0;", "a[0] && i<a", "i++", lambda a: a if a&1 else 0),
    ("int i;", "i=0;", "a<10 && i<a", "i++", lambda a: a if a<10 else 0),
])
def test_every_while_goal_has_exact_local_answer_and_native_witness(
        tmp_path, record_property, declaration, initial, condition, step, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      {declaration}
      always @(posedge clk) begin
        q=0; {initial}
        while({condition}) begin
          q=q+1;
          if(a==7 && i==1) q=q;
          if(1'b0) q=99;
          {step};
        end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("body,expected", [
    ("q=q+1; b=b-1; i++;", lambda a: (a+1)//2),
    ("j=0; while(j<(a&3)) begin q=q+1; j++; end i++;", lambda a: a*(a&3)),
])
def test_actual_mutating_bound_and_nested_back_edges(tmp_path, record_property, body, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      int i,j; reg [3:0] b;
      always @(posedge clk) begin
        q=0; b=a; i=0;
        while(i<b) begin if(1'b0) q=99; {body} end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.eda("verilator", "make")
def test_effectful_condition_runs_through_the_final_false_test(tmp_path, record_property):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      int i;
      function automatic [3:0] bound(input [3:0] v);
        q=q+1;
        return v;
      endfunction
      always @(posedge clk) begin
        q=0; i=0;
        while(i<bound(a)) begin if(1'b0) q=99; i++; end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: a+1)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("initial,condition,expected", [
    (0, "i<a", lambda a: max(0,a)),
    (-8, "i<a", lambda a: a+8),
    (-1, "i<$unsigned(a)", lambda a: 0),
])
def test_signed_and_unsigned_comparison_domains(tmp_path, record_property, initial, condition, expected):
    source = f"""module M(input clk,input signed [3:0] a,output reg [7:0] q);
      int i;
      always @(posedge clk) begin
        q=0; i={initial};
        while({condition}) begin q=q+1; if(1'b0) q=99; i++; end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(-8,8), expected)


@pytest.mark.eda("verilator", "make")
def test_early_return_keeps_only_executed_native_events(tmp_path, record_property):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        int i;
        i=0; f=0;
        while(i<v) begin if(i==2) return 9; if(1'b0) f=99; f=i+1; i++; end
      endfunction
      always @(posedge clk) q=f(a);
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: a if a<3 else 9)


def test_typed_literal_bounds_do_not_require_for_header_metadata():
    from behave.sim.rtl.source_loop_constants import _integral_interval

    kind = {"kind":"PredefinedIntegerType", "bitWidth":32, "isSigned":True}
    assert _integral_interval({"kind":"IntegerLiteral", "type":kind, "value":"32'sd15"}, {}) == (15,15)


@pytest.mark.parametrize("kind", ["Parameter", "EnumValue", "Genvar"])
def test_named_bounds_use_bound_symbol_values_not_spelling(kind):
    from behave.sim.rtl.source_loop_constants import _integral_interval

    type_ = {"kind":"PredefinedIntegerType", "bitWidth":32, "isSigned":True}
    symbols = {"1":{"kind":kind, "value":"32'sd4"}, "2":{"kind":kind, "value":"32'sd7"}}
    for identity, expected in (("1",4),("2",7)):
        assert _integral_interval({"kind":"NamedValue", "type":type_, "symbol":identity+" N"},
                                  symbols) == (expected,expected)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("constants,bound,step,expected", [
    ("parameter int N=7; localparam int STEP=2;", "N", "STEP", 4),
    ("typedef enum int {LIMIT=3} bounds_t;", "LIMIT", "1", 3),
])
def test_elaborated_constants_keep_complete_while_goals(
        tmp_path, record_property, constants, bound, step, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      {constants} int i;
      always @(posedge clk) begin
        q=0; i=0;
        while(i<{bound}) begin q=q+1; if(1'b0) q=99; i=i+{step}; end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: expected)


@pytest.mark.eda("verilator", "make", "iverilog", "vvp")
def test_effectful_while_matches_an_independent_simulator(tmp_path):
    from .test_rtl_source_activation_scheduling import _assert_entries

    source = """module M(input [7:0] a,output logic [31:0] q=0);
      int i;
      function automatic [3:0] bound(input [3:0] v);
        q=q+1;
        return v;
      endfunction
      always @(posedge a[0]) begin
        q=0; i=0;
        while(i<bound(a[4:1])) begin i++; end
      end
    endmodule"""
    # The helper's call count is the numeric oracle; %m inside a function is
    # a lexical helper scope, not the observer's module-instance coordinate.
    rows = _assert_entries(tmp_path, source, [0,1,0,3,2,31], ("i++;",),
                           enumerate_iterations=False)
    assert [value for value, _ in rows] == [0,1,1,2,2,16]


@pytest.mark.parametrize("place", ["condition", "body"])
def test_helper_cannot_hide_a_second_counter_write(tmp_path, place):
    condition = "i<bound(a)" if place == "condition" else "i<a"
    call = "q=bound(a);" if place == "body" else ""
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      int i;
      function automatic [3:0] bound(input [3:0] v); i=i-1; return v; endfunction
      always @(posedge clk) begin i=0; while({condition}) begin {call} i++; end end
    endmodule"""
    with pytest.raises(Refused, match="rtl-source-ir-operation-unsupported"):
        _lower(tmp_path, source)


@pytest.mark.parametrize("declaration,initial,condition,body", [
    ("int i;", "i=0;", "i<a", "q=q+1; i--;"),
    ("int i;", "i=0;", "i<a", "i=i-1; i++;"),
    ("int i;", "i=0;", "i<a", "q=q+1; if(a) i++;"),
    ("int i;", "i=0;", "i!=a", "i++;"),
    ("int i;", "i=0;", "i<a || a[0]", "i++;"),
    ("logic [3:0] i;", "i=0;", "i<=a", "i++;"),
    ("logic [3:0] i;", "i=15;", "i>=a", "i--;"),
    ("logic signed [3:0] i;", "i=0;", "i<a", "i++;"),
    ("logic [3:0] i;", "i=14;", "i<15", "i+=2;"),
    ("int i;", "i=0;", "i<a", "i=4'(i+1);"),
])
def test_unproved_or_wrapping_progress_is_not_silently_truncated(
        tmp_path, declaration, initial, condition, body):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      {declaration}
      always @(posedge clk) begin {initial} while({condition}) begin {body} end end
    endmodule"""
    with pytest.raises(Refused, match="rtl-source-ir-(operation-unsupported|loop-domain-unproved)"):
        _lower(tmp_path, source)


@pytest.mark.parametrize("initialized", [False, True])
def test_while_return_storage_and_early_return(tmp_path, initialized):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        int i;
        {"f=0;" if initialized else ""}
        i=0;
        while(i<v) begin if(i==2) return 9; f=i+1; i++; end
      endfunction
      always @(posedge clk) q=f(a);
    endmodule"""
    if not initialized:
        with pytest.raises(Refused, match="return-undefined"):
            _lower(tmp_path, source)
        return
    _, lowered, names = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in range(16):
        post, _, occurrences = _run_activation(
            lowered, names, {name: a if name == "a" else 0 for name in names}, encoded)
        assert post[names["q"]] == (a if a<3 else 9)
        assert any(hit.role == "zero" for hit in occurrences) == (a==0)
