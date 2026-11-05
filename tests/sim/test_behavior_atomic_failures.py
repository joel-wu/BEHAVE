"""Native atomic failures retain every coincident observation, never a commit."""

import pytest

pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.behavior.atomic_exact import build_atomic_behavior
from behave.sim.behavior.atomic_failure import source_failure_evidence
from behave.sim.behavior.multiclock import IsolatedAtomicMachine
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.failure import CandidateFault
from behave.sim.core.refusal import Refusal, Refused
from tests.support.atomic_behavior import _schedule

from .test_behavior_atomic_exact import _facts, _rows
from .test_behavior_atomic_transactions import IDENTITY, _declarations
from .test_command_handler import _BaselineOnly


def _source(domain, expression):
    return IDENTITY.replace(f'return {{"o{domain}": {domain}}}',
                            f'return {{"o{domain}": {expression}}}')


def _instance(source):
    contract = _declarations()
    return build_atomic_behavior(source, "atomic_failure.py", contract, _schedule(),
        task_contract=contract.digest, construction=_facts({}))


@pytest.mark.isolation
@pytest.mark.parametrize("domain", ("a", "b"))
@pytest.mark.parametrize("expression", (
    "1 // VALUE", "1 << (VALUE - 1)", "1 >> (VALUE - 1)",
    "pow(VALUE, -1, 2)", "[1][VALUE - 2]"))
def test_independent_source_audit_retains_a_failed_slot_and_absorbs_its_tail(domain, expression):
    source = _source(domain, expression.replace("VALUE", domain))
    instance = _instance(source)
    rows = _rows(a0=0, b0=0, a1=1, b2=1)
    assert instance.replay(rows).error
    checked = instance.source_audit((rows,))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()


@pytest.mark.isolation
@pytest.mark.parametrize("domain", ("a", "b", "both"))
@pytest.mark.parametrize("failure", ("division", "assertion"))
def test_public_native_failure_keeps_all_coincident_source_hits_and_no_outputs(tmp_path, domain, failure):
    source = IDENTITY
    for name in ("a", "b") if domain == "both" else (domain,):
        action = (f'return {{"o{name}": 1 // {name}}}' if failure == "division" else
                  f'assert {name} != 0\n        return {{"o{name}": {name}}}')
        source = source.replace(f'return {{"o{name}": {name}}}', action)
    path = tmp_path / "behavior.py"
    path.write_text(source)
    contract = _declarations()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": IDENTITY.encode()}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_behavior_source(source, contract, file="atomic_failure.py"),
                     reference=reference)
    generation = api.GenerationSpec(horizon=3, queue_bound=2, reset_hold_cycles=0, drain=0)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="atomic_failure.py", timeout_s=90),
        generation, bounded_analysis=False)
    schedule = _schedule()
    stimulus = api.Stimulus(queues={"a_in": ((0,), (1,)), "b_in": ((0,), (1,))},
        slots=[slot.event for slot in schedule.slots], clock_schedule=schedule)
    trace = handle._artifact.trace(contract, stimulus)
    assert trace.completion is api.Completion.PADDED
    first = trace.records[0]
    assert dict(first.accepted) == {"a_in": (0,), "b_in": (0,)}
    assert not first.outputs
    assert not any(hit for record in trace.records[1:] for _, hit in record.hits)
    reached = {goal for goal, hit in first.hits if hit}
    failures = {goal for goal in reached if handle.frontend_catalogue[goal].cls == api.SAFETY}
    assert len(failures) == (2 if domain == "both" else 1)
    if domain != "both":
        instance = _instance(source)
        expected = set(instance.replay(_rows(a0=0, b0=0, a1=1, b2=1)).hits)
        assert reached == expected & set(handle.frontend_catalogue)
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command",
        baseline=api.build_baseline(contract, generation, realization=task.realization,
            policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1)), providers=(_BaselineOnly(),),
        probe_budget=0, run_analysis=False, reference=reference))
    assert result.decision.authoritative and not result.evaluation.passed
    assert not result.evaluation.no_result_failures


