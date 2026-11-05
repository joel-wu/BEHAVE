"""Finite task monitors consume actual ordered transactions on both backends."""

import pickle
from itertools import product
from time import perf_counter
from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.failure import FailureRaised
from behave.sim.core.goal_provider import CONCRETE_PHASE, EXACT_ANALYSIS_PHASE, LOCAL_GUIDANCE
from behave.sim.core.reference_progress import ReferenceProgress
from behave.sim.core.stimulus import SlotRecord
from behave.sim.core.transaction_monitor import DeclaredTransactionMonitor
from behave.sim.goal_policy import evaluation_goal_policy

from .test_baseline_public_sequence import REFERENCE, RTL, _contract
from .test_command_handler import _BaselineOnly


def _rule():
    rows = []
    for state, event in product((0, 1), ("zero", "bad", "other")):
        goals = (("bad-output",) if event == "bad" else
                 ("zero-pair",) if state == 1 and event == "zero" else ())
        rows.append(api.FiniteMonitorTransition(state, event, int(event == "zero"), goals))
    return api.TransactionMonitorSpec("stream", api.FiniteMonitor(
        (0, 1), ("zero", "bad", "other"), 0, ("zero-pair", "bad-output"), rows), (
            api.TransactionEvent("zero", "out", api.GoalCondition("eq", api.GoalField("y"), 0)),
            api.TransactionEvent("bad", "out", api.GoalCondition("eq", api.GoalField("y"), 1)),
            api.TransactionEvent("other", "out")), order=("out",), violations=("bad-output",))


def _declared(*rules):
    c = _contract()
    return api.TaskContract(c.endpoints, c.clocks, transaction_monitors=rules or (_rule(),))


def _record(value, behavior):
    return (SlotRecord(outputs={"out": ((value,),)}) if behavior else
            SlotRecord(accepted={"out": (value,)}))


def test_public_wire_and_scoped_capabilities():
    c = _declared()
    assert api.TaskContract.from_dict(c.as_dict()).as_dict() == c.as_dict()
    assert pickle.loads(pickle.dumps(c)).as_dict() == c.as_dict()
    assert c.digest != _contract().digest
    old = _contract().as_dict()
    old.pop("latency_goals")
    old.pop("operation_rules")
    old.pop("source_read_rules")
    old["version"] = "task-contract/10"
    old.pop("transaction_monitors")
    assert api.TaskContract.from_dict(old).digest == _contract().digest
    for kind in ("behavior", "rtl"):
        policy = evaluation_goal_policy(c, CatalogueManifest(()), artifact_kind=kind)
        policy.validate(CONCRETE_PHASE)
        policy.validate(EXACT_ANALYSIS_PHASE)
        assert all(provider.has(LOCAL_GUIDANCE) for provider in policy.enabled_providers()
                   if provider.identity.name.startswith("task-transaction-"))
        assert policy.manifest["monitor:stream:zero-pair"].family == "sequence"
        assert policy.manifest["monitor:stream:bad-output"].cls == api.SAFETY


def test_finite_wire_preserves_bool_integer_none_and_tuple_values():
    values = (False, 0, None, (True, 2, "v"))
    monitor = api.FiniteMonitor(values, values, False, ("seen",), [
        api.FiniteMonitorTransition(state, event, event, ("seen",))
        for state, event in product(values, repeat=2)])
    restored = api.FiniteMonitor.from_dict(monitor.as_dict())
    assert restored.as_dict() == monitor.as_dict()
    assert type(restored.step(False, 0).state) is int
    assert type(restored.step(0, False).state) is bool
    image = monitor.as_dict()
    image["events"][0]["value"] = []
    with pytest.raises(TypeError):
        api.FiniteMonitor.from_dict(image)


@pytest.mark.parametrize("behavior", (False, True))
def test_order_reset_persistence_safety_and_unknown_are_not_aggregated(behavior):
    monitor = DeclaredTransactionMonitor(_declared(), behavior=behavior)
    records = [_record(0, behavior), SlotRecord(), _record(0, behavior),
               _record(1, behavior), _record("X", behavior), _record(0, behavior), _record(0, behavior)]
    # The empty record may be a DUT reset: it is not a logical transaction.
    report = monitor.check(None, SimpleNamespace(records=records))
    assert [(hit.goal, hit.slot) for hit in report.hits] == [("monitor:stream:zero-pair", 2)]
    assert [(hit.goal, hit.slot) for hit in report.violations] == [("monitor:stream:bad-output", 3)]
    assert {row.slot for row in report.unresolved} == {4, 5, 6}
    fresh = monitor.check(None, SimpleNamespace(records=[_record(0, behavior)]))
    assert not fresh.hits and not fresh.violations and not fresh.unresolved


