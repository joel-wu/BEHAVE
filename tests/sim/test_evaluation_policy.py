"""The one rule from a `ConcreteEvaluation` to a result kind, and its diagnostics.

Split out of `test_wave1_closure.py` by concern; every test is a review counterexample
turned regression against `behave.sim.evaluation_policy` and the `Authoritative` record
type. Shared fixtures are imported from where they are defined.
"""
import pathlib  # noqa: F401

import pytest

from behave.sim import api
from behave.sim.core.catalogue import COVERAGE, Goal
from behave.sim.core.hit_vector import HitVector
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.evaluation import _batched
from tests.support.device_boundary import run_device

from .test_api_path import (  # noqa: F401
    Deaf,
    Doubling,
    _evaluate,
    _live,
    _ref,
    contract,
    realization,
    schedule,
)


def _owing(accepted_at, owed=1, endpoint="out", goal="monitor.unanswered-at-horizon"):
    """One unresolved row of the shape `monitor._terminal` emits: one obligation."""
    return {"goal": goal, "slot": 70, "endpoint": endpoint,
            "about": {"accepted_at": accepted_at, "owed": owed, "truncated": False}}


class _Horizon:
    """An evaluation, as `_only_unanswered_at_the_horizon` reads one.

    ``per_run`` is one fault list per run, so a test can put the fault in one run and the
    obligation in another -- which is the shape the flattened version could not tell from
    the aligned one.
    """

    def __init__(self, per_run, unresolved, policy="ordered", sink="out"):
        class _Comparison:
            def __init__(self, faults):
                self._faults = faults

            def as_dict(self):
                return {"faults": self._faults, "policy": policy}

        class _Result:
            ran = True

            def __init__(self, faults):
                self.comparison = _Comparison(faults)

        class _Endpoint:
            name = sink

        class _Contract:
            def outputs(self):
                return [_Endpoint()]

        class _Task:
            contract = _Contract()

        self.results = [_Result(f) for f in per_run]
        self.telemetry = {"monitor_unresolved": unresolved}
        self.task = _Task()


@pytest.mark.parametrize("subset", ("whole", "dropped", "capped"))
def test_a_dropped_or_capped_candidate_set_carries_no_score(subset):
    from behave.sim.authority import CONCRETE, PARTIAL
    from behave.sim.evaluation_policy import verdict

    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    evaluation = _evaluate(task, [schedule([1, 2])], Doubling(), reference=reference)
    got = verdict(evaluation, dropped=("missing",) if subset == "dropped" else (),
                  capped=1 if subset == "capped" else 0, offered=2)
    if subset == "whole":
        assert got.kind == CONCRETE and got.passed
    else:
        assert got.kind == PARTIAL and "subset" in got.why
        with pytest.raises(Refused, match="authority-no-score-under-this-kind"):
            got.passed


@pytest.mark.parametrize("bad", (0, -1, True))
def test_invalid_baseline_size_is_refused_before_artifact_construction(bad):
    with pytest.raises(ValueError, match="positive exact integer"):
        api.BaselinePolicy(case_limit=bad)


@pytest.mark.cosim
def test_native_observations_cannot_fall_back_to_sample_checked_batch(tmp_path, monkeypatch):
    from behave.sim.rtl.artifact import RTLArtifact
    from tests.support.scoring_task import handshake_register_command

    sound = api.handle_command(handshake_register_command(tmp_path / "sound"))
    assert sound.decision.authoritative and sound.decision.passed, sound.as_dict()
    assert sound.evaluation.required_complete
    assert all(run.trace.source_evidence is not None
               for run in sound.evaluation.results)
    request = handshake_register_command(tmp_path / "missing-native")
    monkeypatch.delattr(RTLArtifact, "traces_per_process")
    failed = api.handle_command(request)
    assert not failed.decision.authoritative and failed.decision.passed is None
    assert failed.evaluation.no_result_failures
    assert all(failure.kind == "verifier-replay-failed"
               and failure.origin is api.FailureOrigin.VERIFIER
               and failure.disposition is api.FailureDisposition.NO_RESULT
               for _, failure in failed.evaluation.no_result_failures)
    assert all(run.trace is None for run in failed.evaluation.results)


def test_per_process_replay_preserves_a_typed_setup_refusal():
    """A typed adapter refusal must not be relabelled as an untyped crash."""

    task = api.admit(contract(), realization(), reference=_ref())
    stimuli = [schedule([1]), schedule([2])]

    class Stops:
        kind = "rtl"
        identity = task.realization.identity

        @staticmethod
        def traces_per_process(_contract, _stimuli):
            raise Refused(Refusal(
                "rtl-structural-plan-unavailable",
                "the frozen structural plan cannot be replayed"))

    runs, note = _batched(task, stimuli, Stops())

    assert {failure.kind for failure in runs.values()} == {
        "rtl-structural-plan-unavailable"}
    assert "rtl-structural-plan-unavailable" in note["why"]


