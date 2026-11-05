"""One wall-clock lifetime for post-score analysis, including preparation.

The budget limits work, not the bounded trace domain. Nested operations retain
the same deadline; exhaustion is never a solver answer or a concrete verdict.
"""

import math
import time
from contextlib import ExitStack, contextmanager

from behave.isolation.preparation_control import PreparationCancelled, preparation_scope

from .execution_control import ExecutionPhase, active_budget, budget_scope


class AnalysisStopped(PreparationCancelled):
    def __init__(self, budget):
        self.wall_seconds = budget.wall_seconds
        self.elapsed_seconds = time.monotonic() - budget.started
        super().__init__("analysis-budget-exhausted")


class AnalysisBudget:
    def __init__(self, seconds):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("analysis wall time must be finite and positive")
        self.wall_seconds = seconds
        self.started = time.monotonic()
        self.deadline = self.started + seconds
        self._resources = ExitStack()

    def on_close(self, callback, *args):
        """Release prepared resources even if construction never reaches analysis."""
        self._resources.callback(callback, *args)

    def remaining(self, seconds=None):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise AnalysisStopped(self)
        return left if seconds is None else min(seconds, left)

    def check(self):
        self.remaining()


def active_analysis():
    return active_budget(ExecutionPhase.ANALYSIS)


def check_analysis():
    budget = active_analysis()
    if budget is not None:
        budget.check()


def remaining_query_ms(milliseconds):
    budget = active_analysis()
    if budget is None:
        return milliseconds
    seconds = None if milliseconds is None else milliseconds / 1000
    # Several solver APIs interpret zero as unlimited.
    return max(1, int(budget.remaining(seconds) * 1000))


@contextmanager
def analysis_scope(wall_timeout_seconds=None):
    existing = active_analysis()
    if existing is not None:
        existing.check()
        yield existing
        return
    if wall_timeout_seconds is None:
        raise ValueError("analysis requires an explicit finite wall_timeout_seconds")
    budget = AnalysisBudget(wall_timeout_seconds)
    with budget_scope(ExecutionPhase.ANALYSIS, budget):
        with budget._resources, preparation_scope(budget.check):
            yield budget
