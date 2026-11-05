"""Fixed compiler fixtures for typed ROM rewrites and complete source binding."""

import json
from pathlib import Path

import pytest

from behave.sim.rtl.constant_arrays import UnsupportedConstantArray, normalize_constant_arrays
from behave.sim.rtl.elaboration import elaboration_script
from behave.sim.rtl.shared_frontend import compile_source
from behave.sim.rtl.source_bindings import bind_source_events
from behave.sim.rtl.source_plan import plan_source
from behave.sim.rtl.source_replacements import Replacement, SourceReplacements

from .test_rtl_constant_rom import UNPACKED
from .test_rtl_source_plan import _parse, _run


def _typed(where, source, *, allow_oob=False):
    cst = _parse(where, source)
    _run(where, "slang", *(("-Wno-index-oob", "-Wno-range-oob") if allow_oob else ()),
         "--top", "M", "--ast-json", "ast.json", "--ast-json-source-info",
         "--ast-json-detailed-types", "design.sv")
    return cst, json.loads((where / "ast.json").read_text())


def _compile(where, source, *, scoped=False):
    where.mkdir()
    (where / "design.sv").write_text(source)
    (where / "run.ys").write_text(elaboration_script("M", scoped=scoped) + "write_rtlil design.rtlil\n")

    def run(directory, *argv):
        _run(directory, Path(argv[0]).name, *argv[1:])
        return True

    assert compile_source(where, "M", "run.ys", run=run)
    assert (where / "design.sv").read_bytes() == source.encode()
    return (where / "design.rtlil").read_text()


def test_replacement_map_keeps_unchanged_bytes_and_explicit_expression_origins():
    source = "a ROM[i] z\nπ ROM[j] k\n"
    mapped = SourceReplacements(source, (Replacement(2, 8, "expanded(expr)"),))
    assert mapped.position(1, 1) == (1, 1)
    assert mapped.position(1, 6) == (1, 3)
    assert mapped.position(1, 6, end=True) == (1, 9)
    assert mapped.position(1, 18) == (1, 10)
    assert mapped.position(2, 4) == (2, 4)
    assert mapped.locations("design.sv:1.3-1.17|other.sv:1.1-1.3") == "design.sv:1.3-1.9|other.sv:1.1-1.3"
    with pytest.raises(ValueError):
        SourceReplacements(source, (Replacement(8, 12, "x"), Replacement(9, 10, "y")))
    with pytest.raises(ValueError, match="UTF-8"):
        SourceReplacements("aπb", (Replacement(2, 3, "x"),))
    with pytest.raises(TypeError):
        SourceReplacements(source, [Replacement(2, 8, "x")])


@pytest.mark.eda("slang", "yosys")
@pytest.mark.parametrize("source", [UNPACKED, UNPACKED.replace("[0:5]", "[7:2]"),
    UNPACKED.replace("\n", "\r\n"),
    UNPACKED.replace("logic [2:0]", "logic signed [2:0]")])
def test_rom_origins_bind_the_whole_unannotated_and_annotated_relations(tmp_path, source):
    plan = plan_source(source, _parse(tmp_path / "parse", source))
    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    scoped = _compile(tmp_path / "scoped", plan.compiler.text, scoped=True)
    bindings = bind_source_events(plan, original, annotated, filename="design.sv", scoped=scoped)
    assert bindings.occurrences
    assert all("__behave_rom_" not in row.goal for row in bindings.occurrences)


@pytest.mark.eda("slang", "yosys")
def test_parameterized_rom_instances_share_one_transformation(tmp_path):
    from behave.sim.rtl.constant_arrays import _nodes

    source = """module R #(parameter W=3, L=2, H=3)(input clk, input [2:0] a, output [W-1:0] q);
localparam logic [W-1:0] ROM[L:H] = '{1, -1};
localparam [W-1:0] FIRST = ROM[L], LAST = ROM[H];
assign q = ROM[a];
endmodule
module M(input clk, input [2:0] a, output [7:0] q);
R #(3,2,3) x(clk,a,q[2:0]); R #(5,6,5) y(clk,a,q[7:3]);
endmodule
"""
    cst, ast = _typed(tmp_path / "parse", source)
    plan = normalize_constant_arrays(source, cst, ast)
    assert len(plan.replacements) == 4
    _, after = _typed(tmp_path / "after", plan.text)
    values = lambda tree: [n["value"] for n in _nodes(tree)
                          if n.get("kind") == "Parameter" and n.get("name") in {"FIRST", "LAST"}]
    assert values(ast) == values(after)
    assert len(values(ast)) == 4
    _compile(tmp_path / "compiled", source)


