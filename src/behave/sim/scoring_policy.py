"""A scoring deadline stops work, not evidence collection.

Passing requires the complete mandatory baseline. Transaction checkpoints retain
progress and negative evidence, never permission to pass an incomplete baseline.
The scope includes preparation and search when installed before source preparation.
"""

import math
import time
from contextlib import contextmanager
from dataclasses import dataclass

from behave.core import runtime_defaults as defaults
from behave.isolation.preparation_control import preparation_scope

from .search_policy import SearchStopped
from .execution_control import ExecutionPhase, active_budget, budget_scope


@dataclass(frozen=True, slots=True)
class ScoringPolicy:
    time_budget_s: float = defaults.EVAL_SCORE_SECONDS
    transaction_checkpoints: bool = defaults.SIM_TRANSACTION_CHECKPOINTS

    def __post_init__(self):
        if type(self.transaction_checkpoints) is not bool:
            raise TypeError("transaction_checkpoints must be a boolean")
        if (type(self.time_budget_s) not in (int, float)
                or not math.isfinite(self.time_budget_s) or self.time_budget_s < 0):
            raise ValueError("scoring time must be finite and nonnegative")

    def as_dict(self):
        if self.transaction_checkpoints:
            return {"version": "scoring-policy/3", "time_budget_s": self.time_budget_s,
                    "boundary": "fully-checked-transaction-prefix", "empty": "no-result",
                    "pass_requires": "complete-baseline"}
        return {"version": "scoring-policy/3", "time_budget_s": self.time_budget_s,
                "boundary": "fully-checked-run", "empty": "no-result",
                "pass_requires": "complete-baseline"}


class ScoringStopped(SearchStopped):
    def __init__(self):
        super().__init__("scoring-budget-exhausted")
        # Trusted tool boundary metadata; never a substitute for a completed
        # replay or permission to decode a cancelled command as successful.
        self.interrupted_tool = None


class ScoringBudget:
    def __init__(self, policy, deadline=None):
        self.policy = policy
        self.started = time.monotonic()
        self.deadline = self.started + policy.time_budget_s
        if deadline is not None:
            self.deadline = min(self.deadline, deadline)
        self.finished = False
        self.cleanup_confirmed = True

    @property
    def exhausted(self):
        return not self.finished and time.monotonic() >= self.deadline

    def check(self):
        if self.exhausted:
            raise ScoringStopped()

    def remaining(self, seconds):
        if self.finished:
            return seconds
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ScoringStopped()
        return min(seconds, remaining)

    def receipt(self):
        return {"policy": self.policy.as_dict(),
                "allocated_work_s": max(0, self.deadline - self.started),
                "elapsed_s": time.monotonic() - self.started,
                "exhausted": self.exhausted, "cleanup_confirmed": self.cleanup_confirmed}

    def finish(self):
        if not self.cleanup_confirmed:
            from .core.failure import FailureRaised, RunFailure
            from .core.refusal import Refusal

            raise FailureRaised(RunFailure.infrastructure(
                "scoring-cleanup", Refusal("scoring-cleanup-unconfirmed",
                    "budget finalization cannot confirm worker cleanup")))
        self.finished = True


def active_scoring():
    return active_budget(ExecutionPhase.SCORING)


def check_scoring():
    budget = active_scoring()
    if budget is not None:
        budget.check()


@contextmanager
def scoring_scope(policy, *, deadline=None):
    """Share one deadline through preparation, search and concrete testing.

    ``deadline`` is an optional same-host monotonic deadline from the caller's
    supervisor. Nested operations cannot renew it. Finalization and optional
    analysis run after ``budget.finished`` and do not dispatch scoring work.
    """
    if type(policy) is not ScoringPolicy:
        raise TypeError("scoring requires ScoringPolicy")
    existing = active_scoring()
    if existing is not None:
        if existing.finished:
            raise ValueError("the scoring invocation has already settled")
        if existing.policy != policy:
            raise ValueError("nested scoring must retain the invocation policy")
        yield existing
        return
    budget = ScoringBudget(policy, deadline)
    with budget_scope(ExecutionPhase.SCORING, budget):
        with preparation_scope(budget.check):
            yield budget
