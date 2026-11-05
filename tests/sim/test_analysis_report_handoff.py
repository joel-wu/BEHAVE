"""Production ordering and persistence for the concrete-to-analysis handoff."""

from __future__ import annotations

import json

import pytest

from behave.sim import analysis_service, api
from behave.sim.analysis_report import analyse_after_decision
from behave.sim.authority import CONCRETE, Authoritative
from behave.sim.core.outcome import AnalysisOutcome, Verdict
from behave.sim.core.refusal import Refusal
from behave.sim.result_store import ResultStore

from .test_api_path import Doubling, _evaluate, _ref, contract, realization, schedule


def _decided(tmp_path, value=3):
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    evaluation = _evaluate(
        task, [schedule([value])], Doubling(), reference=reference)
    authority = Authoritative(CONCRETE, evaluation=evaluation)
    store = ResultStore(tmp_path)
    store.begin()
    store.decided(authority)
    return store, authority, evaluation


def test_real_public_analysis_is_attached_after_the_score_is_decided(
        tmp_path, monkeypatch):
    """Production reaches the service exported as ``api.analyse`` with the persisted object itself.

    This call deliberately supplies no exact capability, so the coordinator returns its
    typed missing-capability refusal.  The concrete PASS remains the authority and the
    analysis is a separately bound report field.
    """
    store, authority, evaluation = _decided(tmp_path)
    before = store.standing_authority()
    called = []
    real = analysis_service.analyse

    def observed(got, *args, **kwargs):
        called.append((got, store.snapshot().terminal,
                       store.standing_authority()))
        return real(got, *args, **kwargs)

    monkeypatch.setattr(analysis_service, "analyse", observed)
    outcome = analyse_after_decision(store, authority, wall_timeout_seconds=60)

    assert type(outcome) is AnalysisOutcome
    assert outcome.verdict is Verdict.REFUSED
    assert outcome.evidence["refusal"]["kind"] == \
        "analysis-exact-instance-missing"
    assert called == [(evaluation, True, before)]
    assert evaluation.fingerprint == outcome.concrete
    assert store.standing_authority() == before

    record = json.loads(store.result_path.read_text())
    assert record["authority"] == before
    assert record["authority"]["passed"] is True
    assert record["report"]["passed"] is True
    assert record["report"]["analysis"] == outcome.as_dict()
    assert "passed" not in record["report"]["analysis"]

    # A hard-timeout observation arriving after both conclusions annotates the same
    # terminal envelope; it cannot regress either one.
    assert store.timeout_if_undecided(
        store.invocation, {"during": "later-smoke"}, timeout=5.0) == \
        (True, "decided")
    interrupted = json.loads(store.result_path.read_text())
    assert interrupted["authority"] == before
    assert interrupted["report"]["analysis"] == outcome.as_dict()


def test_analysis_cannot_run_before_or_rewrite_the_decision(tmp_path):
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    evaluation = _evaluate(
        task, [schedule([3])], Doubling(), reference=reference)
    authority = Authoritative(CONCRETE, evaluation=evaluation)
    store = ResultStore(tmp_path)
    store.begin()

    with pytest.raises(RuntimeError, match="only after"):
        analyse_after_decision(store, authority)

    store.decided(authority)
    outcome = analyse_after_decision(store, authority, wall_timeout_seconds=60)
    report = json.loads(store.result_path.read_text())["report"]

    # A later full report has to carry the exact attached outcome.  Omitting it or
    # substituting another analysis is not a report update.
    without = dict(report)
    without.pop("analysis")
    with pytest.raises(ValueError, match="may not replace or remove"):
        store.commit(report=without)
    changed = dict(report)
    changed["analysis"] = {**outcome.as_dict(), "why": "a different answer"}
    with pytest.raises(ValueError, match="may not replace or remove"):
        store.commit(report=changed)

    assert store.standing_authority()["passed"] is True


