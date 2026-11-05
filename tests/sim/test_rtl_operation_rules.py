"""Operation rules compose with source observation; original RTL is the oracle."""

from behave.sim.core.source_rules import SourceGoalRules
import json
import subprocess
from collections import Counter

import pytest

from behave.sim.core.observation_goal import GoalCondition, GoalField
from behave.sim.core.operation_goal import OperationGoal, OperationRule
from behave.sim.rtl.native_events import VERILATOR_FLAGS, prepare_declared_native_events
from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_edits import Insertion, Replacement, SourceEdits, relocate_expression
from behave.sim.rtl.source_goals import goal_of, manifest_of
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_observation_frontend import _installed
from .test_rtl_source_plan import _run


def _rule(operator="add", target=18, kinds=()):
    return OperationRule(operator, OperationGoal("target", GoalCondition("eq", GoalField("result"), target)), kinds)


def _plans(where, source, rules):
    where.mkdir()
    (where / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(where, "M", run=_installed)
    options = {"semantic_document": ast, "sensitivity_document": sensitivity, "enumerate_iterations": False}
    return plan_source(source, cst, **options), plan_source(source, cst, source_rules=SourceGoalRules(operations=rules), **options)


def test_relocation_retains_nested_insertions_and_token_replacement():
    source = "q = a + b;"
    edits = (Insertion(4, "capture("), Insertion(4, "read("), Insertion(5, ")"), Insertion(9, ")"))
    value, remaining, replacements = relocate_expression(source.encode(), (4, 9), edits, (Replacement(6, 7, ","),))
    assert value == "capture(read(a) , b)" and not remaining and not replacements
    result = SourceEdits(source, (Insertion(0, f"tmp={value}; "),), (Replacement(4, 9, "tmp"),))
    assert result.text == "tmp=capture(read(a) , b); q = tmp;"


_SOURCE = """module M(input clk, en, input logic [3:0] a,b,
                    output logic [15:0] q, r, output wire [15:0] c);
  function automatic [15:0] helper(input logic [3:0] x);
    if (x[0]) return x+1; else return x+2;
  endfunction
  initial r=helper(7);
  always @(posedge clk) if (en) begin
    q <= (a*b)+a;
    r <= (a && (b+1)) ? helper(a)+helper(b) : a+3;
  end
  assign c = (a ? a*b : b+1)+a;
endmodule
"""


def _run_values(where, source):
    where.mkdir()
    (where / "design.sv").write_text(source)
    (where / "probe.cpp").write_text('''#include "VM.h"
#include "verilated.h"
#include <iostream>
extern "C" void __behave_native_event_hit(unsigned id) {
  std::cout << "hit " << id << "\\n";
}
int main(int argc,char** argv) {
  Verilated::commandArgs(argc,argv); VM dut;
  for(unsigned a=0;a<16;++a) for(unsigned b=0;b<16;++b)
    for (unsigned enabled: {1u,0u}) {
      std::cout << "input " << a << " " << b << " " << enabled << "\\n";
      dut.clk=0; dut.en=enabled; dut.a=a; dut.b=b;
      dut.eval(); dut.clk=1; dut.eval();
      std::cout << "output " << unsigned(dut.q) << " " << unsigned(dut.r)
                << " " << unsigned(dut.c) << "\\n";
    }
  dut.final(); return 0;
}
''')
    _run(where, "verilator", "--cc", "--exe", "--build", "-j", "1", "-Wno-fatal",
         *VERILATOR_FLAGS, "--top-module", "M", "design.sv", "probe.cpp", timeout_s=120)
    output = subprocess.run([str(where / "obj_dir/VM")], check=True, capture_output=True,
                            text=True, timeout=15).stdout
    values, hits, current = [], [], None
    for line in output.splitlines():
        label, *fields = line.split()
        row = tuple(map(int, fields))
        if label == "input":
            current = row
        elif label == "output":
            values.append((current, row))
        else:
            assert label == "hit" and row[0] != 2**32-1
            hits.append((current, row[0]))
    return values, hits


def _named_hits(plan, hits):
    declarations = plan.declarations
    goals = {producer.event: goal_of(declarations.sites[producer.site], producer.outcome,
                                     filename="design.sv") for producer in plan.declared_events}
    return [(inputs, goals[event.source_event]) for inputs, index in hits
            if (event := plan.invocations[index]).source_event in goals]


@pytest.mark.eda("slang", "verilator", "make")
def test_nested_operations_keep_outputs_structural_hits_and_helper_occurrences(tmp_path):
    completed = OperationRule("add", OperationGoal("completed", GoalCondition("eq", 0, 0)))
    plain, observed = _plans(tmp_path / "parse", _SOURCE, (_rule(), completed))
    collector = prepare_declared_native_events(observed, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=None)
    assert not any(row.issue for row in collector.bindings.projections)
    manifest = manifest_of(observed.declarations, filename="design.sv")
    assert any(goal.family == "operation-event" for goal in manifest.values())
    unmodified, _ = _run_values(tmp_path / "unmodified", _SOURCE)
    original, original_hits = _run_values(tmp_path / "structural", plain.native().text)
    values, hits = _run_values(tmp_path / "operation", observed.native().text)
    assert values == original == unmodified
    names = _named_hits(observed, hits)
    assert Counter((inputs, goal.id) for inputs, goal in names if goal.family != "operation-event") == Counter(
        (inputs, goal.id) for inputs, goal in _named_hits(plain, original_hits))
    target = next(goal.id for goal in manifest.values() if goal.family == "operation-event" and goal.event == "target"
                  and _SOURCE[dict(goal.provenance)["start"]:dict(goal.provenance)["end"]] == "(a*b)+a")
    assert {(a,b,en) for (a,b,en), goal in names if goal.id == target} == {
        (a,b,1) for a in range(16) for b in range(16) if a*b+a == 18}
    assert all(en for (a,b,en), goal in names if goal.family == "operation-event"
               and dict(goal.provenance)["start"] < _SOURCE.index("assign c"))
    def at(goal, text):
        position = dict(goal.provenance)
        return (goal.family == "operation-event" and goal.event == "completed"
                and _SOURCE[position["start"]:position["end"]] == text
                and position["start"] < _SOURCE.index("assign c"))
    # A completion rule is deliberately always true: an incorrectly executed
    # lazy operand or disabled construction helper cannot hide behind a miss.
    assert Counter(inputs for inputs, goal in names if at(goal, "b+1")) == Counter(
        (a,b,1) for a in range(1,16) for b in range(16))
    assert Counter(inputs for inputs, goal in names if at(goal, "x+1")) == Counter({
        (a,b,1): (a&1)+(b&1) for a in range(1,16) for b in range(16) if (a&1)+(b&1)})


@pytest.mark.eda("slang")
def test_operation_rules_keep_dead_sites_but_exclude_elaboration_and_construction(tmp_path):
    source = """module M #(parameter W=8)(input clk, input [7:0] a, output logic [7:0] q);
      function automatic [7:0] unused(input [7:0] x); return x+99; endfunction
      initial q=1+2;
      always @(posedge clk) begin
        if(0) q=a+17;
        else q=a[W-1:0]+$bits(a+3);
      end
    endmodule"""
    _, plan = _plans(tmp_path / "parse", source, (_rule(),))
    assert {source[site.start:site.end] for site in plan.declarations.sites_for("operation-event")} == {
        "a+17", "a[W-1:0]+$bits(a+3)"}
    assert "a+3" in plan.native().text  # type-only query must retain its original expression


@pytest.mark.eda("slang")
def test_different_elaborated_widths_do_not_reuse_the_first_instances_capture(tmp_path):
    source = """module Inner #(parameter W=4)(input [W-1:0] a, output reg [W-1:0] q);
      always_comb q=a+W'(1);
    endmodule
    module M(input [7:0] a, output [7:0] q, output [3:0] r);
      Inner #(.W(8)) wide(a,q);
      Inner #(.W(4)) narrow(a[3:0],r);
    endmodule"""
    _, plan = _plans(tmp_path / "parse", source, (_rule(),))
    from behave.sim.rtl.source_declarations import declare_source

    ast = json.loads((tmp_path / "parse" / "frontend.ast.json").read_text())
    declared = declare_source(source, ast, filename="design.sv", source_rules=SourceGoalRules(operations=(_rule(),)))
    assert plan.declarations == declared
    assert len(declared.sites_for("operation-event")) == 1
    assert "$bits(\\W " in plan.native().text


@pytest.mark.eda("slang")
def test_capture_does_not_choose_an_order_for_effectful_operands(tmp_path):
    source = """module M(input clk, output logic [7:0] q);
      logic [7:0] counter=0;
      always @(posedge clk) q=(counter++)+counter;
    endmodule"""
    _, plan = _plans(tmp_path / "parse", source, (_rule(),))
    assert plan.declarations.sites_for("operation-event")
    with pytest.raises(ValueError, match="conflicting source storage effects"):
        plan.native()


@pytest.mark.eda("slang")
@pytest.mark.solver
@pytest.mark.parametrize("expression,rule", [
    ("(a*b)+a", _rule()),
    ("!a", _rule("logical-not", True)),
    ("a+b", _rule(kinds=("boolean", "integer", "integer"))),
])
def test_source_activation_query_uses_the_same_operation_predicate(tmp_path, expression, rule):
    from behave.sim.rtl.source_integral import typed_symbols
    from behave.sim.rtl.source_ir import lower_source_process

    from .test_rtl_source_ir import _encode, _run_activation

    source = f"module M(input [3:0] a,b, output logic [15:0] q); always_comb q={expression}; endmodule"
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity,
                       source_rules=SourceGoalRules(operations=(rule,)), enumerate_iterations=False)
    context, = plan.declarations.contexts
    lowered = lower_source_process(source, ast, plan, context, filename="design.sv")
    symbols = typed_symbols(ast)
    names = {symbols[identity]["name"]: name for name, identity in lowered.storage}
    encoded = _encode(lowered)
    target, = [mark for mark, _, goal in lowered.occurrences if
               manifest_of(plan.declarations, filename="design.sv")[goal].family == "operation-event"]
    import z3
    for a in range(16):
        for b in range(16):
            _run_activation(lowered, names, {"a": a, "b": b, "q": 0}, encoded)
            solver = z3.Solver()
            solver.add(encoded.constraint, *(encoded.pre[names[name]] == value
                for name, value in (("a", a), ("b", b)) if name in names))
            assert solver.check() == z3.sat
            expected = (a*b+a == 18 if expression == "(a*b)+a" else
                        a == 0 if expression == "!a" else False)
            assert z3.is_true(solver.model().eval(encoded.hits[target])) is expected


