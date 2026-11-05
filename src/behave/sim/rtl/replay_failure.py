"""Execution purpose owns budget disposition; observation does not."""

import math
from enum import Enum

from ..core.failure import FailureDisposition, RunFailure
from ..core.refusal import Refusal

RTL_REPLAY_BUDGET_POLICY = "rtl-replay-budget/2"


class ReplayPurpose(Enum):
    CONCRETE = "concrete"
    ANALYSIS = "analysis"


def replay_seconds(purpose: ReplayPurpose, standalone_seconds: int) -> int:
    """A window-scored run uses the invocation's remaining time, not a second cap.

    Standalone replay and exact-analysis diagnostics use the ordinary public
    tool timeout. The tool supervisor also clamps to search/analysis and
    scoring deadlines and confirms cleanup before propagating a normal stop.
    """
    from ..scoring_policy import active_scoring

    scoring = active_scoring()
    if purpose is ReplayPurpose.CONCRETE and scoring is not None and not scoring.finished:
        return max(1, math.ceil(scoring.remaining(scoring.policy.time_budget_s)))
    return standalone_seconds


def budget_failure(purpose: ReplayPurpose, phase: str, reason: Refusal) -> RunFailure:
    """A concrete budget determines zero; an analysis budget determines no score."""
    if type(purpose) is not ReplayPurpose or phase not in {"build", "run"}:
        raise TypeError("a replay budget needs an explicit purpose and build/run phase")
    concrete = purpose is ReplayPurpose.CONCRETE
    return RunFailure.resource(
        f"candidate-{phase}" if concrete else f"rtl-analysis-{phase}", reason,
        policy=RTL_REPLAY_BUDGET_POLICY,
        disposition=(FailureDisposition.SCORE_ZERO if concrete else FailureDisposition.NO_RESULT))