def test_a_run_in_which_nothing_crossed_scores_zero_and_says_why():
    from behave.sim.authority import CONCRETE
    from behave.sim.evaluation_policy import verdict

    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    stimulus = api.reset_then(task.contract, [], drain=2, controls={"out_ready": 1})
    evaluation = _evaluate(task, [stimulus], Doubling(), reference=reference)
    assert evaluation.accepted == 0 and evaluation.results
    got = verdict(evaluation)
    assert got.kind == CONCRETE and got.passed is False
    assert dict(dict(got.telemetry)["diagnostics"])["accepted"] == 0


def test_an_unanswered_transaction_is_counted_only_where_every_fault_is_one():
    """The alignment rule behind the `unanswered_at_horizon` **diagnostic**.

    It is a diagnostic and not a verdict: Appendix A.1 makes an unpaired expected
    transaction a mismatch and the run a concrete failure, whatever this counts. What
    the count is for is the reading beside the 0 -- non-zero exactly when every fault in
    the evaluation is a transaction still owed at the horizon, each excused by an
    obligation instance in its own run, so an operator knows a `response_deadline`
    declaration is what would separate "late" from "never". A wrong value or an
    unexpected output anywhere makes it 0: those runs are not about the horizon.
    """
    from behave.sim.evaluation_policy import _only_unanswered_at_the_horizon

    late = [["missing", 3, [0], None], ["missing", 4, [1], None]]
    at_horizon = [(0, _owing(0)), (0, _owing(1))]

    assert _only_unanswered_at_the_horizon(_Horizon([late], at_horizon)) == 2
    # A wrong value among them, and the horizon explains nothing.
    assert _only_unanswered_at_the_horizon(
        _Horizon([late + [["value", 5, [1], [2]]]], at_horizon)) == 0
    # An output nobody expected is not a late answer either.
    assert _only_unanswered_at_the_horizon(
        _Horizon([[["extra", 2, None, [7]]]], at_horizon)) == 0
    # And `missing` with nothing outstanding at the end is a different fact: the
    # monitor's own reading is required, not just the shape of the fault.
    assert _only_unanswered_at_the_horizon(_Horizon([late], [])) == 0
    # No faults at all is not this case either.
    assert _only_unanswered_at_the_horizon(_Horizon([[]], at_horizon)) == 0


def test_the_horizon_excuses_the_run_the_evidence_is_about_and_no_other():
    """The evidence is aligned, not aggregated -- for the diagnostic's count.

    (A diagnostic since the appendix alignment: the count no longer converts a verdict,
    and what it must still not do is claim every fault is a late answer when some are
    not.) Every fault of every run used to go into one list; then *any* unresolved row
    anywhere in the evaluation that mentioned "unanswered" excused the lot. Three ways
    that is wrong, and each is a run this build must not excuse:

    * the row belongs to **another run** -- stimulus 7 ended with a transaction in flight,
      so stimulus 3's missing output is forgiven by evidence about a different run;
    * the row belongs to **another endpoint** -- an obligation about a sink this
      comparison is not over;
    * there are **more missing outputs than answers outstanding** -- one transaction in
      flight excusing ten missing outputs, which is a count matched against a boolean.

    And the policy: only `ordered` matching gives a missing fault a position in the
    stream. Under `content` it is an index into a multiset difference and lines up with
    no obligation instance at all.
    """
    from behave.sim.evaluation_policy import _only_unanswered_at_the_horizon

    one = [["missing", 3, [0], None]]
    ten = [["missing", i, [i], None] for i in range(10)]

    # ── the run the evidence is about ──
    # Run 0 has the fault; run 1 is the one that ended with something in flight.
    assert _only_unanswered_at_the_horizon(
        _Horizon([one, []], [(1, _owing(0))])) == 0
    # The same evidence against the run it belongs to.
    assert _only_unanswered_at_the_horizon(
        _Horizon([one, []], [(0, _owing(0))])) == 1
    # Two runs, each with its own outstanding transaction, is two excused faults and not
    # one run's evidence spent twice.
    assert _only_unanswered_at_the_horizon(
        _Horizon([one, one], [(0, _owing(0)), (1, _owing(0))])) == 2
    assert _only_unanswered_at_the_horizon(
        _Horizon([one, one], [(0, _owing(0)), (0, _owing(1))])) == 0

    # ── the endpoint ──
    assert _only_unanswered_at_the_horizon(
        _Horizon([one], [(0, _owing(0, endpoint="other"))])) == 0

    # ── the obligation instance, one answer each ──
    assert _only_unanswered_at_the_horizon(_Horizon([ten], [(0, _owing(0))])) == 0
    assert _only_unanswered_at_the_horizon(
        _Horizon([ten], [(0, _owing(0, owed=10))])) == 10
    assert _only_unanswered_at_the_horizon(
        _Horizon([ten], [(0, _owing(0, owed=9))])) == 0

    # ── a truncated run observed no terminal state, so it carries no `owed` ──
    truncated = {"goal": "monitor.unanswered-at-horizon", "slot": 9, "endpoint": "out",
                 "about": {"truncated": True, "in_flight": 1, "owed": None}}
    assert _only_unanswered_at_the_horizon(_Horizon([one], [(0, truncated)])) == 0

    # ── another obligation entirely is not this one ──
    assert _only_unanswered_at_the_horizon(
        _Horizon([one], [(0, _owing(0, goal="monitor.sink-stability"))])) == 0

    # ── and only `ordered` matching puts a missing fault at a position ──
    assert _only_unanswered_at_the_horizon(
        _Horizon([one], [(0, _owing(0))], policy="content")) == 0