def test_coordinator_exception_is_a_typed_latched_analysis_refusal(
        tmp_path, monkeypatch):
    store, authority, evaluation = _decided(tmp_path)
    before = store.standing_authority()
    message = "solver adapter context " * 100 + "original solver failure"

    def broken(*_args, **_kwargs):
        raise RuntimeError(message)

    monkeypatch.setattr(analysis_service, "analyse", broken)
    outcome = analyse_after_decision(
        store, authority, feasibility_timeout_ms=17, query_timeout_ms=19,
        wall_timeout_seconds=60)

    assert outcome.verdict is Verdict.REFUSED
    assert outcome.concrete == evaluation.fingerprint
    assert outcome.evidence["refusal"]["kind"] == \
        "analysis-coordinator-failed"
    assert outcome.evidence["refusal"]["detail"]["error"] == "RuntimeError: " + message
    assert outcome.evidence["analysis_policy"] == {
        "feasibility_timeout_ms": 17,
        "query_timeout_ms": 19,
        "batch": None,
        "artifact_timeout_ms": None,
    }
    assert store.standing_authority() == before
    report = json.loads(store.result_path.read_text())["report"]
    assert report["analysis"] == outcome.as_dict()


def test_post_score_exact_builder_refusal_is_latched_without_rewriting_score(
        tmp_path):
    store, authority, evaluation = _decided(tmp_path)
    before = store.standing_authority()
    unavailable = Refusal(
        "rtl-task-exact-capability-unsupported",
        "this concrete task is outside the exact RTL subset")

    outcome = analyse_after_decision(
        store, authority, exact_refusal=unavailable, wall_timeout_seconds=60)

    assert outcome.verdict is Verdict.REFUSED
    assert outcome.evidence["refusal"] == unavailable.as_dict()
    assert outcome.concrete == evaluation.fingerprint
    assert store.standing_authority() == before
    record = json.loads(store.result_path.read_text())
    assert record["authority"] == before
    assert record["report"]["analysis"] == outcome.as_dict()


def test_post_score_dispatch_does_not_execute_an_unbound_analysis_object(tmp_path):
    store, authority, evaluation = _decided(tmp_path)
    before = store.standing_authority()
    calls = []

    class ForeignRequest:
        def analyse(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError('a foreign request must not execute')

    outcome = analyse_after_decision(
        store, authority, analysis_request=ForeignRequest(), wall_timeout_seconds=60)
    assert calls == []
    assert outcome.verdict is Verdict.REFUSED
    assert outcome.evidence['refusal']['kind'] == 'analysis-coordinator-failed'
    assert 'closed Behavior request' in outcome.evidence['refusal']['detail']['error']
    assert outcome.concrete == evaluation.fingerprint
    assert store.standing_authority() == before


def test_store_rejects_an_untyped_or_cross_evaluation_analysis(tmp_path):
    store, _authority, evaluation = _decided(tmp_path / "one", value=3)
    other_store, other_authority, _other = _decided(tmp_path / "two", value=4)
    other = analyse_after_decision(other_store, other_authority, wall_timeout_seconds=60)

    standing = store.standing_authority()
    report = json.loads(store.result_path.read_text())["report"]
    fabricated = {
        "verdict": "classified",
        "why": "",
        "per_goal": {},
        "evidence": {},
        "concrete": evaluation.fingerprint,
    }
    # Before the typed handoff was sealed, both calls accepted this exact shape: the
    # concrete fingerprint was real, but no ConditionalEvidence had ever existed.
    with pytest.raises(ValueError, match="only by attach_analysis"):
        store.commit(report={**report, "analysis": fabricated})
    with pytest.raises(ValueError, match="only by attach_analysis"):
        store._commit(report={**report, "analysis": fabricated})
    with pytest.raises(ValueError, match="only by attach_analysis"):
        store.commit(report={**report, "analysis": other.as_dict()})
    with pytest.raises(ValueError, match="different concrete evaluation"):
        store.attach_analysis(other)
    with pytest.raises(TypeError, match="exact typed AnalysisOutcome"):
        store.attach_analysis(other.as_dict())

    forged = {**other.as_dict(), "passed": False}
    with pytest.raises(ValueError, match="only by attach_analysis"):
        store.commit(report={**report, "analysis": forged})
    assert store.standing_authority() == standing
