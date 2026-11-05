"""Shared compiler early exits: full small domains, not just successful parsing."""

import re

import pytest

from behave.sim.rtl.source_returns import normalize_helper_returns

from .test_rtl_rom_normalization import _compile
from .test_rtl_source_plan import _parse, _run

pytestmark = pytest.mark.eda("slang", "yosys")


CASES = [
    ("return v+1;", lambda v: v+1),
    ("if(v==3) return 7; return v;", lambda v: 7 if v == 3 else v),
    (("if(v[0]) begin if(v[1]) return 99; return 33; end else if(v[2]) return 66; "
      "f=v+1;"), lambda v: (99 if v & 2 else 33) if v & 1 else 66 if v & 4 else v+1),
    (("case(v) 1,2: return 17; 3: begin f=2; return f+1; end "
      "default: f=v; endcase f=f+32;"), lambda v: 17 if v in (1, 2) else 3 if v == 3 else v+32),
    (("logic [7:0] cells[0:1][0:1]; cells[1][0]=v; "
      "if(v[0]) return cells[1][0]+7; cells[1][0]=9; return cells[1][0];"),
     lambda v: v+7 if v & 1 else 9),
    (("logic [7:0] x; x[3:0]=v; x[7:4]=3; if(v[0]) return x; "
      "x[7:4]=4; return x;"), lambda v: v+48 if v & 1 else v+64),
    ("if(v[0]) return 7; f=0; for(integer i=0;i<4;i=i+1) f=f+v;",
     lambda v: 7 if v & 1 else 4*v),
    (("reg __behave_return_0; __behave_return_0=v[0]; "
      "if(__behave_return_0) return 3; return 4;"), lambda v: 3 if v & 1 else 4),
    ("if(v[0]) return 9'h1ff; return -2;", lambda v: 255 if v & 1 else 254),
    ("f=v+9; if(v[0]) return; f=33;", lambda v: v+9 if v & 1 else 33),
]


def _function(body, *, lifetime="automatic"):
    return f"""module M(input [3:0] a, output [7:0] q);
      function {lifetime} [7:0] f(input [3:0] v);
        {body}
      endfunction
      assign q=f(a);
    endmodule
    """


def _truth_table(where, source, expected):
    _compile(where, source)
    checks = "; ".join(f"sat -verify -prove q {expected(v)} -set a {v}" for v in range(16))
    _run(where, "yosys", "-Q", "-T", "-p",
         f"read_rtlil design.rtlil; proc; flatten; memory_map; opt; {checks}")


@pytest.mark.parametrize("body,expected", CASES)
def test_complete_small_input_domain(tmp_path, body, expected):
    source = _function(body)
    cst = _parse(tmp_path / "parse", source)
    plan = normalize_helper_returns(source, cst)
    assert plan.replacements
    assert source.count("\n") == plan.text.count("\n")
    assert not re.search(r"\breturn\b", plan.text)
    _truth_table(tmp_path / "compiled", source, expected)


@pytest.mark.parametrize("lifetime", ["", "automatic"])
def test_each_call_resets_its_private_exit_flag(tmp_path, lifetime):
    source = _function("if(v[0]) return 7; return v;", lifetime=lifetime)
    source = source.replace("assign q=f(a);", "assign q=f(a)+f(a^4'b0001);")
    _truth_table(tmp_path / "compiled", source, lambda v: 7+(v & ~1))


def test_task_return_retains_output_copyout_and_skips_later_writes(tmp_path):
    source = """module M(input [3:0] a, output reg [7:0] q);
      task automatic fill(input [3:0] v, output [7:0] x);
        x=v;
        if(v[0]) return;
        x=v+8;
      endtask
      always @* fill(a,q);
    endmodule"""
    _truth_table(tmp_path / "compiled", source, lambda v: v if v & 1 else v+8)


def test_nested_helpers_keep_distinct_exit_flags(tmp_path):
    source = _function("if(v[0]) return g(v); return 99;")
    source = source.replace("function automatic", """function automatic [7:0] g(input [3:0] v);
      if(v[1]) return 5; return v+1;
      endfunction
      function automatic""", 1)
    _truth_table(tmp_path / "compiled", source, lambda v: (5 if v & 2 else v+1) if v & 1 else 99)


