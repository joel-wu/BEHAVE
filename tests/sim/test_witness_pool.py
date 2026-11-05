"""The secondary witness pool never replaces the authoritative evaluation."""

from __future__ import annotations

import json

import pytest

from behave.sim import api
from behave.sim.core.catalogue import COVERAGE, SAFETY, Goal
from behave.sim.core.outcome import ConcreteEvaluation
from behave.sim.core.run_record import RunResult
from behave.sim.core.stimulus import (
    AuditedTrace,
    Completion,
    SlotRecord,
    canonical_json,
)
from tests.support.device_boundary import run_device

from .test_api_path import Doubling, _evaluate, _ref, contract, realization, schedule


class SelectiveFault(Doubling):
    """Correct except on input nine, so one stimulus uniquely carries a mismatch."""

    def settle(self, presented, controls):
        outputs = super().settle(presented, controls)
        if presented is not None and presented[0] == 9 \
                and controls.get("in_valid", 0) and outputs["in_ready"]:
            self.next_held = (27,)
        return outputs


def _evaluation(values=(2, 3), artifact=None):
    task = api.admit(contract(), realization(), reference=_ref())
    stimuli = [schedule([value]) for value in values]
    return _evaluate(
        task, stimuli, artifact or Doubling(), reference=_ref())


def test_redundant_witnesses_form_a_separate_pool_and_do_not_change_the_score():
    evaluation = _evaluation()
    before = json.dumps(evaluation.as_dict(), sort_keys=True)
    raw_before = json.dumps(
        [result.trace.as_dict() if result.ran else None
         for result in evaluation.results],
        sort_keys=True)

    pool = api.minimize_witnesses(evaluation)

    assert type(pool) is api.MinimizedWitnessPool
    assert len(pool.results) == 1
    assert len(pool.dropped) == 1
    assert pool.evaluation == evaluation.fingerprint
    assert json.dumps(evaluation.as_dict(), sort_keys=True) == before
    assert json.dumps(
        [result.trace.as_dict() if result.ran else None
         for result in evaluation.results],
        sort_keys=True) == raw_before
    assert evaluation.passed and len(evaluation.results) == 2


def test_a_unique_mismatch_is_retained_while_a_redundant_clean_run_may_drop():
    evaluation = _evaluation((2, 9), SelectiveFault())

    pool = api.minimize_witnesses(evaluation)

    assert len(pool.results) == 1
    assert pool.results[0].faults
    assert any(row.startswith("mismatch:")
               for row in pool.contributions[pool.results[0].stimulus.fingerprint])
    assert evaluation.mismatch_count == 1, "the authoritative score was not recomputed"


def test_minimized_members_are_independent_of_input_result_order():
    evaluation = _evaluation((2, 3, 4))
    reversed_evaluation = ConcreteEvaluation(
        evaluation.task, results=tuple(reversed(evaluation.results)),
        hits=evaluation.hits, telemetry={},
        catalogue=tuple(evaluation.catalogue.values()))

    left = api.minimize_witnesses(evaluation)
    right = api.minimize_witnesses(reversed_evaluation)

    assert [stimulus.fingerprint for stimulus in left.stimuli] == \
        [stimulus.fingerprint for stimulus in right.stimuli]
    assert set().union(*left.contributions.values()) == \
        set().union(*right.contributions.values())


def test_an_unattributed_goal_hit_refuses_instead_of_being_pruned():
    evaluation = _evaluation((2,))
    ghost = Goal(
        "ghost", SAFETY, family="test", provider="test", policy="test/1",
        evidence="trace")
    forged = ConcreteEvaluation(
        evaluation.task, results=evaluation.results, hits=(ghost.id,),
        telemetry={}, catalogue=(ghost,))

    with pytest.raises(api.Refused, match="minimization-hit-attribution-incomplete"):
        api.minimize_witnesses(forged)