def _public_operation_case(tmp_path, wrong, topology, *, debug=True):
    from behave.sim import api

    parameterized = topology not in {"flat", "effectful", "effectful-pair", "effectful-guarded"}
    integer = api.FiniteType(8)
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", integer, "a")], api.settled("in"),
                     None, kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(16 if parameterized else 8), "y")], api.settled("out"),
                     None, kind=api.Kind.TRANSACTION, answers_per_transaction=1)], [],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))],
        operation_rules=(_rule(target=174),))
    expected = "(((a+1)&255)<<8) | ((a+1)&15)" if parameterized else "(a+1)&255"
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": ('class M:\n def reset(self): pass\n def process(self,a): return {"y":'
                         + expected + '}\n').encode()}, config={"class": "M"})
    if topology in {"effectful", "effectful-pair", "effectful-guarded"}:
        increment = f"8'd{1+wrong}"
        if topology == "effectful-pair":
            increment = "increment()"
        expression = f"value(a)+{increment}"
        if topology == "effectful-guarded":
            expression = f"a ? ({expression}) : {increment}"
        source = f'''module M(input [7:0] a, output logic [7:0] y);
          logic [7:0] calls=0, other_calls=0;
          function automatic [7:0] value(input [7:0] v);
            calls++; return v;
          endfunction
          function automatic [7:0] increment();
            other_calls++; return 8'd{1+wrong};
          endfunction
          always_comb y={expression};
        endmodule'''
    elif topology == "hierarchical":
        # Slice connections now have a port relation; a resized connection still has none.
        source = f'''module Cell #(parameter W=4)(input [W-1:0] a, output logic [W-1:0] y);
          always_comb y=a+W'({1+wrong});
        endmodule
        module M(input [7:0] a, output [15:0] y);
          Cell #(.W(4)) narrow(a,y[3:0]);
          Cell #(.W(8)) wide(a,y[15:8]);
          assign y[7:4]=0;
        endmodule'''
    elif topology in {"generated", "generated-blocking", "generated-conditional"}:
        driver = "assign" if topology == "generated" else "always_comb"
        readers = ""
        output = "{lanes[8].value,4'b0,lanes[4].value}"
        if topology == "generated-conditional":
            readers = '''if(k==8) begin: wide
                logic[k-1:0] result; always_comb result=value;
              end else begin: narrow
                logic[k-1:0] result; always_comb result=value;
              end'''
            output = "{lanes[8].wide.result,4'b0,lanes[4].narrow.result}"
        source = f'''module M(input [7:0] a, output [15:0] y);
          for(genvar k=4;k<=8;k+=4) begin: lanes
            logic [k-1:0] value;
            {driver} value=a[k-1:0]+k'({1+wrong});
            {readers}
          end
          assign y={output};
        endmodule'''
    else:
        source = f"module M(input [7:0] a,output logic [7:0] y); always_comb y=a+8'd{1+wrong}; endmodule"
    path = tmp_path / "design.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    target, = [goal.id for goal in handle.frontend_catalogue.values() if goal.family == "operation-event"]
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=3))
    assert all(seed.depth("in") == 100 for seed in baseline.stimuli)
    request = handle.command_request(run_directory=tmp_path / "command", baseline=baseline, reference=reference,
        providers=handle.generation_providers(reference, workdir=tmp_path / "search")[1:],
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=debug), run_analysis=False)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    return result, handle, baseline, target


