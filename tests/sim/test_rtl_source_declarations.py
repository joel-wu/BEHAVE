"""Independent source inventory, including events erased by synthesis.

These tests establish declaration, not native or bounded support for new rows.
The supported control producer is checked against this independent inventory.
"""

import copy
import json
from dataclasses import replace

import pytest

from behave.sim.rtl.source_declarations import declare_source
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_native_helpers import SOURCES as HELPER_SOURCES
from .test_rtl_source_plan import FIXTURES, _run

pytestmark = pytest.mark.eda("slang")

DESIGN = """module Inner #(parameter N=2)(input logic clk, input logic [3:0] a, output logic q);
  function automatic logic f(input logic x);
    f = x ? a[0] : a[1];
  endfunction
  function automatic logic unused(input logic x);
    unused = x && a[2];
  endfunction
  always @(posedge clk) begin
    for (int i=0; i<N; i++)
      if (a[i] && (a[2] || a[3])) q <= f(a[i]);
    if (1'b0) q <= a[0] ? a[1] : a[2];
    q <= f(a[3]);
  end
endmodule
module M(input logic clk, input logic [3:0] a, output wire [1:0] q);
  for(genvar g=0;g<2;g++) begin: lanes
    Inner #(g) inst(clk,a,q[g]);
  end
endmodule
"""


def _documents(directory, source):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "design.sv").write_text(source)
    _run(directory, "slang", "--top", "M", "--cst-json", "source.cst.json",
         "--ast-json", "source.ast.json", "--ast-json-source-info",
         "--ast-json-detailed-types", "design.sv")
    return (json.loads((directory / "source.cst.json").read_text()),
            json.loads((directory / "source.ast.json").read_text()))


def _text(source, site):
    return source.encode()[site.start:site.end].decode()


def _control_events(plan):
    """Compare pre-existing controls independently of added entry callbacks."""
    events = tuple(event for event in plan.events if event.role != "location")
    ordinals = {event.ordinal: index for index, event in enumerate(events)}
    return tuple(replace(event, ordinal=ordinals[event.ordinal],
                         ancestors=tuple(ordinals[parent] for parent in event.ancestors))
                 for event in events)


def test_source_declaration_keeps_dead_arms_zero_loops_and_call_contexts(tmp_path):
    _, ast = _documents(tmp_path, DESIGN)
    plan = declare_source(DESIGN, ast, filename="design.sv")
    choices = plan.sites_for("control-choice")
    assert len(choices) == 4
    assert {_text(DESIGN, site) for site in choices} == {
        "x ? a[0] : a[1]",
        "if (a[i] && (a[2] || a[3])) q <= f(a[i]);",
        "if (1'b0) q <= a[0] ? a[1] : a[2];",
        "a[0] ? a[1] : a[2]",
    }
    assert all(site.outcomes == ("true", "false") for site in choices)
    loop, = plan.sites_for("loop-activation")
    assert _text(DESIGN, loop).startswith("for (int i=0; i<N; i++)")
    assert loop.outcomes == ("zero", "nonzero")
    terms = plan.sites_for("boolean-expression")
    assert {_text(DESIGN, site) for site in terms} == {"a[i]", "a[2]", "a[3]"}
    assert len({site.evaluation for site in terms}) == 1
    assert all(site.outcomes == ("false", "true") for site in terms)
    # Neither the uncalled helper nor the generate-for becomes a runtime goal.
    assert len([site for site in plan.sites if site.schema != "location"]) == 8
    assert len([item for item in plan.occurrences
                if plan.sites[item.site].schema != "location"]) == 18
    assert {_text(DESIGN, site) for site in plan.sites_for("location")} == {
        "f = x ? a[0] : a[1];", "i",
        "for (int i=0; i<N; i++)\n      if (a[i] && (a[2] || a[3])) q <= f(a[i]);",
        "if (a[i] && (a[2] || a[3])) q <= f(a[i]);", "q <= f(a[i]);",
        "if (1'b0) q <= a[0] ? a[1] : a[2];", "q <= a[0] ? a[1] : a[2];",
        "q <= f(a[3]);",
    }
    helper_index = next(i for i, site in enumerate(plan.sites) if _text(DESIGN, site).startswith("x ?"))
    helper_occurrences = [item for item in plan.occurrences if item.site == helper_index]
    assert len(helper_occurrences) == 4  # two source calls in each of two instances
    assert len({item.context for item in helper_occurrences}) == 2
    assert len({item.calls for item in helper_occurrences}) == 2
    assert all(len(item.calls) == 1 for item in helper_occurrences)