def test_a_real_trace_monitor_safety_hit_is_attributed_and_preserved():
    task = api.admit(
        contract(answers=1, deadline=1), realization(), reference=_ref())

    class Silent:
        kind = "device"
        identity = "doubling"

        def reset(self):
            pass

        def settle(self, _presented, _controls):
            return {"in_ready": 1, "out_valid": 0, "out_y": 0}

        def edge(self):
            pass

    stimulus = api.reset_then(
        task.contract, [(3,)], controls={"out_ready": 1}, drain=4)
    evaluation = _evaluate(
        task, (stimulus,), Silent(), reference=_ref())
    pool = api.minimize_witnesses(evaluation)

    assert "monitor.response-deadline" in evaluation.safety_hits
    assert "goal:monitor.response-deadline" in \
        pool.contributions[stimulus.fingerprint]


def test_minimization_requires_the_closed_concrete_record():
    with pytest.raises(api.Refused, match="minimization-concrete-record-invalid"):
        api.minimize_witnesses(object())


def test_selection_budget_refuses_before_returning_a_partial_cover():
    evaluation = _evaluation((2, 3))
    before = canonical_json(evaluation.as_dict())

    refused = api.secondary_pool_report(evaluation, max_checks=1)
    available = api.secondary_pool_report(evaluation, max_checks=2)

    assert refused["status"] == "refused"
    assert refused["refusal"]["kind"] == "minimization-budget-exhausted"
    assert "pool" not in refused
    assert available["status"] == "available"
    assert available["pool"] == api.minimize_witnesses(evaluation).as_dict()
    assert canonical_json(evaluation.as_dict()) == before


def test_selection_tie_breaking_does_not_reserialize_candidates(monkeypatch):
    from behave.sim import witness_pool

    class Wrong(Doubling):
        def settle(self, presented, controls):
            output = super().settle(presented, controls)
            if presented is not None and controls.get("in_valid", 0) and output["in_ready"]:
                self.next_held = (2 * presented[0] + 1,)
            return output

    evaluation = _evaluation(tuple(range(32)), Wrong())
    expected = api.minimize_witnesses(evaluation).as_dict()
    assert len(expected["kept"]) == len(evaluation.results)
    original = witness_pool.stimulus_bytes
    calls = 0

    def counted(stimulus):
        nonlocal calls
        calls += 1
        return original(stimulus)

    monkeypatch.setattr(witness_pool, "stimulus_bytes", counted)
    assert api.minimize_witnesses(evaluation).as_dict() == expected
    assert calls <= 4 * len(evaluation.results)


def test_the_secondary_pool_record_cannot_be_forged_or_extended():
    evaluation = _evaluation((2,))
    result = evaluation.results[0]
    feature = {result.stimulus.fingerprint: ("completion:normal",)}

    with pytest.raises(TypeError, match="cannot be subclassed"):
        class _Forged(api.MinimizedWitnessPool):
            pass

    with pytest.raises(TypeError, match="derived only"):
        api.MinimizedWitnessPool("not-a-fingerprint", (result,), (), feature)
    with pytest.raises(TypeError, match="derived only"):
        api.MinimizedWitnessPool(
            evaluation.fingerprint, (result,),
            (result.stimulus.fingerprint,), feature)
    with pytest.raises(TypeError, match="derived only"):
        api.MinimizedWitnessPool(evaluation.fingerprint, (result,), (), {})


def test_the_derived_record_rechecks_its_exact_partition_and_feature_universe():
    from behave.sim import witness_pool as implementation

    evaluation = _evaluation((2, 9), SelectiveFault())
    ordered = tuple(sorted(
        evaluation.results,
        key=lambda item: implementation.stimulus_bytes(item.stimulus)))
    clean = next(result for result in ordered if not result.faults)
    mismatch = next(result for result in ordered if result.faults)
    features = implementation._features(evaluation)
    clean_rows = {
        clean.stimulus.fingerprint:
            tuple(sorted(features[clean.stimulus.fingerprint]))}

    with pytest.raises(ValueError, match="canonical complement"):
        api.MinimizedWitnessPool(
            evaluation, (clean,), (), clean_rows,
            _token=implementation._DERIVED)
    with pytest.raises(ValueError, match="does not preserve"):
        api.MinimizedWitnessPool(
            evaluation, (clean,), (mismatch.stimulus.fingerprint,), clean_rows,
            _token=implementation._DERIVED)