@pytest.mark.cosim
@pytest.mark.parametrize("wrong", [False, True])
@pytest.mark.parametrize("topology", ["flat", "generated", "generated-blocking", "generated-conditional",
                                     "effectful", "effectful-pair", "effectful-guarded"])
def test_public_rtl_rules_baseline_residual_query_and_native_replay(tmp_path, wrong, topology, record_property):
    from behave.sim import api

    result, handle, baseline, target = _public_operation_case(tmp_path, wrong, topology)
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative
    assert result.decision.passed is not wrong
    if wrong:
        assert result.evaluation.mismatch_count
        assert result.evaluation.failure_witness is not None
        assert result.evaluation.unexecuted and not result.evaluation.complete
        assert result.generated.search_summary["stop_reason"] == "baseline-failed"
    else:
        assert result.evaluation.complete
        search, = result.generated.searches
        feedback = search.as_dict()["telemetry"]["baseline_coverage"]
        assert target in feedback["unhit"] and target not in feedback["hits"]
        assert search.telemetry["probes"] > 0
        reached = {goal for run in result.evaluation.results
                   for row in run.trace.records for goal, hit in row.hits if hit is True}
        assert target in reached
        frontend = set(handle.frontend_catalogue)
        baseline_inputs = {seed.identity_bytes for seed in baseline.stimuli}
        baseline_hits = {goal for run in result.evaluation.results if run.stimulus.identity_bytes in baseline_inputs
                         for row in run.trace.records for goal, hit in row.hits if hit is True} & frontend
        assert baseline_hits == frontend.intersection(feedback["hits"])
        assert 0 < len(baseline_hits) < len(frontend)
        record_property("frontend_goals", len(frontend))
        record_property("baseline_traces", len(baseline.stimuli))
        record_property("baseline_frontend_hits", len(baseline_hits))
        record_property("final_frontend_hits", len(frontend.intersection(reached)))
        record_property("probes", search.telemetry["probes"])