def test_return_expression_composes_with_typed_rom_replacements(tmp_path):
    source = _function("if(v[0]) return ROM[v[1:0]]; return ROM[0]+v;")
    source = source.replace("function automatic", "localparam logic [7:0] ROM[0:3]='{7,9,11,13};\n"
                            "      function automatic", 1)
    _truth_table(tmp_path / "compiled", source, lambda v: (7,9,11,13)[v & 3] if v & 1 else 7+v)


def test_old_style_formals_and_signed_result_conversion(tmp_path):
    source = """module M(input [3:0] a,output [7:0] q);
      function automatic signed [3:0] f;
        input [3:0] v;
        if(v[0]) return -2;
        return v;
      endfunction
      assign q=f(a);
    endmodule"""
    _truth_table(tmp_path / "compiled", source,
                 lambda v: 254 if v & 1 else (v if v < 8 else v+240))


@pytest.mark.solver
@pytest.mark.eda("verilator", "make")
def test_effectful_condition_and_return_expression_are_evaluated_once(tmp_path, record_property):
    from .test_rtl_bounded_source_loops import _assert_closure

    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      reg [7:0] count;
      function automatic [7:0] tick(); count=count+1; return count; endfunction
      function automatic [7:0] f(input [3:0] v);
        if(tick()==1) return tick()+v;
        count=100;
        return 0;
      endfunction
      always @(posedge clk) begin
        count=0;
        q=f(a);
        q=q+count;
        if(1'b0) q=99;
      end
    endmodule"""
    combinational = source.replace("@(posedge clk)", "@*")
    _truth_table(tmp_path / "compiler", combinational, lambda v: v+4)
    _assert_closure(tmp_path, record_property, source, range(16), lambda v: v+4)


@pytest.mark.solver
@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("body,expected", [CASES[index] for index in (2, 3, 4, 8)])
def test_native_all_goal_queries_and_compiler_outputs_agree(tmp_path, record_property, body, expected):
    from .test_rtl_bounded_source_loops import _assert_closure

    _truth_table(tmp_path / "compiler", _function(body), expected)
    source = _function(body).replace("input [3:0] a", "input clk,input [3:0] a").replace(
        "output [7:0] q", "output reg [7:0] q").replace(
        "assign q=f(a);", "always @(posedge clk) begin if(1'b0) q=0; q=f(a); end")
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.parametrize("loop", [
    "for(integer i=0;i<4;i=i+1) if(v[i]) return i;",
    "while(v!=0) begin if(v[0]) return 1; v=v>>1; end",
    "repeat(4) if(v[0]) return 7;",
])
def test_loop_exits_are_not_unsoundly_rewritten_as_body_guards(tmp_path, loop):
    source = _function(loop + " return 0;")
    plan = normalize_helper_returns(source, _parse(tmp_path / "parse", source))
    assert not plan.replacements and plan.text == source


def test_no_return_leaves_bytes_unchanged(tmp_path):
    source = _function("if(v[0]) f=v; else f=v+1;")
    plan = normalize_helper_returns(source, _parse(tmp_path / "parse", source))
    assert not plan.replacements and plan.text == source


def test_shadowed_result_is_not_rewritten_as_assignment_to_inner_local(tmp_path):
    source = _function("begin: inner logic [7:0] f; f=v+9; if(v[0]) return f; return 3; end")
    plan = normalize_helper_returns(source, _parse(tmp_path / "parse", source))
    assert not plan.replacements and plan.text == source
    with pytest.raises(AssertionError):
        _compile(tmp_path / "compiled", source)


def test_only_final_return_does_not_add_control_state(tmp_path):
    source = _function("integer x; x=v+1; return x;")
    plan = normalize_helper_returns(source, _parse(tmp_path / "parse", source))
    assert len(plan.replacements) == 1
    assert "__behave_return_" not in plan.text
    _truth_table(tmp_path / "compiled", source, lambda v: v+1)


def test_expression_and_later_statement_locations_still_name_original_bytes(tmp_path):
    source = _function("if(v[0]) return v+7;\n        f=v+3; // keep this comment")
    plan = normalize_helper_returns(source, _parse(tmp_path / "parse", source))
    for text in ("v+7", "f=v+3", "// keep this comment"):
        for offset in range(len(text)):
            old = source.encode().index(text.encode()) + offset
            new = plan.text.encode().index(text.encode()) + offset
            prefix = plan.text.encode()[:new]
            line = prefix.count(b"\n") + 1
            column = new - prefix.rfind(b"\n")
            expected = (source.encode()[:old].count(b"\n") + 1,
                        old - source.encode()[:old].rfind(b"\n"))
            assert plan.position(line, column) == expected