def test_behavior_batch_order_and_explicit_same_record_endpoint_order():
    monitor = DeclaredTransactionMonitor(_declared(), behavior=True)
    report = monitor.check(None, SimpleNamespace(records=[SlotRecord(outputs={"out": ((0,), (0,), (1,))})]))
    assert [hit.goal for hit in report.hits] == ["monitor:stream:zero-pair"]
    assert [hit.goal for hit in report.violations] == ["monitor:stream:bad-output"]
    fsm = api.FiniteMonitor((0, 1), ("in", "out"), 0, ("reply",), [
        api.FiniteMonitorTransition(state, event, int(event == "in"),
                                    ("reply",) if state == 1 and event == "out" else ())
        for state, event in product((0, 1), ("in", "out"))])
    for order, expected in ((('in', 'out'), True), (('out', 'in'), False)):
        rule = api.TransactionMonitorSpec("order", fsm, [
            api.TransactionEvent("in", "in"), api.TransactionEvent("out", "out")], order=order)
        for behavior in (False, True):
            record = (SlotRecord(accepted={"in": (5,)}, outputs={"out": ((5,),)}) if behavior else
                      SlotRecord(accepted={"in": (5,), "out": (5,)}))
            report = DeclaredTransactionMonitor(_declared(rule), behavior=behavior).check(
                None, SimpleNamespace(records=[record]))
            assert bool(report.hits) is expected


def test_classifier_priority_and_contract_validation():
    rule = _rule()
    broad = api.TransactionEvent("zero", "out", api.GoalCondition("ge", api.GoalField("y"), 0))
    priority = api.TransactionMonitorSpec("priority", rule.monitor, (broad, *rule.events),
                                         order=("out",), violations=rule.violations)
    report = DeclaredTransactionMonitor(_declared(priority), behavior=False).check(
        None, SimpleNamespace(records=[_record(1, False), _record(1, False)]))
    assert len(report.hits) == 1 and not report.violations
    with pytest.raises(ValueError, match="default"):
        api.TransactionMonitorSpec("bad", rule.monitor, rule.events[:-1], order=("out",))
    with pytest.raises(ValueError, match="order"):
        api.TransactionMonitorSpec("bad", rule.monitor, rule.events, order=("in",))
    with pytest.raises(ValueError, match="distinct"):
        _declared(rule, rule)
    with pytest.raises(ValueError, match="history"):
        api.TransactionEvent("zero", "out", api.GoalCondition("eq", api.GoalField("y", previous=True), 0))


def test_reference_uses_its_own_outputs_and_keeps_monitor_state():
    progress = ReferenceProgress(_declared())
    # Candidate's bad output must not contaminate the reference monitor.
    progress.observe_reference_transactions(_record(1, False), 0, {"out": ((0,),)})
    progress.observe_reference_transactions(SlotRecord(), 1, {})
    progress.observe_reference_transactions(_record(1, False), 2, {"out": ((0,),)})
    assert len(progress._declared_monitor.hits) == 1
    with pytest.raises(FailureRaised) as caught:
        progress.observe_reference_transactions(_record(0, False), 3, {"out": ((1,),)})
    assert caught.value.failure.origin is api.FailureOrigin.REFERENCE
    assert not caught.value.failure.determines_score


