"""Syntax-independent ROM acceptance: typed values, composition and provenance."""

import pytest

from behave.sim.rtl.constant_arrays import UnsupportedConstantArray, _nodes, normalize_constant_arrays
from behave.sim.rtl.source_bindings import bind_source_events
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_rom_normalization import _compile, _typed
from .test_rtl_source_plan import _parse, _run


def _values(tree):
    return {node["name"]: node["value"] for node in _nodes(tree)
            if node.get("kind") == "Parameter" and node["name"].startswith("check_")}


@pytest.mark.eda("slang", "yosys")
@pytest.mark.parametrize("type_", ["logic [7:4]", "logic signed [4:7]", "reg [0:0]",
                                     "bit [3:0]", "int", "byte unsigned", "word_t"])
@pytest.mark.parametrize("pattern", ["'{1,-1,2,0}", "'{2{1,-1}}",
                                      "'{3:-1,default:2}", "'{2:1,3:2,4:3,5:-1}",
                                      "'{'x,4'b1x0z,'z,0}"])
def test_integral_elements_and_patterns_keep_compiler_values(tmp_path, type_, pattern):
    probes = ["1", "2", "3", "4", "5", "6", "3'bx", "3'bz", "-1"]
    source = ("module M(input [2:0] a, output signed [63:0] q);\n"
              "typedef logic signed [7:4] word_t;\n"
              f"localparam {type_} TABLE[2:5] = {pattern};\n"
              "function automatic signed [63:0] read_word(input logic signed [31:0] index);\n"
              "read_word = TABLE[index]; endfunction\n"
              + "\n".join(f"localparam signed [63:0] check_{i} = read_word({index});"
                          for i, index in enumerate(probes))
              + "\nlocalparam check_dims=$dimensions(TABLE);\n"
              "assign q = TABLE[a]; endmodule\n")
    cst, before = _typed(tmp_path / "before", source, allow_oob=True)
    normalized = normalize_constant_arrays(source, cst, before)
    _, after = _typed(tmp_path / "after", normalized.text, allow_oob=True)
    assert _values(before) == _values(after)
    # Out-of-bounds constant probes are diagnostic-only. The production compiler
    # still applies its ordinary diagnostic policy to submitted source.
    dynamic = "\n".join(line for line in source.splitlines() if "check_" not in line) + "\n"
    _compile(tmp_path / "compiled", dynamic)
    # Independently prove known outputs in the actual Yosys relation, including
    # two-state X/Z initialization and invalid-address defaults. Slang-vs-Slang
    # agreement alone would miss Yosys treating bit/int as four-state storage.
    assertions = []
    for i, address in enumerate(probes[:6]):
        value = _values(before)[f"check_{i}"]
        if "d" in value:
            expected = ((-1 if value.startswith("-") else 1) * int(value.split("d", 1)[1])) % (1 << 64)
            assertions.append(f"sat -verify -set a {address} -prove q 64'd{expected}")
    _run(tmp_path / "compiled", "yosys", "-Q", "-T", "-p",
         "read_rtlil design.rtlil; proc; opt; " + "; ".join(assertions))


COMPOSED = """module M(input clk, input [1:0] a, output logic [7:0] q);
typedef logic [7:4] word_t;
localparam word_t LEFT[0:3] = '{2,0,3,1}, RIGHT[0:3] = '{0:5,default:7}, C=2;
localparam word_t THIRD[0:3] = '{LEFT[1], RIGHT[0], C, LEFT[$right(LEFT)]};
always @(posedge clk) begin
  if (RIGHT[ // the outer read must include the rewritten child
       a[0] ? LEFT[a] : THIRD[a] /* no generated comment can swallow code */
      ] == 5) q = RIGHT[LEFT[a]];
  else q = THIRD[a];
end
endmodule
"""


@pytest.mark.eda("slang", "yosys")
def test_nested_reads_multiline_and_multiple_declarators_share_source_binding(tmp_path):
    plan = plan_source(COMPOSED, _parse(tmp_path / "parse", COMPOSED))
    original = _compile(tmp_path / "original", COMPOSED)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    scoped = _compile(tmp_path / "scoped", plan.compiler.text, scoped=True)
    bindings = bind_source_events(plan, original, annotated, filename="design.sv", scoped=scoped)
    assert bindings.occurrences
    assert all("__behave_rom_" not in row.goal for row in bindings.occurrences)


@pytest.mark.eda("slang", "yosys")
def test_parameterized_repetition_and_default_do_not_bake_in_one_instance(tmp_path):
    source = """module R #(parameter N=4, L=2, H=L+N-1, W=8)(input [3:0] a, output [W-1:0] q);
localparam logic [W-1:0] A[L:H]='{N{-1}}, B[L:H]='{L:1,default:2};
localparam [W-1:0] check_first=B[L], check_last=B[H], check_repeat=A[H];
assign q=A[a]+B[a];
endmodule
module M(input [3:0] a, output [23:0] q);
R #(4,2,5,8) x(a,q[7:0]); R #(7,9,3,16) y(a,q[23:8]);
endmodule
"""
    cst, before = _typed(tmp_path / "before", source)
    normalized = normalize_constant_arrays(source, cst, before)
    _, after = _typed(tmp_path / "after", normalized.text)
    values = lambda tree: [node["value"] for node in _nodes(tree)
                          if node.get("kind") == "Parameter" and node["name"].startswith("check_")]
    assert values(before) == values(after)
    assert len(values(before)) == 6
    _compile(tmp_path / "compiled", source)