@pytest.mark.isolation
def test_a_partial_write_before_failure_does_not_replace_the_previous_native_commit():
    source = '''class M:
    def reset(self):
        self.x = 0
        self.y = 0
    def process_a(self, a):
        self.x = a + 1
        return {"oa": 1 // a}
    def process_b(self, b):
        self.y = b + 1
        return {"ob": b}
'''
    contract = _declarations()
    instance = build_atomic_behavior(source, "stateful_failure.py", contract, _schedule(),
        task_contract=contract.digest, construction=_facts({"x": 0, "y": 0}))
    rows = _rows(a0=1, b0=2, a1=0, b2=3)
    replay, witness = instance.replay_witness(rows, instance.bind().domain.digest())
    assert replay.error == "DivZero"
    assert dict(witness.states[0]) == {"x": 0, "y": 0}
    assert all(dict(state) == {"x": 2, "y": 3} for state in witness.states[1:])
    checked = instance.source_audit((rows,))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()


@pytest.mark.isolation
@pytest.mark.parametrize("expression", ("1 // a", "1 << (a - 1)", "pow(a, -1, 2)"))
def test_source_audit_compares_the_error_code_not_just_whether_a_handler_failed(monkeypatch, expression):
    instance = _instance(_source("a", expression))
    step = IsolatedAtomicMachine.step

    def wrong_error(self, slot, invocations=()):
        try:
            return step(self, slot, invocations)
        except CandidateFault as exc:
            detail = exc.refusal.as_dict()["detail"]
            detail["error"] = "IndexError"
            raise CandidateFault(Refusal(exc.refusal.kind, exc.refusal.why, **detail)) from None

    monkeypatch.setattr(IsolatedAtomicMachine, "step", wrong_error)
    checked = instance.source_audit((_rows(a0=0, b0=2, a1=1, b2=3),))
    assert checked.status is AuditCheckStatus.INCONSISTENT, checked.as_dict()


def test_unattributed_exceptions_and_incomplete_failure_records_cannot_become_audit_evidence():
    assert source_failure_evidence(ZeroDivisionError()) is None
    assert source_failure_evidence(CandidateFault(Refusal("multiclock-handler-failed", "opaque callback"))) is None
    with pytest.raises(Refused, match="failure records"):
        source_failure_evidence(CandidateFault(Refusal("behavior-semantic-failure", "missing evidence")))


@pytest.mark.isolation
def test_public_analysis_audits_a_missing_failure_witness_without_changing_the_completed_score(tmp_path):
    # The single zero-valued baseline is safe and agrees with the reference.
    # The declared 2-bit domain also admits a=3, which reaches the partial operation.
    source = _source("a", "1 // (3 - a)")
    path = tmp_path / "behavior.py"
    path.write_text(source)
    contract = _declarations()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": IDENTITY.encode()}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_behavior_source(source, contract, file="atomic_failure.py"),
                     reference=reference)
    generation = api.GenerationSpec(horizon=3, queue_bound=1, reset_hold_cycles=0, drain=0)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="atomic_failure.py", timeout_s=90),
        generation, bounded_analysis=True)
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command",
        # This fixture declares a fixed schedule, not response cardinalities for
        # transaction-driven multi-clock completion. Preserve that public domain.
        baseline=api.build_baseline(contract, generation, realization=task.realization,
            policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1)), providers=(_BaselineOnly(),),
        probe_budget=0, run_analysis=True, reference=reference,
        feasibility_timeout_ms=30_000, query_timeout_ms=30_000,
        analysis_wall_timeout_seconds=180))
    assert result.decision.authoritative and result.evaluation.passed
    goal, = (goal for goal, declaration in handle.frontend_catalogue.items() if declaration.cls == api.SAFETY)
    assert goal not in result.evaluation.hits
    assert result.analysis.concrete == result.evaluation.fingerprint
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    assert result.analysis.per_goal[goal]["status"] == "reached", result.analysis.as_dict()
    witness = next(row for row in result.analysis.evidence["witnesses"]
                   if row["witness_ref"] == result.analysis.per_goal[goal]["support_ref"])
    assert witness["origin"] == "bounded-query"