@pytest.mark.cosim
@pytest.mark.parametrize("debug", [False, True])
def test_hierarchical_query_limitation_preserves_native_baseline_or_stops_debug(tmp_path, debug):
    from pathlib import Path

    from behave.sim import api

    result, _, baseline, target = _public_operation_case(tmp_path, False, "hierarchical", debug=debug)
    saved = json.loads(Path(result.record_path).read_text())
    assert saved["progress"]["baseline"]["complete"] is True
    limitation = "rtl-source-scheduler-port-propagation-unbound"
    if debug:
        assert saved["progress"]["baseline_evaluation"]["passed"] is True
        assert result.status is api.CommandStatus.REFUSED, result.as_dict()
        assert result.failure.kind == "generation-debug-stop"
        assert result.failure.as_dict()["detail"]["cause"]["kind"] == limitation
        assert not result.decision.authoritative and result.decision.passed is None
    else:
        assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
        assert result.evaluation.complete and result.decision.authoritative and result.decision.passed
        search, = result.generated.searches
        telemetry = search.as_dict()["telemetry"]
        assert limitation in telemetry["refusals"]
        # Schedule-only monitor questions may be locally UNSAT; the refused
        # operation query is never reported as one.
        assert not any(target in telemetry["target_sets"][answer["target_set"]]
                       for answer in telemetry["answers"] if answer["outcome"] == "local-unsat")
        assert result.generated.stimuli == result.generated.baseline
        # Nothing new is replayed: every final run, including the baseline's own
        # continuous workload, is reused from the baseline.
        assert result.evaluation.execution_cost["reuse"]["completed_runs"] == len(result.evaluation.results)
