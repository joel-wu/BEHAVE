"""Assertion failures occur after the lazy message, not at a false test alone."""

import json
import subprocess
import sys

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.instance import lower
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema


CASES = {
    "plain": ('assert a != 3', ''),
    "literal_message": ('assert a != 3, "bad value"', ''),
    "condition_failure": ('assert 8 // a', ''),
    "message_failure": ('assert a != 2, 1 // (a - 2)', ''),
    "lazy_message": ('assert a != 3, self.message(a)', '''
    def message(self, a):
        self.n = (self.n + 1) & 15
        return 7 if a & 1 else 8
'''),
    "inner_assert": ('assert a != 3, self.message(a)', '''
    def message(self, a):
        self.n = (self.n + 1) & 15
        assert a != 3, "inner"
        return a
'''),
    "short_circuit": ('assert (a & 1) and (8 // (a - 3)), self.message(a)', '''
    def message(self, a):
        self.n = (self.n + 1) & 15
        return a
'''),
    "always_true": ('assert True, 1 // 0', ''),
    "always_false": ('assert False, "stop"\n        self.n = 1 // 0', ''),
    "multiline": ('assert (\n            a != 3\n        ), (\n            "bad"\n        )', ''),
    "condition_effects": ('assert self.condition(a)', '''
    def condition(self, a):
        self.n = (self.n + 1) & 15
        return a != 3
'''),
    "same_line": ('assert a != 1; assert a != 2', ''),
    "loop": ('for i in range(a & 3):\n            assert i != 1', ''),
    "index_message": ('assert a != 3, [1, 2][a]', ''),
}


def _source(case):
    body, helpers = CASES[case]
    return f'''class M:
    def reset(self):
        self.n = 0
{helpers}
    def process(self, a):
        {body}
        self.n = (self.n + 2) & 15
        return {{"y": a}}
'''


