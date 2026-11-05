"""Symbolic conditions for the very same source invocations as native callbacks.

This is an encoding handoff, not a second catalogue. Declarations stay on
SourcePlan. Missing compiler bindings remain missing; they are not unreachable
goals. Implicit-process interval histories are not represented by these guards.
"""

from dataclasses import dataclass

from ..core.refusal import Refusal
from .control_condition import ControlCondition, triggered
from .source_plan import SourcePlan


@dataclass(frozen=True, slots=True)
class SourceEncoding:
    plan: SourcePlan
    conditions: tuple[ControlCondition | None, ...]
    # Native physical coordinates -> the source plan's invocation indices.
    invocations: tuple[int, ...]

    def predicate_for(self, event, explorer, *, active_events=None):
        import z3

        if not self.plan.enumerate_iterations:
            return Refusal("rtl-source-iterations-unencoded",
                           "native source occurrences do not encode individual loop iterations")
        if self.plan.declarations is not None and self.plan.declarations.failures:
            return Refusal("rtl-source-failure-transition-unencoded",
                           "source failure actions and subsequent control need an explicit step relation")
        condition = self.conditions[event]
        if condition is None:
            return Refusal("rtl-source-event-unencoded",
                           "this source event has no complete compiler control binding", event=event)
        if any(edge == "always" for edge, _ in condition.triggers):
            return Refusal("rtl-source-event-interval-unsupported",
                           "a settled predicate does not encode combinational activation history", event=event)
        if not triggered(condition.triggers, active_events):
            return z3.BoolVal(False)
        return condition.predicate(explorer, target=f"source-event:{event}")
