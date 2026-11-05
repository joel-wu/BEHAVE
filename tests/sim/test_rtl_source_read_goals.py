"""Real HDL reads, including old NBA values; no output-snapshot surrogate."""

from collections import Counter

import pytest

from behave.sim import api
from behave.sim.core.goal_provider import RTL_LOCAL_GUIDANCE_POLICY
from behave.sim.core.source_rules import SourceGoalRules
from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_observation_frontend import _installed
from .test_rtl_operation_rules import _named_hits, _run_values
from .test_source_read_goals import _read


def _plans(where, source, rules, *, operations=()):
    where.mkdir(exist_ok=True)
    (where / "design.sv").write_text(source)
    (cst, document), sensitivity = parse_observation_source(where, "M", run=_installed)
    options = {"semantic_document": document, "sensitivity_document": sensitivity, "enumerate_iterations": False}
    return (document, plan_source(source, cst, **options),
            plan_source(source, cst, source_rules=SourceGoalRules(operations, rules), **options))


@pytest.mark.eda("slang")
def test_catalogue_does_not_require_native_capture_metadata(tmp_path):
    import json
    import subprocess

    from behave.core.paths import eda_root
    from behave.sim.rtl.shared_frontend import declare_catalogue
    from behave.sim.rtl.source_declarations import declare_source

    source = "module M(input [7:0] a, output [7:0] y); assign y=a; endmodule"
    (tmp_path / "design.sv").write_text(source)
    rules = SourceGoalRules(reads=(_read("a", 173),))
    calls = []

    def run(where, executable, *args):
        calls.append(executable)
        assert executable == "/tools/slang"
        return subprocess.run((str(eda_root() / "bin/slang"), *args), cwd=where, check=False).returncode == 0

    assert declare_catalogue(tmp_path, "M", run=run, source_rules=rules)
    declared = json.loads((tmp_path / "source-goals.json").read_text())
    (_cst, ast), _ = parse_observation_source(tmp_path, "M", run=_installed)
    full = manifest_of(declare_source(source, ast, filename="design.sv", source_rules=rules), filename="design.sv")
    assert declared == full.as_dict()
    assert calls == ["/tools/slang"]  # no observer, synthesis, or fallback parse


@pytest.mark.eda("slang", "verilator", "make")
def test_native_reads_preserve_values_old_state_signedness_and_unselected_calls(tmp_path):
    source = '''module M(input clk,en,input logic [3:0] a,b,
      output logic [15:0] q=0,r=0,c=0);
      logic signed [3:0] s=0;
      function automatic [15:0] helper(input logic [3:0] x);
        if(x[0]) return x; else return 0;
      endfunction
      initial c=helper(7);
      always @(posedge clk) if(en) begin
        q<=a;
        s=b;
        r<=q;
        c<=helper(a)+s;
      end
    endmodule
'''
    rules = (_read("q", 5, "old-five"), _read("s", -1, "negative"), _read("x", 7, "helper-seven"))
    _, plain, observed = _plans(tmp_path / "parse", source, rules)
    unmodified, _ = _run_values(tmp_path / "unmodified", source)
    original, original_hits = _run_values(tmp_path / "structural", plain.native().text)
    values, hits = _run_values(tmp_path / "sampled", observed.native().text)
    assert values == original == unmodified
    named = _named_hits(observed, hits)
    assert Counter((inputs, goal.id) for inputs, goal in named if goal.family != "value-observation") == Counter(
        (inputs, goal.id) for inputs, goal in _named_hits(plain, original_hits))
    samples = [(inputs, goal) for inputs, goal in named if goal.family == "value-observation"]
    assert samples and all(enabled for (_, _, enabled), _ in samples)
    expected_old, old = set(), 0
    for inputs, _outputs in unmodified:
        a, _b, enabled = inputs
        if enabled:
            if old == 5:
                expected_old.add(inputs)
            old = a
    assert {inputs for inputs, goal in samples if goal.event == "old-five"} == expected_old
    assert {inputs for inputs, goal in samples if goal.event == "negative"} == {
        (a, 15, 1) for a in range(16)}
    assert {inputs for inputs, goal in samples if goal.event == "helper-seven"} == {
        (7, b, 1) for b in range(16)}


@pytest.mark.eda("slang", "verilator", "make")
def test_reads_compose_with_operations_without_narrowing_or_eager_sampling(tmp_path):
    from .test_rtl_operation_rules import _rule

    source = '''module M(input clk,en,input logic [3:0] a,b,
      output logic [15:0] q=0,r=0,c=0);
      always @(posedge clk) if(en) begin
        q<=a+4'd1;
        r<=a ? b : 0;
        c<=a;
      end
    endmodule
'''
    rules = (_read("a", 15, "unsigned-high"), _read("a", -1, "negative"),
             _read("a", (1 << 100) + 15, "wide-literal"),
             api.SourceReadRule("b", "evaluated", api.GoalCondition("eq", 0, 0)))
    _, plain, observed = _plans(tmp_path / "parse", source, rules, operations=(_rule(target=16),))
    expected, _ = _run_values(tmp_path / "original", source)
    values, hits = _run_values(tmp_path / "observed", observed.native().text)
    assert values == expected
    named = _named_hits(observed, hits)
    assert {inputs for inputs, goal in named if goal.event == "unsigned-high"} == {
        (15, b, 1) for b in range(16)}
    assert not any(goal.event in {"negative", "wide-literal"} for _, goal in named)
    assert {inputs for inputs, goal in named if goal.event == "evaluated"} == {
        (a, b, 1) for a in range(1, 16) for b in range(16)}
    assert {inputs for inputs, goal in named if goal.family == "operation-event"} == {
        (15, b, 1) for b in range(16)}
    assert len(observed.declarations.sites_for("value-observation")) == 4
    assert not plain.declarations.sites_for("value-observation")


