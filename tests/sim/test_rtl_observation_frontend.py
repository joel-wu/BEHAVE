"""The installed source-analysis client feeds the existing observation plan."""

import json
import subprocess
from pathlib import Path

import pytest

from behave.core.paths import eda_root
from behave.sim.rtl.native_dependencies import NATIVE_FRONTEND_EXECUTABLE
from behave.sim.rtl.shared_frontend import SLANG_FLAGS, parse_observation_source, parse_source
from behave.sim.rtl.source_plan import plan_source


def test_observation_documents_are_requested_together(tmp_path):
    calls = []
    documents = {"frontend.cst.json": {"syntaxTrees": []},
                 "frontend.ast.json": {"design": {}},
                 "frontend.sensitivity.json": []}

    def run(where, *args):
        calls.append(args)
        for name, value in documents.items():
            (where / name).write_text(json.dumps(value))
        return True

    parsed, reads = parse_observation_source(tmp_path, "M", run=run)
    assert parsed == (documents["frontend.cst.json"], documents["frontend.ast.json"])
    assert reads == []
    assert len(calls) == 1
    assert calls[0][0] == "/tools/behave-rtl-frontend"
    assert all(flag in calls[0] for flag in SLANG_FLAGS)
    assert all(name in calls[0] for name in documents)
    assert calls[0].count("--threads") == 1
    assert calls[0][calls[0].index("--threads") + 1] == "1"


@pytest.mark.parametrize("typed", [False, True])
def test_catalogue_parser_obeys_the_same_single_cpu_permit(tmp_path, typed):
    calls = []
    documents = {"frontend.cst.json": {"syntaxTrees": []}, "frontend.ast.json": {"design": {}}}

    def run(where, *args):
        calls.append(args)
        for name, value in documents.items():
            (where / name).write_text(json.dumps(value))
        return True

    cst, ast = parse_source(tmp_path, "M", run=run, typed=typed)
    assert cst == documents["frontend.cst.json"]
    assert ast == (documents["frontend.ast.json"] if typed else None)
    assert len(calls) == 1
    assert calls[0][0] == "/tools/slang"
    assert calls[0].count("--threads") == 1
    assert calls[0][calls[0].index("--threads") + 1] == "1"


def test_failed_observation_parse_has_no_alternate_parser(tmp_path):
    calls = []

    def fail(where, *args):
        calls.append(args)
        return False

    assert parse_observation_source(tmp_path, "M", run=fail) is None
    assert len(calls) == 1


def _installed(where, executable, *args):
    assert Path(executable).name == "behave-rtl-frontend"
    return subprocess.run(
        (str(eda_root() / "libexec" / NATIVE_FRONTEND_EXECUTABLE), *args),
        cwd=where, check=False).returncode == 0


@pytest.mark.eda("slang")
@pytest.mark.parametrize(("body", "expected_names"), [
    ("q=a-a;", {"a"}),
    ("q=a^a;", {"a"}),
    ("if (1'b0) q=a; else q=7;", set()),
    ("q=a[0];", {"a"}),
])
def test_compiler_metadata_reaches_the_same_source_plan(tmp_path, body, expected_names):
    from behave.sim.rtl.source_declarations import _nodes

    source = "module M(input [3:0] a,input noise,output reg [3:0] q); always_comb " + body + " endmodule"
    (tmp_path / "design.sv").write_text(source)
    result = parse_observation_source(tmp_path, "M", run=_installed)
    assert result is not None
    (cst, ast), sensitivity = result
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
    original = plan_source(source, cst, semantic_document=ast)
    assert plan.events == original.events and plan.compiler == original.compiler
    assert plan.declarations == original.declarations
    assert plan.event_waits and "initial" in plan.native().text
    assert not plan.native_issues
    assert {read["symbol"].split(".")[-1] for row in sensitivity for read in row["reads"]} == expected_names
    symbols = {str(node["addr"]): node for node in _nodes(ast) if "addr" in node}
    for row in sensitivity:
        for read in row["reads"]:
            assert symbols[str(read["symbol_id"])]["name"] == "a"
            assert read["scope_id"] == row["scope_id"]
            assert all("__behave_source_write_base" in term["expression"]
                       and term["unpacked_dimensions"] == 0 for term in read["projections"])
    if "a[0]" in body:
        read, = sensitivity[0]["reads"]
        assert (read["low"], read["high"]) == (0, 0)
    assert (tmp_path / "design.sv").read_text() == source


@pytest.mark.eda("slang")
def test_installed_analysis_reports_source_errors(tmp_path, capfd):
    (tmp_path / "design.sv").write_text(
        "module M(input a,b,output logic q); always_comb q=a; always_comb q=b; endmodule")
    assert parse_observation_source(tmp_path, "M", run=_installed) is None
    assert "multiple-always-assigns" in capfd.readouterr().err


@pytest.mark.eda("slang")
@pytest.mark.parametrize(("access", "rank"), [
    ("x[a[0]][4+a[1]][a[2]]", 3),
    ("x[1][4+a[1]][a[2]]", 2),
    ("x[1][4][a[2]]", 1),
    ("x[1][4][0]", 0),
])
def test_compiler_projection_shape_is_for_the_selected_range(tmp_path, access, rank):
    source = f"""module M(input [2:0] a,output logic [7:0] q);
      typedef logic [7:0] cube_t [1:0][4:5][0:1];
      cube_t x;
      always_comb q={access};
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    result = parse_observation_source(tmp_path, "M", run=_installed)
    assert result is not None
    _, sensitivity = result
    read, = (read for row in sensitivity for read in row["reads"] if read["symbol"] == "M.x")
    projection, = read["projections"]
    assert projection["unpacked_dimensions"] == rank
    assert "templates" not in read


@pytest.mark.eda("slang")
def test_compiler_exports_resolved_integer_types_without_changing_goal_identity(tmp_path):
    from copy import deepcopy

    from behave.sim.rtl.source_declarations import declare_source
    from behave.sim.rtl.source_goals import manifest_of
    from behave.sim.rtl.source_integral import integral_type, typed_symbols

    source = """module M(input clk,input int a,input int unsigned b,
      input longint unsigned c,output logic q);
      always @(posedge clk) q=(a<0)||(b<0)||(c<0);
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    manifests = []
    for _ in range(2):
        parsed = parse_observation_source(tmp_path, "M", run=_installed)
        assert parsed is not None
        (_, ast), _ = parsed
        original = deepcopy(ast)
        symbols = typed_symbols(ast)
        values = {node["name"]: integral_type(node["type"], symbols)
                  for node in symbols.values() if node["kind"] == "Net"}
        assert (values["a"].width, values["a"].signed) == (32, True)
        assert (values["b"].width, values["b"].signed) == (32, False)
        assert (values["c"].width, values["c"].signed) == (64, False)
        assert ast == original  # Type enrichment must not change the input AST.
        declared = declare_source(source, ast, filename="design.sv")
        manifests.append(manifest_of(declared, filename="design.sv").as_dict())
    assert manifests[0] == manifests[1]