@pytest.mark.parametrize("rows", [
    ((True, (("goal", "g"), ("slot", 0), ("endpoint", "out"))),),
    ((0, (("goal", "g"), ("slot", -1), ("endpoint", "out"))),),
    ((0, (("goal", "g"), ("slot", 0), ("endpoint", "out"),
          ("invented", 1))),),
])
def test_malformed_monitor_attribution_refuses_instead_of_losing_a_witness(rows):
    evaluation = _evaluation((2,))
    forged = ConcreteEvaluation(
        evaluation.task, results=evaluation.results, hits=(),
        telemetry={"monitor_hits": rows},
        catalogue=tuple(evaluation.catalogue.values()))

    with pytest.raises(api.Refused, match="minimization-monitor-evidence-malformed"):
        api.minimize_witnesses(forged)


def test_secondary_pool_telemetry_cannot_change_the_verdict_or_diagnostics():
    from behave.sim.evaluation_policy import verdict

    evaluation = _evaluation((2, 3))
    attached = {"operator_context": {"campaign": "test"}}

    concrete = verdict(evaluation, telemetry=attached)
    partial = verdict(
        evaluation, dropped=("outside-domain",), offered=3, telemetry=attached)

    assert concrete.evaluation is evaluation and concrete.passed
    assert partial.evaluation is evaluation and not partial.authoritative
    assert "operator_context" in concrete.telemetry
    assert "operator_context" in partial.telemetry
    assert "diagnostics" in concrete.telemetry
    assert "diagnostics" not in partial.telemetry
    with pytest.raises(ValueError, match="cannot replace"):
        verdict(evaluation, telemetry={"diagnostics": {"accepted": 999}})
    with pytest.raises(ValueError, match="post-decision report view"):
        verdict(evaluation, telemetry={
            "secondary_witness_pool": {"status": "available"}})


def test_a_secondary_pool_bug_is_telemetry_and_cannot_destroy_the_score(monkeypatch):
    from behave.sim.evaluation_policy import verdict

    evaluation = _evaluation((2,))

    def broken(_evaluation):
        raise RuntimeError("set-cover implementation bug")

    monkeypatch.setattr("behave.sim.witness_pool.minimize_witnesses", broken)
    telemetry = api.secondary_pool_report(evaluation)
    result = verdict(evaluation)

    assert result.passed and result.evaluation is evaluation
    assert telemetry["status"] == "refused"
    assert telemetry["refusal"]["kind"] == "secondary-witness-pool-failed"


def test_secondary_pool_report_has_one_closed_failure_shape(monkeypatch):
    evaluation = _evaluation((2,))

    def broken(_evaluation):
        raise RuntimeError("set-cover implementation bug")

    monkeypatch.setattr("behave.sim.witness_pool.minimize_witnesses", broken)
    got = api.secondary_pool_report(evaluation)

    assert set(got) == {"status", "refusal"}
    assert got["status"] == "refused"
    assert got["refusal"]["kind"] == "secondary-witness-pool-failed"


