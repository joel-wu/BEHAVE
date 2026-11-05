"""Shared budget expiry must not become a backend/session refusal."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from behave.sim import analysis_budget, job_session, scoring_policy, search_policy
from behave.sim.job_isolation import IsolatedJobFailure


@pytest.fixture
def blocked_session(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    timer = SimpleNamespace(monotonic=lambda: clock.now)
    for module in (job_session, search_policy, scoring_policy, analysis_budget):
        monkeypatch.setattr(module, "time", timer)

    child = SimpleNamespace(closed=0)

    def close():
        child.closed += 1

    child.close = close

    class BlockedThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

        def is_alive(self):
            return not child.closed

        def join(self, seconds):
            clock.now += seconds

    monkeypatch.setattr(job_session, "threading", SimpleNamespace(Thread=BlockedThread))
    remaining = job_session.remaining_timeout

    delayed = False

    def interrupted_calculation(seconds):
        # A context switch after the caller's clock read, before calculating
        # the enclosing lifetime. Combining that old clock with this duration
        # invents an earlier deadline and mislabels cancellation as a fault.
        nonlocal delayed
        if not delayed:
            clock.now += 0.25
            delayed = True
        return remaining(seconds)

    monkeypatch.setattr(job_session, "remaining_timeout", interrupted_calculation)
    session = job_session.JobSession.__new__(job_session.JobSession)
    session._child, session._closed, session._cleanup_error = child, False, None
    session._serial, session._version, session._exchanges = 0, "test/1", None
    session.timings = []
    return session, child, clock


def _scope(kind):
    if kind == "search":
        budget = search_policy.SearchBudget(search_policy.SearchPolicy(time_budget_s=1))
        return search_policy.search_scope(budget.direction(1)), search_policy.SearchStopped
    if kind == "scoring":
        return (scoring_policy.scoring_scope(scoring_policy.ScoringPolicy(time_budget_s=1)),
                scoring_policy.ScoringStopped)
    if kind == "analysis":
        return analysis_budget.analysis_scope(1), analysis_budget.AnalysisStopped
    return nullcontext(), IsolatedJobFailure


@pytest.mark.parametrize("kind", ["search", "scoring", "analysis"])
def test_duration_conversion_cannot_precede_the_shared_deadline(blocked_session, kind):
    session, child, clock = blocked_session
    scope, stopped = _scope(kind)
    with scope, pytest.raises(stopped):
        session._exchange_admitted("probe", {}, 300)
    assert clock.now == pytest.approx(1)
    assert session.timings[0]["wall_ms"] == pytest.approx(1000)
    session.close()
    assert child.closed == 1


@pytest.mark.parametrize("kind", ["search", "scoring", "analysis", "none"])
def test_real_backend_timeout_remains_a_failure(blocked_session, kind):
    session, child, clock = blocked_session
    scope, _stopped = _scope(kind)
    with scope, pytest.raises(IsolatedJobFailure) as caught:
        session._exchange_admitted("probe", {}, 0.125)
    assert caught.value.how == "timeout"
    assert "0.125s" in str(caught.value)
    assert 0.125 <= clock.now < 1  # Own timeout, not the shared lifetime.
    session.close()
    assert child.closed == 1
