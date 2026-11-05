"""Directions divide the time that is actually available to the invocation."""

from types import SimpleNamespace

import pytest

from behave.sim import scoring_policy, search_policy
from behave.sim.scoring_policy import ScoringPolicy, ScoringStopped, scoring_scope
from behave.sim.search_policy import SearchBudget, SearchPolicy, remaining_timeout, search_scope


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    timer = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(search_policy, "time", timer)
    monkeypatch.setattr(scoring_policy, "time", timer)
    return clock


@pytest.mark.parametrize("baseline_s, outer_deadline, available", [
    (100, None, 600),
    (900, None, 300),
    (900, 1100, 200),
])
def test_time_directions_share_the_tighter_live_deadline(
        clock, baseline_s, outer_deadline, available):
    with scoring_scope(ScoringPolicy(1200), deadline=outer_deadline) as score:
        clock.now = baseline_s
        budget = SearchBudget(SearchPolicy(time_budget_s=600))
        first = budget.direction(2)
        assert first.report("test")["allocated_s"] == available / 2
        assert first.deadline == baseline_s + available / 2
        with search_scope(first):
            assert remaining_timeout(600) == available / 2
        # An early return transfers unused time, never renews either budget.
        clock.now += 25
        second = budget.direction(1)
        assert second.deadline == baseline_s + available
        assert second.report("test")["allocated_s"] == available - 25
        assert second.deadline <= score.deadline
        assert second.deadline <= budget.deadline


def test_every_later_direction_redivides_remaining_real_time(clock):
    with scoring_scope(ScoringPolicy(1200)):
        clock.now = 900
        budget = SearchBudget(SearchPolicy(time_budget_s=600))
        first = budget.direction(3)
        assert first.deadline == 1000
        clock.now = 925
        second = budget.direction(2)
        assert second.deadline == 1062.5
        clock.now = 1000
        assert budget.direction(1).deadline == 1200


def test_expiry_is_reported_inside_the_direction_not_during_allocation(clock):
    with scoring_scope(ScoringPolicy(10)):
        clock.now = 11
        lease = SearchBudget(SearchPolicy(time_budget_s=600)).direction(2)
        assert lease.deadline == 11
        assert lease.report("test")["allocated_s"] == 0
        with pytest.raises(ScoringStopped):
            lease.check()


def test_explicit_probe_only_experiment_keeps_no_direction_time_lease(clock):
    with scoring_scope(ScoringPolicy(1200)):
        clock.now = 900
        budget = SearchBudget(SearchPolicy(time_budget_s=None, max_probes=2))
        lease = budget.direction(2)
        assert lease.deadline is None
        with search_scope(lease):
            assert remaining_timeout(600) == 300


def test_search_outside_scoring_keeps_its_original_total_budget(clock):
    budget = SearchBudget(SearchPolicy(time_budget_s=600))
    assert budget.direction(2).deadline == 300
    clock.now = 100
    assert budget.direction(1).deadline == 600


def test_scheduler_leaves_time_for_the_second_direction_after_slow_baseline(clock):
    from .test_search_budget import TimedProvider, reports, run

    first = TimedProvider(clock, "first", probe_s=2)
    second = TimedProvider(clock, "second")
    with scoring_scope(ScoringPolicy(1200)):
        clock.now = 1196
        result = run([first, second], SearchPolicy(time_budget_s=600))
    a, b = reports(result)
    assert a["allocated_s"] == b["allocated_s"] == 2
    assert a["stop_reason"] == "time-budget-exhausted"
    assert first.received_timeout[0] == 2
    assert second.probes and b["probes_completed"] > 0
    assert (first.closed, second.closed) == (1, 1)
    assert [dict(stimulus.queues)["in"] for stimulus in result.stimuli] == [
        ((0,),), ((1,),), ((2,),)]
