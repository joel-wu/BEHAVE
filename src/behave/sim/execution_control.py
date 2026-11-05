"""Compose active execution lifetimes without owning their policies.

Phase services create budgets, choose exceptions and release their resources.
Workers, replay and CPU admission only check or clamp those active lifetimes.
No duration, solver limit or scheduler is introduced here. The check order is
intentional: Analysis, Scoring, then Search preserves existing stop attribution.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from typing import Protocol


class ExecutionPhase(Enum):
    ANALYSIS = "analysis"
    SCORING = "scoring"
    SEARCH = "search"


class ExecutionBudget(Protocol):
    def check(self) -> None: ...

    def remaining(self, seconds: float) -> float: ...


# Independent variables preserve each phase's nesting/restoration and thread
# context inheritance. The common layer is their only storage owner.
_ACTIVE = {
    phase: ContextVar(f"behave_{phase.value}_budget", default=None)
    for phase in ExecutionPhase
}


def active_budget(phase: ExecutionPhase) -> ExecutionBudget | None:
    return _ACTIVE[phase].get()


@contextmanager
def budget_scope(phase: ExecutionPhase, budget: ExecutionBudget | None):
    """Bind this phase only; None temporarily removes only its own lifetime."""
    slot = _ACTIVE[phase]
    token = slot.set(budget)
    try:
        yield
    finally:
        slot.reset(token)


def check_execution():
    for phase in ExecutionPhase:
        budget = active_budget(phase)
        if budget is not None:
            budget.check()


def remaining_timeout(seconds):
    """Intersect the backend's existing limit with every active lifetime."""
    for phase in ExecutionPhase:
        budget = active_budget(phase)
        if budget is not None:
            seconds = budget.remaining(seconds)
    return seconds
