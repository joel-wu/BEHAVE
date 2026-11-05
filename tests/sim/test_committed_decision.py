"""Public score reads use the latched decision, never a second evaluation."""

import json
from pathlib import Path

import pytest

from behave.sim import api, result_store
from behave.sim.authority import REFUSED, Authoritative
from behave.sim.core.outcome import ConcreteEvaluation

from .test_api_path import Doubling
from .test_command_handler import _request


@pytest.mark.parametrize("broken", (False, True))
def test_public_decision_is_the_committed_value(tmp_path, monkeypatch, broken):
    class Broken(Doubling):
        def settle(self, _presented, _controls):
            raise RuntimeError("candidate failed")

    got = api.handle_command(_request(
        tmp_path, artifact=Broken() if broken else None, run_analysis=False))
    decision = got.decision
    assert type(decision) is api.CommittedDecision
    record = json.loads(Path(got.record_path).read_text())
    expected = {key: value for key, value in record["authority"].items()
                if key != "evaluation"}

    def forbidden(*args, **kwargs):
        raise AssertionError("a decision read must not reopen or rescore evidence")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(ConcreteEvaluation, "passed", property(forbidden))
    monkeypatch.setattr(ConcreteEvaluation, "as_evidence_dict", forbidden)
    assert decision.authoritative is True
    assert decision.passed is (not broken)
    assert decision.invocation == got.invocation
    assert decision.record_path == got.record_path
    assert decision.as_dict()["authority"] == expected
    assert decision.evaluation_fingerprint == expected["evaluation_fingerprint"]
    assert bool(decision.failures) is broken
    if broken:
        assert decision.failures[0][1].origin is api.FailureOrigin.CANDIDATE
    with pytest.raises(AttributeError):
        decision.invocation = "another-run"
    with pytest.raises(TypeError):
        decision.authority["passed"] = True
    image = decision.as_dict()
    image["authority"]["passed"] = "mutated"
    assert decision.passed is (not broken)


def test_uncommitted_passing_evaluation_has_no_decision(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("primary record write failed")

    monkeypatch.setattr(result_store.ResultStore, "decided", broken)
    got = api.handle_command(_request(tmp_path, run_analysis=False))
    assert got.evaluation.passed
    assert got.status is api.CommandStatus.INFRASTRUCTURE_FAILURE
    assert got.decision is None


def test_derived_view_failure_preserves_primary_decision(tmp_path, monkeypatch):
    original = result_store.ResultStore._advance_shared

    def broken(self, envelope):
        if envelope.terminal:
            raise OSError("derived view write failed after primary commit")
        return original(self, envelope)

    monkeypatch.setattr(result_store.ResultStore, "_advance_shared", broken)
    got = api.handle_command(_request(tmp_path, run_analysis=False))
    assert got.status is api.CommandStatus.EVALUATED
    assert got.decision.authoritative and got.decision.passed
    assert got.failure
    assert json.loads(Path(got.record_path).read_text())["authority"]["passed"]


def test_no_result_decision_is_not_candidate_zero(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("generation service broke")

    monkeypatch.setattr("behave.sim.generation.generate_stimuli", broken)
    got = api.handle_command(_request(tmp_path, run_analysis=False))
    assert got.decision.kind == "partial"
    assert got.decision.authoritative is False
    assert got.decision.passed is None


def test_refusal_latches_and_in_progress_has_no_decision(tmp_path):
    store = result_store.ResultStore(tmp_path)
    assert store.committed_decision() is None
    store.begin()
    assert store.committed_decision() is None
    store.decided(Authoritative(REFUSED, why="first refusal"))
    before = store.committed_decision().as_dict()
    store.decided(Authoritative(REFUSED, why="later proposal"))
    assert store.committed_decision().as_dict() == before
    assert store.committed_decision().passed is None
    with pytest.raises(TypeError, match="CommandResult"):
        api.CommittedDecision(store.snapshot(), store.result_path)


def test_summary_never_traverses_the_evaluation(tmp_path, monkeypatch):
    store = result_store.ResultStore(tmp_path)
    (tmp_path / "command").mkdir()
    got = api.handle_command(_request(tmp_path / "command", run_analysis=False))
    from behave.sim.authority import CONCRETE

    store.decided(Authoritative(CONCRETE, evaluation=got.evaluation))
    evidence = store.snapshot().authority["evaluation"]
    original = result_store._plain

    def guarded(value):
        assert value is not evidence
        assert value is not store.snapshot().authority
        return original(value)

    monkeypatch.setattr(result_store, "_plain", guarded)
    assert store.standing_authority_summary()["passed"] is True
    assert store.committed_decision().passed is True
