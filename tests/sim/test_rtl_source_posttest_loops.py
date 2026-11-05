"""Whole-catalogue closure for captured repeat counts and post-tested loops."""

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_bounded_source_loops import _assert_closure, _lower
from .test_rtl_source_ir import _encode, _run_activation

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("signed,count,body,expected", [
    (False, "a", "q=q+1;", lambda a: a),
    (True, "a", "q=q+1;", lambda a: max(0, a)),
    (False, "4'(a+1)", "q=q+1;", lambda a: (a+1)%16),
    (False, "a&3", "q=q+1;", lambda a: a&3),
    (False, "b", "q=q+1; b=0;", lambda a: a),
    (False, "b++", "q=q+b;", lambda a: a*((a+1)%16)),
    (False, "a ? 2 : 0", "q=q+1;", lambda a: 2 if a else 0),
    (False, "N", "q=q+1;", lambda a: 3),
    (False, "0", "q=q+1;", lambda a: 0),
    (False, "-1", "q=q+1;", lambda a: 0),
])
def test_repeat_captures_typed_count_once_and_keeps_every_goal(
        tmp_path, record_property, signed, count, body, expected):
    source = f"""module M(input clk,input {'signed' if signed else ''} [3:0] a,output reg [7:0] q);
      parameter int N=3; reg [3:0] b;
      always @(posedge clk) begin
        q=0; b=a;
        repeat({count}) begin {body} if(1'b0) q=99; end
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(-8,8) if signed else range(16), expected)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("declaration,initial,condition,step,expected", [
    ("int i;", "i=0;", "i<a", "i++", lambda a: max(1,a)),
    ("int i;", "i=a;", "i<7", "i++", lambda a: max(1,7-a)),
    ("int i;", "i=0;", "i<=a", "i+=2", lambda a: a//2+1),
    ("int i;", "i=15;", "i>int'(a)", "i--", lambda a: max(1,15-a)),
    ("int i;", "i=15;", "i>=int'(a)", "i-=2", lambda a: (15-a)//2+1),
    ("logic [4:0] i;", "i=0;", "i<a", "i++", lambda a: max(1,a)),
    ("logic signed [5:0] i;", "i=-8;", "i<int'(a)", "i++", lambda a: a+8),
    ("int i;", "i=0;", "a[0] && i<a", "i++", lambda a: a if a&1 else 1),
])
def test_do_while_runs_body_before_any_condition(
        tmp_path, record_property, declaration, initial, condition, step, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      {declaration}
      always @(posedge clk) begin
        q=0; {initial}
        do begin q=q+1; if(1'b0) q=99; {step}; end while({condition});
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("body,expected", [
    ("repeat(a&3) begin q=q+1; end i++;", lambda a: max(1,a)*(a&3)),
    ("q=q+1; b=b>>1; i++;", lambda a: next(i for i in range(1,17) if i >= (a>>i))),
])
def test_nested_counts_and_mutating_do_bound(tmp_path, record_property, body, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      int i; reg [3:0] b;
      always @(posedge clk) begin
        q=0; i=0; b=a;
        do begin if(1'b0) q=99; {body} end while(i<b);
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("loop,expected", [
    ("repeat(bound(a)) begin if(1'b0) q=99; q=q+1; end", lambda a: a+1),
    ("do begin if(1'b0) q=99; i++; end while(i<bound(a));", lambda a: max(1,a)),
])
def test_evaluated_header_effects_follow_loop_form(tmp_path, record_property, loop, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      int i;
      function automatic [3:0] bound(input [3:0] v); q=q+1; return v; endfunction
      always @(posedge clk) begin q=0; i=0; {loop} end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("loop,expected", [
    ("repeat(v) begin if(i==2) return 9; if(1'b0) f=99; f=i+1; i++; end",
     lambda a: a if a<3 else 9),
    ("do begin if(i==2) return 9; if(1'b0) f=99; f=i+1; i++; end while(i<v);",
     lambda a: max(1,a) if a<3 else 9),
])
def test_early_returns_stop_later_bodies_and_tests(tmp_path, record_property, loop, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        int i; i=0; f=0; {loop}
      endfunction
      always @(posedge clk) q=f(a);
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.parametrize("loop,defined", [
    ("do begin f=7; i++; end while(i<v);", True),
    ("do f=7; while(0);", True),
    ("repeat(1) f=7;", True),
    ("repeat(0) f=7;", False),
    ("repeat(-1) f=7;", False),
    ("repeat(v) f=7;", False),
    ("do begin if(v) f=7; i++; end while(i<v);", False),
])
def test_definite_return_assignment_respects_first_body(tmp_path, loop, defined):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v); int i; i=0; {loop} endfunction
      always @(posedge clk) q=f(a);
    endmodule"""
    if not defined:
        with pytest.raises(Refused, match="return-undefined"):
            _lower(tmp_path, source)
        return
    _, lowered, names = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in range(16):
        post, _, _ = _run_activation(lowered, names, {n: a if n=="a" else 0 for n in names}, encoded)
        assert post[names["q"]] == 7