@pytest.mark.eda("slang", "yosys")
def test_index_width_is_not_widened_by_normalization(tmp_path):
    source = """module M(input [1:0] a, output [7:0] q);
localparam logic [7:0] TABLE[0:3]='{11,22,33,44};
localparam check_wrap=TABLE[2'b11+2'b01], check_signed=TABLE[2'sb11+2'sb01];
assign q=TABLE[a+2'b01]; endmodule
"""
    cst, before = _typed(tmp_path / "before", source)
    plan = normalize_constant_arrays(source, cst, before)
    _, after = _typed(tmp_path / "after", plan.text)
    assert _values(before) == _values(after)
    assert set(_values(before).values()) == {"8'd11"}
    _compile(tmp_path / "compiled", source)
    _run(tmp_path / "compiled", "yosys", "-Q", "-T", "-p",
         "read_rtlil design.rtlil; proc; opt; sat -verify -set a 3 -prove q 11")


@pytest.mark.eda("slang", "yosys")
def test_parameterized_index_width_and_pure_cast_keep_local_overflow(tmp_path):
    source = """module R #(parameter W=2)(input [W-1:0] a, output [7:0] q);
localparam logic[7:0] TABLE[0:7]='{10,20,30,40,50,60,70,80};
assign q=TABLE[$unsigned(a+W'(1))];
endmodule
module M(input [2:0] a,output [15:0] q);
R #(2) narrow(a[1:0],q[7:0]); R #(3) wide(a,q[15:8]);
endmodule
"""
    _compile(tmp_path / "compiled", source)
    _run(tmp_path / "compiled", "yosys", "-Q", "-T", "-p",
         "read_rtlil design.rtlil; proc; opt; sat -verify -set a 3 -prove q 12810")


@pytest.mark.eda("slang", "yosys")
def test_declared_bound_expression_keeps_its_own_width(tmp_path):
    source = """module M(input [2:0] a, output [3:0] q);
localparam logic [3:0] TABLE[(2'b11+2'b01):5]='{0,1,2,3,4,5};
localparam logic [3:0] OTHER[0:5]='{(2'b11+2'b01):9,default:1};
localparam check_count=$size(TABLE), check_first=TABLE[0];
localparam check_key=OTHER[0];
assign q=TABLE[a]; endmodule
"""
    cst, before = _typed(tmp_path / "before", source)
    plan = normalize_constant_arrays(source, cst, before)
    _, after = _typed(tmp_path / "after", plan.text)
    assert _values(before) == _values(after)
    _compile(tmp_path / "compiled", source)
    _run(tmp_path / "compiled", "yosys", "-Q", "-T", "-p",
         "read_rtlil design.rtlil; proc; opt; sat -verify -set a 0 -prove q 0")


@pytest.mark.eda("slang", "yosys")
def test_element_type_queries_do_not_see_the_flattened_layout(tmp_path):
    queries = ("size", "bits", "left", "right", "low", "high", "increment", "dimensions", "unpacked_dimensions")
    source = ("module M(input [1:0] a, output [31:0] q);\n"
              "typedef logic signed [7:4] word_t; localparam word_t TABLE[0:3]='{default:1};\n"
              + "\n".join(f"localparam check_{name}=${name}(TABLE[a]);" for name in queries)
              + "\nassign q=check_left; endmodule\n")
    cst, before = _typed(tmp_path / "before", source)
    plan = normalize_constant_arrays(source, cst, before)
    _, after = _typed(tmp_path / "after", plan.text)
    assert _values(before) == _values(after)
    _compile(tmp_path / "compiled", source)


@pytest.mark.eda("slang")
@pytest.mark.parametrize("declaration,expression", [
    ("localparam logic A[0:1]='{0,1};", "A[a]"),
    ("localparam logic [1:0][1:0] A[0:1]='{0,1};", "A[a]"),
    ("localparam logic [3:0] A[0:1][0:1]='{'{0,1},'{2,3}};", "A[a][a]"),
    ("localparam logic [3:0] A[0:1]='{0,1};", "$bits($typename(A[a]))"),
    ("localparam logic [7:4] A[0:1]='{0,1};", "$left(A[a],1)"),
    ("localparam logic [7:4] A[0:1]='{0,1};", "$left($signed(A[a]))"),
    ("localparam logic [7:4] A[0:1]='{0,1};", "A[a][4]"),
])
def test_unrepresented_type_forms_refuse_before_native_parser(tmp_path, declaration, expression):
    source = f"module M(input a, output [31:0] q); {declaration} assign q={expression}; endmodule\n"
    cst, ast = _typed(tmp_path, source)
    with pytest.raises(UnsupportedConstantArray):
        normalize_constant_arrays(source, cst, ast)