@pytest.mark.isolation
@pytest.mark.parametrize("reference_bad", (False, True))
def test_atomic_reference_consumes_same_slot_batches_in_declared_order(tmp_path, reference_bad):
    from tests.support.atomic_behavior import _contract as atomic_contract
    from tests.support.atomic_behavior import _schedule
    from tests.support.device_boundary import evaluate_device

    c = atomic_contract()
    monitor = api.FiniteMonitor((0, 1), ("a", "b", "bad"), 0, ("pair", "bad-output"), [
        api.FiniteMonitorTransition(state, event, int(event == "a"),
            ("bad-output",) if event == "bad" else ("pair",) if state == 1 and event == "b" else ())
        for state, event in product((0, 1), ("a", "b", "bad"))])
    rule = api.TransactionMonitorSpec("atomic", monitor, (
        api.TransactionEvent("a", "a_out"),
        api.TransactionEvent("bad", "b_out", api.GoalCondition("eq", api.GoalField("ob"), 1)),
        api.TransactionEvent("b", "b_out")), order=("a_out", "b_out"), violations=("bad-output",))
    c = api.TaskContract(c.endpoints, c.clocks, transaction_monitors=(rule,))
    source = """class M:
    def reset(self):
        pass
    def process_a(self, a):
        return {"oa": 0}
    def process_b(self, b):
        return {"ob": 0}
"""
    reference_source = source.replace('"ob": 0', f'"ob": {int(reference_bad)}')
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": reference_source.encode()}, config={"class": "M"})
    path = tmp_path / "candidate.py"
    path.write_text(source)
    task = api.admit(c, api.realization_of_behavior_source(source, c), reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, timeout_s=60),
                                  api.GenerationSpec(1, 1, 0, 0), bounded_analysis=False)
    schedule = _schedule(n_slots=1)
    stimulus = api.Stimulus({"a_in": [(0,)], "b_in": [(0,)]},
                           [slot.event for slot in schedule.slots], clock_schedule=schedule)
    result = evaluate_device(task, [stimulus], handle._artifact, reference=reference,
                             catalogue=api.evaluation_manifest(task, handle.frontend_catalogue))
    if reference_bad:
        assert result.no_result_failures
        assert all(failure.origin is api.FailureOrigin.REFERENCE
                   and failure.kind == "reference-transaction-monitor"
                   for _index, failure in result.no_result_failures)
    else:
        assert result.complete and result.passed
        assert "monitor:atomic:pair" in result.hits


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
@pytest.mark.parametrize("candidate_bad,reference_bad", ((False, False), (True, False), (False, True)))
def test_public_baseline_observes_sequences_and_attributes_safety(tmp_path, kind, candidate_bad, reference_bad,
                                                               record_property):
    started = perf_counter()
    c = _declared()
    reference_source = REFERENCE.replace('return {"y": a}', f'return {{"y": {int(reference_bad)}}}')
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": reference_source.encode()}, config={"class": "M"})
    if kind == "behavior":
        source = REFERENCE.replace('return {"y": a}', f'return {{"y": {int(candidate_bad)}}}')
        path = tmp_path / "candidate.py"
        path.write_text(source)
        task = api.admit(c, api.realization_of_behavior_source(source, c, file="candidate.py"), reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="candidate.py", timeout_s=60),
                                      bounded_analysis=False)
    else:
        source = RTL.replace("VALUE", f"8'd{int(candidate_bad)}")
        path = tmp_path / "candidate.sv"
        path.write_text(source)
        task = api.admit(c, api.realization_of_rtl_source(source, top="M", contract=c), reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl", timeout_s=120))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", baseline=api.BaselinePolicy(),
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=False, reference=reference))
    record_property("public_lifecycle_seconds", perf_counter() - started)
    record_property("unique_baseline", len(result.generated.stimuli) if result.generated else 0)
    if reference_bad:
        assert not result.decision.authoritative, result.decision
        assert result.decision.failures
        assert all(failure.origin is api.FailureOrigin.REFERENCE
                   and failure.kind == "reference-transaction-monitor"
                   for _index, failure in result.decision.failures)
    else:
        assert result.status is api.CommandStatus.EVALUATED, result.status
        assert result.decision.authoritative
        assert not result.evaluation.no_result_failures
        assert result.evaluation.passed is not candidate_bad
        assert result.decision.passed is not candidate_bad
        assert ("monitor:stream:bad-output" in result.evaluation.hits) is candidate_bad
        if candidate_bad:
            # A native candidate violation is decisive without executing the
            # rest of the pool. Do not require old full-pool failure behavior.
            assert result.evaluation.failure_witness is not None
            assert "monitor:stream:bad-output" in result.evaluation.safety_hits
            assert result.evaluation.unexecuted
            assert not any(row["runs"] for row in result.evaluation.execution_cost["phases"]
                           if row["phase"] == "continuous")
        else:
            assert result.evaluation.complete
            assert "monitor:stream:zero-pair" in result.evaluation.hits
        assert all(stimulus.depth("in") == 100 for stimulus in result.generated.stimuli)