@pytest.mark.eda("slang", "yosys")
@pytest.mark.parametrize("expression", ["$size(ROM)", "$bits(ROM)", "$left(ROM)", "$right(ROM)",
                                        "$low(ROM)", "$high(ROM)", "$increment(ROM)",
                                        "$dimensions(ROM)", "$unpacked_dimensions(ROM)",
                                        "ROM[$right(ROM)]", "ROM[$size(ROM)-1]"])
def test_compile_time_queries_keep_original_array_dimensions(tmp_path, expression):
    source = UNPACKED.replace("assign q =", f"localparam N = {expression};\nassign q =")
    cst, ast = _typed(tmp_path / "before", source)
    normalized = normalize_constant_arrays(source, cst, ast)
    _, after = _typed(tmp_path / "after", normalized.text)
    from behave.sim.rtl.constant_arrays import _nodes

    values = lambda tree: [n["value"] for n in _nodes(tree) if n.get("kind") == "Parameter" and n.get("name") == "N"]
    assert values(ast) == values(after)


@pytest.mark.eda("slang", "yosys")
def test_escaped_identifier_cannot_capture_the_generated_bound(tmp_path):
    source = UNPACKED.replace("assign q =", "wire \\__behave_rom_0_width ;\nassign q =")
    cst, ast = _typed(tmp_path, source)
    with pytest.raises(UnsupportedConstantArray, match="namespace"):
        normalize_constant_arrays(source, cst, ast)


@pytest.mark.eda("slang", "yosys")
def test_whole_array_is_not_silently_normalized(tmp_path):
    source = UNPACKED.replace("assign q =", "localparam logic [2:0] OTHER[0:5] = ROM;\nassign q =")
    cst, ast = _typed(tmp_path, source)
    with pytest.raises(UnsupportedConstantArray):
        normalize_constant_arrays(source, cst, ast)


@pytest.mark.eda("slang", "yosys")
@pytest.mark.parametrize("index", ["i++", "++i", "i--", "--i", "(i=1)"])
def test_effectful_indexes_are_not_silently_normalized(tmp_path, index):
    source = ("module M(output logic [2:0] q); int i;\n"
              "localparam logic [2:0] ROM [0:5] = '{0,5,2,7,1,3};\n"
              f"initial begin i=0; q=ROM[{index}]; end\nendmodule\n")
    cst, ast = _typed(tmp_path, source)
    with pytest.raises(UnsupportedConstantArray, match="effect"):
        normalize_constant_arrays(source, cst, ast)


@pytest.mark.eda("slang", "yosys")
@pytest.mark.parametrize("left,right", [(0, 5), (5, 0), (2, 7), (7, 2), (-3, 2), (2, -3), ("32'hffffffff", 4)])
@pytest.mark.parametrize("width,signed", [(1, False), (3, True), (9, False)])
def test_typed_constant_read_equivalence_with_explicit_invalid_x_policy(tmp_path, left, right, width, signed):
    from behave.sim.rtl.constant_arrays import _nodes

    # Constants evaluated by the same independent SV compiler establish the exact
    # four-state values and the containing expression's sign extension. This is
    # not a Verilator two-state test that silently turns X into zero.
    probes = list(map(str, range(-5, 10))) + ["32'hffffffff", "3'bx", "3'bz"]
    lo, hi = (-1 if left == "32'hffffffff" else left), right
    lo, hi = min(lo, hi), max(lo, hi)
    expressions = []
    for i, address in enumerate(probes):
        converted = f"$signed(32'({address}))"
        guard = f"!$isunknown({converted}) && {converted} >= {lo} && {converted} <= {hi}"
        expressions.append(f"localparam signed [31:0] check_{i} = ({guard}) ? ROM[{address}] : 32'bx;")
    source = (f"module M; localparam logic {'signed ' if signed else ''}[{width-1}:0] "
              f"ROM[{left}:{right}] = '{{0,1,2,3,-2,-1}};\n" + "\n".join(expressions) + "\nendmodule\n")
    cst, before = _typed(tmp_path / "before", source, allow_oob=True)
    normalized = normalize_constant_arrays(source, cst, before)
    _, after = _typed(tmp_path / "after", normalized.text, allow_oob=True)
    values = lambda doc: {n["name"]: n["value"] for n in _nodes(doc)
                          if n.get("kind") == "Parameter" and n["name"].startswith("check_")}
    assert values(before) == values(after)
