"""An RPC deadline does not charge the verifier's idle time to Behavior."""

import time

import pytest

from behave.sim.behavior.isolated import Broken, IsolatedModel, Limits, _session_wall_seconds
from behave.sim.scoring_policy import ScoringPolicy, ScoringStopped, scoring_scope
from behave.sim.search_policy import SearchBudget, SearchPolicy, search_scope

SOURCE = """class M:
    def reset(self): pass
    def process(self, a): return {'y': a}
"""


def test_session_reuses_the_shared_remaining_budget(monkeypatch):
    now = [100.]
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    assert _session_wall_seconds(2) == 2
    with scoring_scope(ScoringPolicy(time_budget_s=30)):
        assert _session_wall_seconds(2) == 30
        lease = SearchBudget(SearchPolicy(time_budget_s=12)).direction(1)
        with search_scope(lease):
            assert _session_wall_seconds(2) == 12
            now[0] += 5.1
            assert _session_wall_seconds(2) == 7
        assert _session_wall_seconds(2) == 25
        now[0] = 130.
        with pytest.raises(ScoringStopped):
            _session_wall_seconds(2)


@pytest.mark.isolation
def test_model_can_wait_for_the_verifier_between_bounded_calls():
    with (scoring_scope(ScoringPolicy(time_budget_s=12)),
          IsolatedModel(SOURCE, cls="M", limits=Limits(seconds=10, wall_seconds=2)) as model):
        assert model.process(a=3) == {"y": 3}
        time.sleep(2.3)  # Work in the trusted parent, not a blocked model call.
        assert model.process(a=4) == {"y": 4}


@pytest.mark.isolation
def test_session_budget_keeps_the_independent_per_answer_cpu_limit():
    source = SOURCE.replace("return {'y': a}", "\n        while True: pass")
    with (scoring_scope(ScoringPolicy(time_budget_s=30)),
          IsolatedModel(source, cls="M", limits=Limits(seconds=2, wall_seconds=2)) as model, pytest.raises(Broken)):
        model.process(a=0)