def test_every_unique_contribution_is_retained_and_only_the_clean_duplicate_drops():
    """Exercise the full preservation contract in one closed concrete record.

    The task's reference is the straight-line Behavior model used by ``_evaluation``;
    ``rtl.unique-slot`` exists only in the artifact's slot record.  The other
    contributors deliberately live at different evidence boundaries: a trace-judged
    monitor violation, an exact mismatch and an infrastructure outcome, each completion
    class and a distinct schedule class.  One clean run has only a schedule/completion
    class already covered by the others, so it is the member the secondary pool may
    remove.
    """
    evaluation = _evaluation((2, 3, 9, 4), SelectiveFault())
    assert evaluation.mismatch_count == 1
    slot_goal = Goal(
        "rtl.unique-slot", COVERAGE, family="rtl-branch", provider="rtl",
        policy="test/1", evidence="slot-record")
    monitor_goal = Goal(
        "monitor.unique-safety", SAFETY, family="protocol", provider="monitor",
        policy="test/1", evidence="trace")

    results = list(evaluation.results)
    original = results[0]
    records = list(original.trace.records)
    image = records[-1].as_dict()
    image["hits"] = [*image["hits"], [slot_goal.id, True]]
    records[-1] = SlotRecord.from_dict(image)
    marked_trace = AuditedTrace(
        original.stimulus, initial_state=dict(original.trace.initial_state),
        records=records, completion=original.trace.completion,
        domain=original.trace.domain, artifact=original.trace.artifact)
    comparison = api.compare_run(evaluation.task, _ref(), marked_trace)
    results[0] = RunResult.compared(
        original.stimulus, marked_trace, comparison)

    failed = schedule([5])
    results.append(RunResult.failed_to_run(
        failed,
        api.RunFailure.infrastructure(
            "test-replay",
            api.Refusal("simulator-crashed", "the simulator crashed"))))

    padded_source = _evaluation((6,)).results[0]
    padded_trace = AuditedTrace(
        padded_source.stimulus,
        initial_state=dict(padded_source.trace.initial_state),
        records=padded_source.trace.records, completion=Completion.PADDED,
        domain=padded_source.trace.domain, artifact=padded_source.trace.artifact)
    padded_comparison = api.compare_run(evaluation.task, _ref(), padded_trace)
    results.append(RunResult.compared(
        padded_source.stimulus, padded_trace, padded_comparison))

    incomplete_source = _evaluation((7,)).results[0]
    incomplete_trace = AuditedTrace(
        incomplete_source.stimulus,
        initial_state=dict(incomplete_source.trace.initial_state),
        records=incomplete_source.trace.records[:-1],
        completion=Completion.INCOMPLETE,
        domain=incomplete_source.trace.domain,
        artifact=incomplete_source.trace.artifact)
    results.append(RunResult.not_compared(
        incomplete_source.stimulus, incomplete_trace,
        api.RunFailure.verifier(
            "test-comparison",
            api.Refusal("trace-ended-early", "the trace ended early"))))

    distinct_schedule = schedule([8], ready=0)
    distinct_trace = run_device(evaluation.task, distinct_schedule, Doubling())
    distinct_comparison = api.compare_run(
        evaluation.task, _ref(), distinct_trace)
    results.append(RunResult.compared(
        distinct_schedule, distinct_trace, distinct_comparison))
    catalogue = tuple(evaluation.catalogue.values()) + (slot_goal, monitor_goal)
    combined = ConcreteEvaluation(
        evaluation.task, results=results,
        hits=(slot_goal.id, monitor_goal.id),
        telemetry={"monitor_violations": [
            (1, {"goal": monitor_goal.id, "slot": 0})]},
        catalogue=catalogue)

    pool = api.minimize_witnesses(combined)
    kept = {result.stimulus.fingerprint for result in pool.results}

    assert results[0].stimulus.fingerprint in kept       # slot-record goal
    assert results[1].stimulus.fingerprint in kept       # monitor safety goal
    assert results[2].stimulus.fingerprint in kept       # mismatch
    assert results[4].stimulus.fingerprint in kept       # infrastructure
    assert results[5].stimulus.fingerprint in kept       # padded completion
    assert results[6].stimulus.fingerprint in kept       # incomplete completion
    assert results[7].stimulus.fingerprint in kept       # distinct schedule
    assert results[3].stimulus.fingerprint in pool.dropped
    assert set(kept).isdisjoint(pool.dropped)
    retained = set().union(*pool.contributions.values())
    assert {"completion:normal", "completion:padded", "completion:incomplete"} \
        <= retained
    assert any(feature.startswith("goal:rtl.unique-slot") for feature in retained)
    assert any(feature.startswith("goal:monitor.unique-safety")
               for feature in retained)
    exact_faults = {
        "mismatch:" + canonical_json([
            kind, position,
            None if expected is None else list(expected),
            None if observed is None else list(observed),
        ])
        for kind, position, expected, observed in results[2].faults}
    assert exact_faults <= retained
    assert (
        "failure:run:infrastructure:no-result:test-replay:simulator-crashed"
        in retained)
    assert (
        "failure:comparison:verifier:no-result:test-comparison:trace-ended-early"
        in retained)
    assert len([feature for feature in retained
                if feature.startswith("schedule:")]) == 2
