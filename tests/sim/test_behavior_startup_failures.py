"""Startup ownership and post-score construction through the public command."""

import json
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.behavior import construction, isolated
from behave.sim.core.failure import CandidateFault

from .test_behavior_public_exact_construction import (
    STRAIGHT_LINE_SOURCE,
    _contract,
    _generation,
)
from .test_command_handler import _BaselineOnly


def _failing_source(stage):
    if stage == "load":
        return '1 // 0\n' + STRAIGHT_LINE_SOURCE
    if stage == "constructor":
        return STRAIGHT_LINE_SOURCE.replace(
            "class M:", 'class M:\n    def __init__(self):\n'
            '        1 // 0\n')
    assert stage == "reset"
    return STRAIGHT_LINE_SOURCE.replace(
        "        pass", '        1 // 0')


def _reference(source):
    return api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source.encode("utf-8")}, config={"class": "M"})


def _request(tmp_path, *, source=STRAIGHT_LINE_SOURCE,
             reference_source=STRAIGHT_LINE_SOURCE, analysis=False):
    path = tmp_path / "candidate.py"
    path.write_text(source)
    contract = _contract()
    reference = _reference(reference_source)
    task = api.admit(
        contract,
        api.realization_of_behavior_source(source, contract, file="candidate.py"),
        reference=reference,
    )
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file="candidate.py", timeout_s=30),
        _generation(contract), bounded_analysis=analysis)
    return handle.command_request(
        run_directory=tmp_path / "command",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=(_BaselineOnly(),), probe_budget=0,
        run_analysis=analysis, reference=reference,
        analysis_wall_timeout_seconds=180 if analysis else None,
    )


@pytest.mark.isolation
@pytest.mark.parametrize("stage", ["load", "constructor", "reset"])
@pytest.mark.parametrize("analysis", [False, pytest.param(True, marks=pytest.mark.solver)])
def test_source_startup_failure_reaches_a_persisted_zero(tmp_path, stage, analysis):
    result = api.handle_command(_request(
        tmp_path, source=_failing_source(stage), analysis=analysis))
    # A proven candidate startup failure may stop before the remaining cases.
    assert result.decision.authoritative
    assert not result.evaluation.passed
    assert result.evaluation.candidate_failures
    assert not result.evaluation.no_result_failures
    record = json.loads(Path(result.record_path).read_text())
    assert record["authority"]["passed"] is False
    assert record["report"]["passed"] is False


@pytest.mark.isolation
@pytest.mark.parametrize("stage", ["load", "constructor", "reset"])
def test_reference_startup_failure_is_not_a_candidate_zero(tmp_path, stage):
    if stage in {"load", "constructor"}:
        with pytest.raises(api.Refused) as caught:
            _reference(_failing_source(stage))
        assert type(caught.value) is api.Refused
        assert caught.value.refusal.kind == "reference-model-would-not-start"
        assert caught.value.refusal.as_dict()["detail"]["cause"]["kind"] == (
            "candidate-load-failed" if stage == "load"
            else "candidate-construction-failed")
        return
    result = api.handle_command(_request(
        tmp_path, reference_source=_failing_source(stage)))
    assert not result.evaluation.complete
    assert not result.evaluation.candidate_failures
    assert result.evaluation.no_result_failures
    assert all(failure.origin is api.FailureOrigin.REFERENCE
               for _, failure in result.evaluation.no_result_failures)
    record = json.loads(Path(result.record_path).read_text())
    assert "passed" not in record["authority"]
    assert "passed" not in record["report"]


@pytest.mark.isolation
def test_worker_startup_bookkeeping_failure_is_not_a_candidate_zero(
        tmp_path, monkeypatch):
    request = _request(tmp_path)
    target = 'config = dict(first.get("config") or {})'
    assert isolated._CHILD.count(target) == 1
    monkeypatch.setattr(isolated, "_CHILD", isolated._CHILD.replace(
        target, 'raise RuntimeError("worker bookkeeping failed")'))
    result = api.handle_command(request)
    assert not result.evaluation.complete
    assert not result.evaluation.candidate_failures
    assert all(failure.origin is api.FailureOrigin.INFRASTRUCTURE
               for _, failure in result.evaluation.no_result_failures)


@pytest.mark.isolation
@pytest.mark.parametrize("analysis", [False, pytest.param(True, marks=pytest.mark.solver)])
def test_construction_authentication_belongs_after_score_persistence(
        tmp_path, monkeypatch, analysis):
    from behave.sim.result_store import ResultStore

    construction.clear_construction_cache()
    persisted = []
    calls = []
    decide = ResultStore.decided

    def observed_decision(store, authority):
        result = decide(store, authority)
        persisted.append(authority.as_dict())
        return result

    def unavailable(*_args, **_kwargs):
        assert persisted, "exact construction ran before score persistence"
        calls.append(True)
        raise construction.ConstructionUnavailable("injected exact-only failure")

    monkeypatch.setattr(ResultStore, "decided", observed_decision)
    monkeypatch.setattr(construction, "construction_facts", unavailable)
    result = api.handle_command(_request(tmp_path, analysis=analysis))
    assert result.evaluation.passed
    assert bool(calls) is analysis
    assert persisted
    if analysis:
        assert result.analysis.verdict is api.Verdict.REFUSED
    record = json.loads(Path(result.record_path).read_text())
    assert record["authority"]["passed"] is True


@pytest.mark.parametrize("record", [
    None,
    {"kind": "candidate-construction-failed", "why": "failed"},
    {"kind": "service-failed", "why": "failed", "detail": {"error": "RuntimeError"}},
    {"kind": "candidate-load-failed", "why": "failed", "detail": {"error": 1}},
    {"kind": "candidate-load-failed", "why": "failed", "detail": {
        "error": "RuntimeError", "passed": True}},
])
def test_startup_failure_channel_rejects_malformed_evidence(record):
    model = object.__new__(isolated.IsolatedModel)
    with pytest.raises(isolated.Broken):
        model._accept_greeting({"startup_failure": record})


def test_native_load_errors_keep_their_type_outside_attributed_replay():
    from behave.sim.behavior.instrumented import load_source

    with pytest.raises(ZeroDivisionError) as caught:
        load_source(_failing_source("load"))
    assert type(caught.value) is ZeroDivisionError
    with pytest.raises(CandidateFault) as caught:
        load_source(_failing_source("load"), _attribute_startup_failure=True)
    assert caught.value.refusal.kind == "candidate-load-failed"
