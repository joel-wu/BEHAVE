"""Cancellation before a launch is not evidence of a live orphan worker."""

import io
from types import SimpleNamespace

import pytest

from behave.isolation import IsolationRefusal, IsolationUnavailable, linux
from behave.sim import job_isolation, job_session
from behave.sim.scoring_policy import ScoringPolicy, scoring_scope
from behave.sim.search_policy import SearchStopped
from tests.isolation.test_policy import _spec


def test_source_verification_cancellation_does_not_poison_scoring(monkeypatch):
    failure = SearchStopped("time-budget-exhausted")
    available = linux._backend_requirements()
    monkeypatch.setattr(linux, "_backend_requirements", lambda: available)
    monkeypatch.setattr(job_session, "_freeze_inputs", lambda *a: ({}, ()))
    monkeypatch.setattr(job_session, "_job_spec", lambda *a, **kw: _spec())

    def cancel(*args, **kwargs):
        raise failure

    def no_process(*args, **kwargs):
        pytest.fail("cancelled source verification must not launch a process")

    monkeypatch.setattr(linux, "_verify_source", cancel)
    monkeypatch.setattr(linux.subprocess, "Popen", no_process)
    with scoring_scope(ScoringPolicy(60)) as budget:
        with pytest.raises(SearchStopped) as caught:
            job_session.JobSession("echo", {}, module="fixture", version="fixture/1",
                                   timeout_s=1, wall_s=1, exchanges=None)
        assert caught.value is failure
        assert budget.cleanup_confirmed
        budget.finish()


@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("kind", ["session", "named-job"])
def test_only_owner_confirmed_cancellation_releases_resources(monkeypatch, confirmed, kind):
    failure = SearchStopped("time-budget-exhausted")
    failure.cleanup_confirmed = confirmed

    def cancel(*args, **kwargs):
        raise failure

    module = job_session if kind == "session" else job_isolation
    monkeypatch.setattr(module, "_freeze_inputs", lambda *a: ({}, ()))
    monkeypatch.setattr(module, "_job_spec", lambda *a, **kw: _spec())
    monkeypatch.setattr(module, "start" if kind == "session" else "run", cancel)
    with scoring_scope(ScoringPolicy(60)) as budget:
        with pytest.raises(SearchStopped) as caught:
            if kind == "session":
                module.JobSession("echo", {}, module="fixture", version="fixture/1",
                                  timeout_s=1, wall_s=1, exchanges=None)
            else:
                module.run_isolated_job("echo", {}, timeout_s=1)
        assert caught.value is failure
        assert budget.cleanup_confirmed == confirmed


@pytest.mark.parametrize("confirmed", [False, True])
def test_run_cancellation_reports_actual_cleanup(monkeypatch, confirmed):
    failure = SearchStopped("time-budget-exhausted")

    def cancel(*args, **kwargs):
        raise failure

    def close(**kwargs):
        if not confirmed:
            raise linux.IsolationCleanupError("test owner was not reaped")

    process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO(),
                              wait=cancel, close=close)
    monkeypatch.setattr(linux, "start", lambda *a, **kw: process)
    with pytest.raises(SearchStopped) as caught:
        linux.run(_spec())
    assert caught.value is failure
    assert failure.cleanup_confirmed == confirmed
    if not confirmed:
        assert "test owner was not reaped" in failure.__notes__[0]


@pytest.mark.parametrize("confirmed", [False, True])
def test_session_launch_refusal_retains_cleanup_evidence(monkeypatch, confirmed):
    detail = {} if confirmed else {"cleanup": {"state": "unconfirmed", "why": "test wait failed"}}
    refusal = IsolationUnavailable(IsolationRefusal("test-launch", "original setup failure", detail))

    def refuse(*args, **kwargs):
        raise refusal

    monkeypatch.setattr(job_session, "_freeze_inputs", lambda *a: ({}, ()))
    monkeypatch.setattr(job_session, "_job_spec", lambda *a, **kw: _spec())
    monkeypatch.setattr(job_session, "start", refuse)
    with scoring_scope(ScoringPolicy(60)) as budget:
        with pytest.raises(job_isolation.IsolatedJobFailure) as caught:
            job_session.JobSession("echo", {}, module="fixture", version="fixture/1",
                                   timeout_s=1, wall_s=1, exchanges=None)
        assert caught.value.code == "test-launch"
        assert budget.cleanup_confirmed == confirmed