@pytest.mark.parametrize("declaration,initial,condition,step", [
    ("logic [3:0] i;", "i=15;", "i<a", "i++"),
    ("logic [3:0] i;", "i=0;", "i>a", "i--"),
    ("int i;", "i=0;", "i<a", "i--"),
    ("int i;", "i=0;", "i<a", "i=i-1; i++"),
])
def test_do_first_iteration_is_not_exempt_from_progress_proof(tmp_path, declaration, initial, condition, step):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      {declaration} always @(posedge clk) begin
        {initial} do begin q=q+1; {step}; end while({condition});
      end
    endmodule"""
    with pytest.raises(Refused, match="rtl-source-ir-(operation-unsupported|loop-domain-unproved)"):
        _lower(tmp_path, source)


@pytest.mark.eda("verilator", "make", "iverilog", "vvp")
@pytest.mark.parametrize("loop,expected", [
    ("repeat(bound(a[4:1])) begin q=q+1; i++; end", [0,1,1,2,2,16]),
    ("do begin i++; end while(i<bound(a[4:1]));", [0,1,1,1,1,15]),
    ("repeat($signed(a[4:1])) begin q=q+1; i++; end", [0,0,0,1,1,0]),
])
def test_posttest_and_count_effects_match_independent_simulator(tmp_path, loop, expected):
    from .test_rtl_source_activation_scheduling import _assert_entries

    source = f"""module M(input [7:0] a,output logic [31:0] q=0);
      int i;
      function automatic [3:0] bound(input [3:0] v); q=q+1; return v; endfunction
      always @(posedge a[0]) begin q=0; i=0; {loop} end
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,3,2,31], ("i++;",), enumerate_iterations=False)
    assert [value for value, _ in rows] == expected


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("posttest", [False, True])
@pytest.mark.parametrize("condition,effect", [("0", False), ("b++ & 0", True)])
def test_proven_false_test_needs_no_counter_but_preserves_effects(
        tmp_path, record_property, posttest, condition, effect):
    body = "begin q=q+1; if(1'b0) q=99; end"
    loop = f"do {body} while({condition});" if posttest else f"while({condition}) {body}"
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      reg [3:0] b;
      always @(posedge clk) begin q=0; b=a; {loop} q=q+b; end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: int(posttest)+(a+int(effect))%16)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("posttest", [False, True])
def test_return_skips_posttest_effect_but_not_repeat_entry(tmp_path, record_property, posttest):
    body = "begin if(i==1) return 9; if(1'b0) f=99; f=i+1; i++; end"
    loop = (f"do {body} while(i<bound(v));" if posttest else f"repeat(bound(v)) {body}")
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      reg [3:0] tests;
      function automatic [3:0] bound(input [3:0] v); tests=tests+1; return v; endfunction
      function automatic [7:0] f(input [3:0] v); int i; i=0; f=0; {loop} endfunction
      always @(posedge clk) begin tests=0; q=f(a); q=q+(tests<<4); end
    endmodule"""
    # A repeat test always runs once on entry. A do test runs after the first
    # body, but must not run again after the second body returns.
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 16 + (9 if a>=2 else max(int(posttest),a)))


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("initial", [0, -8])
def test_signed_posttest_input_does_not_suppress_first_body(tmp_path, record_property, initial):
    source = f"""module M(input clk,input signed [3:0] a,output reg [7:0] q);
      int i;
      always @(posedge clk) begin
        q=0; i={initial};
        do begin q=q+1; if(1'b0) q=99; i++; end while(i<a);
      end
    endmodule"""
    _assert_closure(tmp_path, record_property, source, range(-8,8), lambda a: max(1,a-initial))