@pytest.mark.parametrize("body, locations", [
    ("always_comb q=a+1;", {"q=a+1;"}),
    ("initial if(a) q=1; else q=0;", set()),
    ("function automatic logic unused(input logic x); unused=x?1:0; endfunction always_comb q=a;", {"q=a;"}),
    ("if(0) begin: absent always_comb if(a) q=1; else q=0; end else begin: used always_comb q=a; end", {"q=a;"}),
])
def test_locations_do_not_introduce_uncalled_or_construction_events(tmp_path, body, locations):
    source = "module M(input logic a, output logic q); " + body + " endmodule"
    _, ast = _documents(tmp_path, source)
    plan = declare_source(source, ast, filename="design.sv")
    assert all(site.schema == "location" for site in plan.sites)
    assert {_text(source, site) for site in plan.sites} == locations
    assert len(plan.occurrences) == len(locations)


@pytest.mark.parametrize("body, expected", [
    ("repeat(0) q=a;", ("zero", "nonzero")),
    ("while(0) q=a;", ("zero", "nonzero")),
    ("do q=a; while(0);", ("zero", "nonzero")),
    ("for(int i=0;i<0;i++) q=a;", ("zero", "nonzero")),
    ("case(a) 0: q=0; 1,2: q=1; endcase", ("arm_0", "arm_1", "no-match")),
    ("case(a) default: q=0; 1: q=1; endcase", ("arm_0", "default")),
])
def test_runtime_instance_rule_does_not_apply_a_static_reachability_filter(tmp_path, body, expected):
    source = f"module M(input clk, input [1:0] a, output reg q); always @(posedge clk) {body} endmodule"
    _, ast = _documents(tmp_path, source)
    plan = declare_source(source, ast, filename="design.sv")
    site, = (site for site in plan.sites if site.schema != "location")
    assert site.outcomes == expected
    assert sum(plan.sites[item.site] == site for item in plan.occurrences) == 1


def test_continuous_expression_is_runtime_but_generate_predicate_is_not(tmp_path):
    source = """module M(input logic a,b,c, output wire q);
      if (1) begin: enabled assign q=a ? (b && c) : 0; end
    endmodule"""
    _, ast = _documents(tmp_path, source)
    plan = declare_source(source, ast, filename="design.sv")
    assert len(plan.sites_for("control-choice")) == 1
    assert len(plan.sites_for("boolean-expression")) == 2


def test_part_select_elaboration_operands_do_not_declare_runtime_events(tmp_path):
    source = """module M #(parameter W=8)(input [7:0] a, output reg [3:0] q);
      function automatic [3:0] take(input [7:0] x);
        take = x[(W < 4 ? W : 4)-1:0];
      endfunction
      always_comb begin
        q = take(a);
        q = a[(a[0] ? 3 : 2) +: (W >= 4 ? 2 : 1)];
        if (1) q = q;
      end endmodule"""
    cst, ast = _documents(tmp_path, source)
    declarations = declare_source(source, ast, filename="design.sv")
    # Exclusion is by static operand role, not by predicate value. Keep the
    # dynamic selector and even the literal-true runtime if statement.
    assert {_text(source, site) for site in declarations.sites_for("control-choice")} == {
        "a[0] ? 3 : 2", "if (1) q = q;"}
    plan = plan_source(source, cst, semantic_document=ast)
    native = plan.native().text
    assert "x[(W < 4 ? W : 4)-1:0]" in native
    assert "(W >= 4 ? 2 : 1)" in native


