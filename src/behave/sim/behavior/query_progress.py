"""Provisional replay credit in the physical query's witness coordinates."""

from __future__ import annotations

from ..core.audit import WitnessInconsistent
from ..core.query import QueryCall, SolverAnswer
from ..core.query_feedback import QueryFeedback


class QueryProgress:
    """Keep replay credit across command cones without retaining solver models."""

    def __init__(self, feedback, projection=None):
        if type(feedback) is not QueryFeedback:
            raise TypeError("query progress requires core-owned audit feedback")
        self.feedback = feedback
        self.projection = projection
        self.reached = {}

    def projected(self, projection):
        if self.projection is not None:
            raise ValueError("a stateless query cannot be projected twice")
        return QueryProgress(self.feedback, projection)

    def observe(self, call):
        if call.answer is not SolverAnswer.SAT:
            return
        full = call
        if self.projection is not None:
            full = QueryCall(
                call.goals, call.answer,
                witness=self.projection.lift(call.witness),
                telemetry=call.telemetry, solver_calls=call.solver_calls)
        replayed = self.feedback.checkpoint(full)
        trace = call.witness
        for goal in replayed.hits & self.feedback.goals:
            self.reached.setdefault(goal, trace)

    def remaining(self, goals):
        return [goal for goal in goals if goal not in self.reached]

    def merge(self, reachable, unreachable, undecided, goals):
        credit = self.reached.keys() & set(goals)
        if credit & set(unreachable):
            raise WitnessInconsistent(
                "closure-query-transcript-unsat-refuted",
                "an exact UNSAT answer contradicts a replayed query witness",
                goals=sorted(credit & set(unreachable)))
        for goal in credit:
            reachable[goal] = self.reached[goal]
            undecided.pop(goal, None)