@pytest.mark.eda("slang")
@pytest.mark.solver
@pytest.mark.parametrize("assignment,expected", [("q<=a; r<=q;", 5), ("q=a; r<=q;", 7)])
def test_read_query_respects_blocking_and_nonblocking_order(tmp_path, assignment, expected):
    import z3

    from behave.sim.rtl.source_integral import typed_symbols
    from behave.sim.rtl.source_ir import lower_source_process

    from .test_rtl_source_ir import _encode, _run_activation

    source = f"module M(input clk,input [3:0] a,output logic [3:0] q,r); always @(posedge clk) begin {assignment} end endmodule"
    document, _, plan = _plans(tmp_path, source, (_read("q", 5, "five"), _read("q", 7, "seven")))
    context, = plan.declarations.contexts
    lowered = lower_source_process(source, document, plan, context, filename="design.sv")
    symbols = typed_symbols(document)
    names = {symbols[identity]["name"]: name for name, identity in lowered.storage}
    encoded = _encode(lowered)
    _run_activation(lowered, names, {"a": 7, "q": 5, "r": 0}, encoded)
    manifest = manifest_of(plan.declarations, filename="design.sv")
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.pre[names["a"]] == 7, encoded.pre[names["q"]] == 5)
    assert solver.check() == z3.sat
    hits = {manifest[goal].event for mark, _, goal in lowered.occurrences
            if manifest[goal].family == "value-observation" and z3.is_true(solver.model().eval(encoded.hits[mark]))}
    assert hits == {"five" if expected == 5 else "seven"}


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.solver
def test_selected_scalar_and_packed_read_goals_keep_native_values(tmp_path):
    import z3

    from behave.sim.rtl.source_integral import typed_symbols
    from behave.sim.rtl.source_ir import lower_source_process

    from .test_rtl_source_ir import _encode

    source = """module M(input clk,en,input [1:0][1:0] a,input [3:0] b,
      output logic [15:0] q=0,r=0,c=0);
      logic signed flag;
      always @(posedge clk) if(en) begin
        flag=a[0][0]; q=a[1][0]+a[1]; r=flag; c=a;
      end
    endmodule"""
    rules = (_read("a", 13, "base"), _read("a[1]", 3, "slice"),
             _read("a[1][0]", 1, "bit"), _read("flag", -1, "negative-bit"))
    document, _, plan = _plans(tmp_path / "parse", source, rules)
    context, = plan.declarations.contexts
    lowered = lower_source_process(source, document, plan, context, filename="design.sv")
    symbols = typed_symbols(document)
    names = {symbols[identity]["name"]: name for name, identity in lowered.storage}
    encoded = _encode(lowered)
    catalogue = manifest_of(plan.declarations, filename="design.sv")
    # Selected prefixes are storage paths, not additional whole-value reads.
    # Only the four explicitly evaluated occurrences above get read goals.
    assert Counter(goal.event for goal in catalogue.values()
                   if goal.family == "value-observation") == Counter(rule.name for rule in rules)
    _, hits = _run_values(tmp_path / "native", plan.native().text)
    native = {(inputs, goal.event) for inputs, goal in _named_hits(plan, hits)
              if goal.family == "value-observation"}
    for a in range(16):
        expected = ({"base"} if a == 13 else set()) | ({"slice"} if a >> 2 == 3 else set())
        if a & 4:
            expected.add("bit")
        if a & 1:
            expected.add("negative-bit")
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre[names["a"]] == a, encoded.pre[names["en"]] == 1)
        assert solver.check() == z3.sat
        actual = {catalogue[goal].event for mark, _, goal in lowered.occurrences
                  if catalogue[goal].family == "value-observation"
                  and z3.is_true(solver.model().eval(encoded.hits[mark]))}
        assert actual == expected
        assert {event for inputs, event in native if inputs == (a, 0, 1)} == expected
    assert not any(event for (_, _, enabled), event in native if not enabled)


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.isolation
@pytest.mark.solver
def test_public_rtl_baseline_miss_search_and_native_confirmation(tmp_path, record_property):
    import json
    from time import monotonic

    start = monotonic()
    integer = api.FiniteType(8)
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", integer, "a")], api.settled("in"), None, kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", integer, "y")], api.settled("out"), None,
                     kind=api.Kind.TRANSACTION, answers_per_transaction=1)], [],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))],
        source_read_rules=(_read("a", 173),))
    source = "module M(input [7:0] a, output [7:0] y); assign y=a; endmodule"
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b"class M:\n def reset(self): pass\n def process(self,a): return {'y':a}\n"}, config={"class": "M"})
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"))
    providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "queries")
                      if p.policy == RTL_LOCAL_GUIDANCE_POLICY)
    assert len(providers) == 1
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference, providers=providers,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100),
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True), run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    goal, = (goal.id for goal in result.evaluation.catalogue.values() if goal.family == "value-observation")
    search, = result.generated.searches
    assert goal in search.telemetry["baseline_coverage"]["unhit"]
    assert goal in result.evaluation.hits
    assert any(goal in dict(row)["targets"] for rows in search.provenance.values() for row in rows)
    record_property("source_read_loop", json.dumps({"goal": goal, "baseline_hit": False,
                                                   "confirmed_hit": True, "total_s": monotonic() - start}))