@pytest.mark.parametrize("statement", ["$error(\"bad\");", "$fatal(1,\"bad\");", "assert(a);"])
def test_designated_runtime_failures_are_declared_without_executing_them(tmp_path, statement):
    source = f"module M(input clk,a); always @(posedge clk) {statement} endmodule"
    _, ast = _documents(tmp_path, source)
    plan = declare_source(source, ast, filename="design.sv")
    failure, = plan.sites_for("artifact-failure")
    assert failure.outcomes == ("failure",)


def test_source_identity_does_not_retain_frontend_addresses(tmp_path):
    _, ast = _documents(tmp_path, DESIGN)
    expected = declare_source(DESIGN, ast, filename="design.sv")
    changed = copy.deepcopy(ast)

    def renumber(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "addr":
                    value[key] = item + 1000000
                elif isinstance(item, str) and " " in item and item.split(" ", 1)[0].isdigit():
                    number, suffix = item.split(" ", 1)
                    value[key] = f"{int(number) + 1000000} {suffix}"
                else:
                    renumber(item)
        elif isinstance(value, list):
            for item in value:
                renumber(item)

    renumber(changed)
    assert declare_source(DESIGN, changed, filename="design.sv") == expected
    ast.clear()
    assert len([site for site in expected.sites if site.schema != "location"]) == 8


@pytest.mark.parametrize("name", ["inline", "constant-if", "priority-case", "unrolled", "always-ff"])
def test_existing_native_controls_consume_the_typed_source_plan(tmp_path, name):
    source = FIXTURES[name]
    cst, ast = _documents(tmp_path, source)
    old = plan_source(source, cst)
    plan = plan_source(source, cst, semantic_document=ast)
    assert plan.declarations is not None
    assert plan.native()
    assert plan.compiler.text == old.compiler.text
    assert _control_events(plan) == old.events


@pytest.mark.parametrize("name", HELPER_SOURCES)
def test_typed_call_selection_preserves_supported_native_helper_contexts(tmp_path, name):
    source = HELPER_SOURCES[name]
    cst, ast = _documents(tmp_path, source)
    old = plan_source(source, cst)
    plan = plan_source(source, cst, semantic_document=ast)
    assert plan.native()
    assert plan.calls == old.calls
    assert _control_events(plan) == old.events
    assert plan.visits == old.visits


@pytest.mark.parametrize("value", ["$bits(f(a))", "$typename(f(a)) == \"logic\""])
def test_unevaluated_type_queries_do_not_call_helpers(tmp_path, value):
    source = f"""module M(input logic a, output logic q);
      function automatic logic f(input logic x); f=x ? 1'b1 : 1'b0; endfunction
      always_comb q={value};
    endmodule"""
    _, ast = _documents(tmp_path, source)
    plan = declare_source(source, ast, filename="design.sv")
    assert len(plan.sites) == 1
    assert plan.sites[0].schema == "location"
    assert _text(source, plan.sites[0]) == f"q={value};"
    assert all(source[start:end].startswith("$") for start, end in plan.call_spans)


def test_native_producer_also_skips_unevaluated_helper_calls(tmp_path):
    source = """module M(input clk,a, output reg q);
      function automatic logic f(input logic x);
        if(x) f=1; else f=0;
      endfunction
      always @(posedge clk) q <= $bits(f(a));
    endmodule"""
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    location, = plan.declarations.sites
    assert location.schema == "location"
    assert _text(source, location) == "q <= $bits(f(a));"
    assert not plan.calls
    assert [event.role for event in plan.events] == ["process", "location"]
    assert plan.native()


@pytest.mark.eda("yosys")
def test_shared_compiler_reuses_parsed_documents(tmp_path):
    from pathlib import Path

    from behave.sim.rtl.elaboration import elaboration_script
    from behave.sim.rtl.shared_frontend import compile_source, parse_source

    source = FIXTURES["inline"]
    (tmp_path / "design.sv").write_text(source)
    (tmp_path / "run.ys").write_text(elaboration_script("M") + "write_rtlil design.rtlil\n")
    commands = []

    def run(directory, *argv):
        command = (Path(argv[0]).name, *argv[1:])
        commands.append(command)
        _run(directory, *command)
        return True

    parsed = parse_source(tmp_path, "M", run=run, typed=True)
    assert parsed is not None
    plan = plan_source(source, parsed[0], semantic_document=parsed[1])
    assert len(plan.declarations.sites_for("control-choice")) == 1
    assert len(plan.declarations.sites_for("location")) == 3
    assert compile_source(tmp_path, "M", "run.ys", run=run, parsed=parsed)
    assert [command[0] for command in commands] == ["slang", "yosys"]
    assert (tmp_path / "design.sv").read_text() == source


def test_native_source_plan_uses_the_selected_top_and_helper_closure(tmp_path):
    source = """module Unused(input clk,a, output reg q);
      always @(posedge clk) if(a) q<=1; else q<=0;
    endmodule
    module M(input clk,a, output reg q);
      function automatic logic unused(input logic x);
        if(x) unused=1; else unused=0;
      endfunction
      if(0) begin: absent
        always @(posedge clk) if(a) q<=0; else q<=1;
      end
      always @(posedge clk) if(a) q<=1; else q<=0;
    endmodule"""
    cst, ast = _documents(tmp_path, source)
    with pytest.raises(ValueError, match="outside an observed event body"):
        plan_source(source, cst).native()
    plan = plan_source(source, cst, semantic_document=ast)
    assert len(plan.declarations.sites_for("control-choice")) == 1
    assert len(plan.declarations.sites_for("location")) == 3
    assert len(plan.declarations.process_starts) == 1
    assert [event.role for event in plan.events] == [
        "process", "location", "if-true", "location", "if-false", "location"]
    assert not plan.native_issues
    assert plan.native()


@pytest.mark.parametrize("query", ["$size", "$left"])
def test_array_query_still_evaluates_its_dimension_argument(tmp_path, query):
    source = f"""module M(input logic a, output integer q);
      function automatic logic [3:0] unevaluated(input logic x);
        unevaluated=x ? 4'd1 : 4'd0;
      endfunction
      function automatic integer dimension(input logic x);
        dimension=x ? 1 : 2;
      endfunction
      always_comb q={query}(unevaluated(a),dimension(a));
    endmodule"""
    _, ast = _documents(tmp_path, source)
    plan = declare_source(source, ast, filename="design.sv")
    site, = plan.sites_for("control-choice")
    assert _text(source, site) == "x ? 1 : 2"
    assert {_text(source, site) for site in plan.sites_for("location")} == {
        "dimension=x ? 1 : 2;", f"q={query}(unevaluated(a),dimension(a));"}


@pytest.mark.isolation
@pytest.mark.eda("yosys")
def test_public_preparation_keeps_only_selected_source_producers(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.native_preparation import PreparedNativeEvents, prepare_source_observer

    from .test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    source = """module Unused(input clk,a,output reg q);
      always @(posedge clk) if(a) q<=1; else q<=0;
    endmodule
    module M(input clk,a,output reg q);
      function automatic logic never_called(input logic x);
        if(x) never_called=1; else never_called=0;
      endfunction
      always @(posedge clk) if(a) q<=1; else q<=0;
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    catalogue = RTLArtifact(tmp_path / "design.sv", "M", tmp_path / "build").frontend_catalogue()
    result = prepare_source_observer(source, top="M", execution_sha256="1" * 64,
                                     catalogue=catalogue, callback_limit=100)
    assert type(result) is PreparedNativeEvents, result
    assert result.plan.bindings.event_count == 6
    assert len(catalogue) == 5  # Three statement entries and both conditional outcomes.
    result.plan.check_catalogue(catalogue)
