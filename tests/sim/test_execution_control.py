"""Common execution control composes, but does not create, phase budgets."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from contextvars import copy_context
from itertools import permutations
import subprocess
import sys
from types import SimpleNamespace

import pytest

from behave.sim import execution_control as control


class Budget:
    def __init__(self, name, seconds, calls):
        self.name, self.seconds, self.calls = name, seconds, calls

    def check(self):
        self.calls.append((self.name, "check"))

    def remaining(self, seconds):
        self.calls.append((self.name, "remaining", seconds))
        return min(seconds, self.seconds)


def test_control_import_does_not_load_phase_policies_or_solver():
    subprocess.run([sys.executable, "-c", """
import sys
from behave.sim import execution_control
for name in ('search_policy', 'scoring_policy', 'analysis_budget', 'generation',
             'evaluation', 'analysis_service'):
    assert 'behave.sim.' + name not in sys.modules, name
assert 'z3' not in sys.modules
"""], check=True, capture_output=True, text=True, timeout=20)


def test_old_search_names_are_only_compatibility_aliases():
    from behave.sim import search_policy

    assert search_policy.check_search is control.check_execution
    assert search_policy.remaining_timeout is control.remaining_timeout


@pytest.mark.parametrize("order", tuple(permutations(control.ExecutionPhase)))
def test_all_active_budgets_compose_in_policy_order_not_scope_order(order):
    calls = []
    budgets = {phase: Budget(phase.value, seconds, calls)
               for phase, seconds in zip(control.ExecutionPhase, (30, 20, 10))}
    with ExitStack() as scopes:
        for phase in order:
            scopes.enter_context(control.budget_scope(phase, budgets[phase]))
        control.check_execution()
        assert calls == [(phase.value, "check") for phase in control.ExecutionPhase]
        calls.clear()
        assert control.remaining_timeout(40) == 10
        assert calls == [("analysis", "remaining", 40),
                         ("scoring", "remaining", 30),
                         ("search", "remaining", 20)]
    assert all(control.active_budget(phase) is None for phase in control.ExecutionPhase)


def test_phase_override_restores_after_failure_without_disabling_other_phases():
    analysis, search = control.ExecutionPhase.ANALYSIS, control.ExecutionPhase.SEARCH
    outer, inner, lease = (Budget(name, 10, []) for name in ("outer", "inner", "search"))
    with control.budget_scope(analysis, outer), control.budget_scope(search, lease):
        with pytest.raises(RuntimeError, match="failed"):
            with control.budget_scope(analysis, inner), control.budget_scope(search, None):
                assert control.active_budget(analysis) is inner
                assert control.active_budget(search) is None
                raise RuntimeError("failed")
        assert control.active_budget(analysis) is outer
        assert control.active_budget(search) is lease
    assert control.active_budget(analysis) is control.active_budget(search) is None


def test_worker_context_inherits_owners_without_leaking_its_override():
    phase = control.ExecutionPhase.ANALYSIS
    parent, child = Budget("parent", 10, []), Budget("child", 5, [])

    def work():
        assert control.active_budget(phase) is parent
        with control.budget_scope(phase, child):
            assert control.active_budget(phase) is child
        return control.active_budget(phase)

    with control.budget_scope(phase, parent), ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(copy_context().run, work).result(timeout=5) is parent
        assert control.active_budget(phase) is parent
        # Context propagation is explicit; the executor thread is not left bound.
        assert pool.submit(control.active_budget, phase).result(timeout=5) is None
    assert control.active_budget(phase) is None


@pytest.mark.parametrize("phases, exception_name", [
    (("analysis", "scoring", "search"), "AnalysisStopped"),
    (("scoring", "search"), "ScoringStopped"),
    (("search",), "SearchStopped"),
])
@pytest.mark.parametrize("operation", [control.check_execution, lambda: control.remaining_timeout(50)])
def test_simultaneous_expiry_keeps_original_exception_precedence(
        monkeypatch, phases, exception_name, operation):
    from behave.sim import analysis_budget, scoring_policy, search_policy

    now = [0.0]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    for module in (analysis_budget, scoring_policy, search_policy):
        monkeypatch.setattr(module, "time", clock)
    lease = search_policy.SearchBudget(search_policy.SearchPolicy(time_budget_s=1)).direction(1)
    scopes = {
        "analysis": lambda: analysis_budget.analysis_scope(1),
        "scoring": lambda: scoring_policy.scoring_scope(scoring_policy.ScoringPolicy(time_budget_s=1)),
        "search": lambda: search_policy.search_scope(lease),
    }
    expected = {"AnalysisStopped": analysis_budget.AnalysisStopped,
                "ScoringStopped": scoring_policy.ScoringStopped,
                "SearchStopped": search_policy.SearchStopped}[exception_name]
    with ExitStack() as stack:
        for name in reversed(phases):
            stack.enter_context(scopes[name]())
        now[0] = 2.0
        with pytest.raises(expected) as error:
            operation()
        assert type(error.value) is expected


def test_count_only_search_retains_wall_clamp_and_explicit_cancellation_rules():
    from behave.sim import search_policy

    lease = search_policy.SearchBudget(
        search_policy.SearchPolicy(time_budget_s=None, max_probes=2)).direction(1)
    with search_policy.search_scope(lease):
        lease.cancel("owner-stopped")
        assert control.remaining_timeout(7) == 7
        with pytest.raises(search_policy.SearchStopped, match="owner-stopped"):
            control.check_execution()