@pytest.mark.cosim
def test_a_late_answer_is_a_mismatch_and_the_diagnostic_says_what_to_declare(tmp_path):
    from tests.support.scoring_task import handshake_register_command

    result = api.handle_command(handshake_register_command(
        tmp_path, source_name="drop_fourth.sv.txt"))
    assert result.decision.authoritative and result.decision.passed is False, result.as_dict()
    diagnostics = dict(dict(result.decision.authority["telemetry"])["diagnostics"])
    assert diagnostics["unanswered_at_horizon"] >= 1
    assert diagnostics["accepted"] >= 4
    assert result.evaluation.mismatch_count >= 1


def test_an_unbound_trace_is_not_a_concrete_failure_of_anything():
    """An unbound trace names no artifact or no domain, so the evidence cannot be tied
    to the design under test -- and `ConcreteEvaluation.passed` answers False over one,
    which under CONCRETE read as "this design determinately failed". Reproduced with a
    run whose **agreement is perfect**: the same records, bindings stripped, and the
    verdict was FAIL.

    Two layers, like every rule on this type: the constructor refuses, and the
    authority path returns `partial` for an evaluation that reaches it unbound.
    """
    from behave.sim.authority import CONCRETE, Authoritative
    from behave.sim.core.outcome import ConcreteEvaluation
    from behave.sim.core.run_record import RunResult
    from behave.sim.core.stimulus import AuditedTrace

    task = api.admit(contract(), realization(), reference=_ref())
    stim = schedule([3])
    honest = run_device(task, stim, Doubling())
    stripped = AuditedTrace(stim, records=list(honest.records),
                            completion=honest.completion, domain="", artifact="")
    comparison = api.compare_run(task, _ref(), stripped)
    evaluation = ConcreteEvaluation(
        task, results=[RunResult.compared(stim, stripped, comparison)])
    assert evaluation.unbound == ("artifact", "domain")
    assert not evaluation.mismatches, "the agreement itself is perfect"
    assert evaluation.passed is False, "and unbound still withholds the pass"

    with pytest.raises(Refused, match="authority-evidence-unbound"):
        Authoritative(CONCRETE, evaluation=evaluation)

    from behave.sim.authority import PARTIAL
    from behave.sim.evaluation_policy import verdict

    got = verdict(evaluation)
    assert got.kind == PARTIAL and "bound to no" in got.why
    with pytest.raises(Refused, match="authority-no-score-under-this-kind"):
        got.passed


def test_a_compact_false_hit_vector_still_declares_its_complete_catalogue():
    """False compact bits may be omitted from iteration, not from shape checks."""
    from behave.sim.core.outcome import ConcreteEvaluation
    from behave.sim.core.run_record import RunResult
    from behave.sim.core.stimulus import AuditedTrace, SlotRecord

    task = api.admit(contract(), realization(), reference=_ref())
    stimulus = schedule([3])
    original = run_device(task, stimulus, Doubling())
    records = tuple(SlotRecord(
        accepted=dict(record.accepted),
        state=dict(record.state),
        environment=dict(record.environment),
        outputs=dict(record.outputs),
        hits=HitVector.from_names(("foreign.false",)),
        monitor=dict(record.monitor),
    ) for record in original.records)
    trace = AuditedTrace(
        stimulus,
        initial_state=dict(original.initial_state),
        records=records,
        completion=original.completion,
        domain=original.domain,
        artifact=original.artifact,
    )
    catalogue = api.CatalogueManifest((Goal(
        "declared.false", COVERAGE, family="test", provider="test",
        policy="compact-hit-shape/1",
    ),))

    with pytest.raises(Refused, match="evaluation-recorded-hit-outside"):
        ConcreteEvaluation(
            task,
            results=(RunResult.not_compared(
                stimulus, trace,
                api.RunFailure.verifier(
                    "test-comparison",
                    Refusal("shape-test", "shape test fixture"))),),
            catalogue=catalogue,
        )