def _schema():
    return TransactionSchema([Endpoint("a", "input", 4), Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)


def _native(namespace, a, hits, sites):
    model = namespace["M"]()
    model.reset()
    hits.clear()
    try:
        output = model.process(a)
    except (AssertionError, ZeroDivisionError, IndexError) as error:
        goal = instrumented.attribute_safety_failure(error, sites)
        if sites:
            assert goal is not None, (a, type(error), sites)
        if goal:
            hits.append(goal)
        return None, type(error).__name__, model.n, frozenset(hits)
    return output, "", model.n, frozenset(hits)


@pytest.mark.parametrize("case", CASES)
def test_assertion_is_declared_independently_of_lowering(case):
    source = _source(case)
    plan = plan_source(source_model(source), source)
    assertions = [site for site in plan.safety_sites if site.kind == "assertionerror"]
    assert len(assertions) == (2 if case in {"inner_assert", "same_line"} else 1)
    assert all(plan.manifest[site.id].cls == "safety" for site in assertions)


@pytest.mark.solver
@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_entire_assertion_catalogue_has_exact_queries_and_native_witnesses(tmp_path, case, lowerer):
    pytest.importorskip("z3")
    source = _source(case)
    plan = plan_source(source_model(source), source)
    instance = lower(source, _schema(), bound=1, lowerer=lowerer)
    assert instance.frontend_catalogue_manifest() == plan.manifest
    plain = {}
    exec(compile(source, "behavior.py", "exec", optimize=0), plain)
    observed, _, hits = instrumented.load_source(source, goal_plan=plan)
    native_hits = {}
    codes = {"": "", "AssertionError": "AssertionError", "ZeroDivisionError": "DivZero", "IndexError": "IndexError"}
    for a in range(16):
        expected = _native(plain, a, [], ())
        actual = _native(observed, a, hits, plan.safety_sites)
        assert actual[:3] == expected[:3]
        state, output, reached, error = interp.run_step(instance.proc, {"a": a})
        assert error == codes[actual[1]]
        assert set(reached) == actual[3]
        assert not output.transactions if error else output.transactions == [actual[0]]
        assert state["n"] == actual[2]
        native_hits[a] = actual[3]
    verdict = instance.query(tuple(plan.manifest), steps=1, timeout_ms=5000, workers=1)
    expected_reachable = set().union(*native_hits.values())
    assert not verdict.undecided, verdict.undecided
    assert set(verdict.reachable) == expected_reachable
    assert verdict.unreachable == set(plan.manifest) - expected_reachable
    audit = []
    for goal in plan.manifest:
        trace = verdict.reachable.get(goal)
        if trace is not None:
            assert len(trace) == 1
            assert goal in _native(observed, trace[0]["a"], hits, plan.safety_sites)[3]
        audit.append({"goal": goal, "answer": "SAT" if trace is not None else "UNSAT", "trace": trace})
    (tmp_path / "goal_audit.json").write_text(json.dumps(audit, indent=2))


@pytest.mark.solver
@pytest.mark.isolation
@pytest.mark.parametrize("case", ["plain", "message_failure", "lazy_message", "inner_assert"])
def test_assertion_source_audit_checks_the_actual_failure_occurrence(case):
    from behave.sim.core.audit import AuditCheckStatus

    instance = lower(_source(case), _schema(), bound=1)
    checked = instance.source_audit(tuple([{"a": a}] for a in range(4)))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()


def test_a_launcher_optimization_flag_cannot_remove_artifact_assertions():
    source = _source("literal_message")
    code = f'''from behave.sim.behavior.instrumented import load_source
namespace, _, _ = load_source({source!r})
model = namespace["M"]()
model.reset()
try:
    model.process(3)
except AssertionError:
    print("assertion-preserved")
else:
    raise RuntimeError("the launcher removed the artifact assertion")
'''
    result = subprocess.run([sys.executable, "-O", "-c", code], check=True,
                            capture_output=True, text=True)
    assert result.stdout.strip() == "assertion-preserved"


@pytest.mark.solver
def test_a_missing_assertion_producer_is_detected_even_without_a_failing_input(monkeypatch):
    from behave.sim.behavior.lowering import LoweringError, _Lowerer

    guard = _Lowerer._guard

    def omit_assertion(self, condition, goal, code, node=None):
        if code != "AssertionError":
            guard(self, condition, goal, code, node)

    monkeypatch.setattr(_Lowerer, "_guard", omit_assertion)
    with pytest.raises(LoweringError, match="neither produced nor discharged"):
        lower(_source("plain"), _schema(), bound=1)


@pytest.mark.solver
@pytest.mark.isolation
@pytest.mark.parametrize("message", ["", ', "bad value"', ", self.message(a)"])
def test_public_search_finds_and_replays_a_baseline_missed_assertion(tmp_path, message):
    from behave.sim import api
    from .test_baseline_public_sequence import REFERENCE, _contract

    source = REFERENCE.replace('        return {"y": a}',
        '        assert a != 7' + message + '\n        return {"y": a}')
    if "self.message" in message:
        source += '\n    def message(self, a):\n        assert a != 7, "inner"\n        return a\n'
    contract = _contract()
    path = tmp_path / "candidate.py"
    path.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_behavior_source(source, contract, file="candidate.py"),
                     reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="candidate.py", timeout_s=90),
                                  bounded_analysis=False)
    providers = tuple(provider for provider in handle.generation_providers(
        reference, workdir=tmp_path / "guidance") if "candidate:" in provider.name)
    assert len(providers) == 1
    request = handle.command_request(run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100), providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True), run_analysis=False)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED
    assert result.decision.authoritative and not result.decision.passed
    assert not result.evaluation.no_result_failures
    assertions = {goal for goal in result.evaluation.catalogue if goal.endswith(":assertionerror")}
    assert len(assertions) == (2 if "self.message" in message else 1)
    actual = assertions & set(result.evaluation.hits)
    assert len(actual) == 1
    if "self.message" in message:
        assert next(iter(actual)) == next(goal for goal in assertions
            if result.evaluation.catalogue[goal].where("line") == source.count("\n", 0, source.index('assert a != 7, "inner"')) + 1)
    baseline_ids = {stimulus.identity_bytes for stimulus in request.baseline.stimuli}
    for run in result.evaluation.results:
        if run.stimulus.identity_bytes in baseline_ids:
            assert not (assertions & {goal for record in run.trace.records for goal, hit in record.hits if hit})
    assert any(run.stimulus.identity_bytes not in baseline_ids for run in result.evaluation.results)
    assert 0 < sum(search.telemetry["probes"] for search in result.generated.searches) <= 4