@pytest.mark.parametrize("compact", (False, True))
def test_false_goals_are_validated_in_every_run_not_just_the_first(compact):
    from behave.sim.core.outcome import ConcreteEvaluation
    from behave.sim.core.run_record import RunResult
    from behave.sim.core.stimulus import AuditedTrace, SlotRecord

    task = api.admit(contract(), realization(), reference=_ref())
    catalogue = api.CatalogueManifest((Goal(
        "declared.false", COVERAGE, family="test", provider="test",
        policy="compact-hit-shape/1",
    ),))
    results = []
    for value, goal in ((3, "declared.false"), (4, "foreign.false")):
        stimulus = schedule([value])
        original = run_device(task, stimulus, Doubling())
        hits = HitVector.from_names((goal,)) if compact else {goal: False}
        records = tuple(SlotRecord(
            accepted=dict(record.accepted), state=dict(record.state),
            environment=dict(record.environment), outputs=dict(record.outputs),
            hits=hits, monitor=dict(record.monitor),
        ) for record in original.records)
        trace = AuditedTrace(
            stimulus, initial_state=dict(original.initial_state), records=records,
            completion=original.completion, domain=original.domain,
            artifact=original.artifact,
        )
        results.append(RunResult.not_compared(stimulus, trace,
            api.RunFailure.verifier("test-comparison", Refusal("shape-test", "shape test"))))

    assert ConcreteEvaluation(task, results[:1], catalogue=catalogue).hits == ()
    with pytest.raises(Refused, match="evaluation-recorded-hit-outside"):
        ConcreteEvaluation(task, results, catalogue=catalogue)


def test_an_empty_candidate_set_is_refused_not_scored():
    from behave.sim.authority import CONCRETE, Authoritative
    from behave.sim.core.outcome import ConcreteEvaluation

    task = api.admit(contract(), realization(), reference=_ref())
    evaluation = ConcreteEvaluation(task, results=[])
    with pytest.raises(Refused, match="authority-evidence-empty"):
        Authoritative(CONCRETE, evaluation=evaluation)


def test_the_result_version_moved_with_the_kinds():
    """Raw diagnostic observations and admitted goal evidence stay distinct."""
    from behave.sim.authority import RESULT_VERSION

    assert RESULT_VERSION == "authority/104"


def test_only_a_concrete_evaluation_carries_a_score():
    """Four things could speak last in the old chain, and the report named which in a
    field called `verdict_source`, so `passed` meant one of four statements."""
    from behave.sim.authority import CONCRETE, PARTIAL, REFUSED, Authoritative

    for kind in (REFUSED, PARTIAL, "heuristic_smoke_test"):
        result = Authoritative(kind, why="whatever")
        assert not result.authoritative
        assert "passed" not in result.as_dict()
        with pytest.raises(Refused, match="authority-no-score-under-this-kind"):
            result.passed

    task = api.admit(contract(), realization(), reference=_ref())
    got = _evaluate(task, [schedule([3])], Doubling(), reference=_ref())
    concrete = Authoritative(CONCRETE, evaluation=got)
    assert concrete.authoritative and concrete.as_dict()["passed"] is True


def test_the_four_outcomes_are_a_closed_set():
    """They were four module-level strings, so `result_kind` could hold anything a
    caller spelled and a consumer comparing against a literal had no way to be told it
    had the wrong one."""
    from behave.sim.authority import Authoritative, Outcome

    assert {o.value for o in Outcome} == {
        "concrete_evaluation", "refused", "partial", "heuristic_smoke_test"}
    assert [o for o in Outcome if o.authoritative] == [Outcome.CONCRETE]
    with pytest.raises(ValueError):
        Authoritative("verified")


def test_auxiliary_reset_probe_without_stimuli_is_unresolved(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact

    assert RTLArtifact("x.sv", "top", tmp_path).reset_audit(None, [])[0] == "unresolved"
