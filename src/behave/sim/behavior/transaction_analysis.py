"""Post-score exact binding for transaction-step Behavior sources.

Preparation freezes the source catalogue needed by generation and concrete replay.
Representation planning and solver construction remain behind :meth:`bind`, which the
common command invokes only after persisting the concrete score.
"""

from __future__ import annotations

from ..core.admission import AdmittedTask
from ..core.catalogue import CatalogueManifest
from ..core.frozen import Frozen
from ..core.refusal import Refusal, Refused
from ..core.stimulus import Stimulus
from ..stimulus_set import GenerationSpec
from .exact_service import (
    BehaviorExactJobContext,
    BehaviorIsolatedAnalysisInstance,
)

__all__ = ["BehaviorAnalysisRequest", "prepare_behavior_analysis_request"]


class BehaviorAnalysisRequest(Frozen):
    """Frozen ordinary-Behavior inputs for ancillary bounded analysis."""

    __slots__ = (
        "_analysis_bound", "context", "frontend_catalogue", "generation_spec",
        "goal_capability", "goal_policy", "task",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("BehaviorAnalysisRequest is a closed capability record")

    def __init__(self, task, context, generation_spec, frontend_catalogue,
                 goal_policy, goal_capability, *, analysis_bound=None):
        from ..core.goal_provider import GoalCapabilityUse, GoalPolicy

        if type(task) is not AdmittedTask:
            raise TypeError("a Behavior analysis request needs an AdmittedTask")
        if type(context) is not BehaviorExactJobContext \
                or context.task is not task or len(context.domains) >= 2 \
                or context.clock_schedule is not None:
            raise TypeError(
                "an ordinary Behavior request needs its unscheduled source context")
        if type(generation_spec) is not GenerationSpec:
            raise TypeError(
                "a Behavior analysis request needs one frozen GenerationSpec")
        # One ordinary Behavior step is an accepted input batch, not a clock
        # slot. Reset/drain and the RTL horizon must not truncate that count.
        if analysis_bound is None:
            analysis_bound = generation_spec.queue_bound
        if type(analysis_bound) is not int or isinstance(analysis_bound, bool) \
                or not 1 <= analysis_bound <= generation_spec.queue_bound:
            raise ValueError(
                "an ordinary Behavior analysis bound must be a positive transaction-"
                "batch count no larger than GenerationSpec.queue_bound")
        if type(frontend_catalogue) is not CatalogueManifest:
            raise TypeError(
                "a Behavior analysis request needs one frozen frontend catalogue")
        if type(goal_policy) is not GoalPolicy \
                or type(goal_capability) is not GoalCapabilityUse \
                or goal_policy.manifest is not goal_capability.source_catalogue \
                or goal_policy.digest() != goal_capability.policy_digest:
            raise TypeError(
                "a Behavior analysis request needs one frozen exact goal handoff")
        self.task = task
        self.context = context
        self.generation_spec = generation_spec
        self._analysis_bound = analysis_bound
        self.frontend_catalogue = frontend_catalogue
        self.goal_policy = goal_policy
        self.goal_capability = goal_capability
        self._freeze()

    @property
    def analysis_bound(self):
        return self._analysis_bound

    def _memory_for(self, stimuli):
        stimuli = tuple(stimuli)
        if any(type(stimulus) is not Stimulus for stimulus in stimuli):
            raise TypeError("an exact Behavior pool contains only Stimulus records")
        for stimulus in stimuli:
            faults = stimulus.faults_against(self.task.contract)
            if faults:
                raise Refused(Refusal(
                    "behavior-analysis-stimulus-outside-contract",
                    "Behavior analysis cannot consume a stimulus outside the "
                    "admitted task",
                    first=faults[0], count=len(faults)))
        from .memory_binding import memory_environment_for_stimuli

        memory_environment = memory_environment_for_stimuli(
            self.task.contract, stimuli, self.generation_spec.horizon)
        return memory_environment

    def bind(self, stimuli):
        """Construct the diagnostic operation proxy for this frozen request."""

        memory_environment = self._memory_for(stimuli)
        instance = BehaviorIsolatedAnalysisInstance(
            self.context, self.analysis_bound,
            goal_policy=self.goal_policy,
            goal_capability=self.goal_capability,
            memory_environment=memory_environment,
            memory_horizon=(
                self.generation_spec.horizon
                if memory_environment is not None else None))
        if instance.frontend_catalogue_manifest() != self.frontend_catalogue:
            raise Refused(Refusal(
                "behavior-analysis-catalogue-changed",
                "post-score binding changed the frozen Behavior frontend catalogue"))
        return instance.bound()

    def analyse(self, evaluation, *, feasibility_timeout_ms=None,
                query_timeout_ms=None, batch=None):
        """Run one complete post-score analysis in one exact judge job."""

        from ..core.outcome import ConcreteEvaluation

        if type(evaluation) is not ConcreteEvaluation \
                or evaluation.task is not self.task:
            raise TypeError(
                "a Behavior analysis request consumes its task's concrete evaluation")
        memory_environment = self._memory_for(evaluation.stimuli)
        return self.context.analyse(
            evaluation, self.analysis_bound,
            goal_policy=self.goal_policy, goal_capability=self.goal_capability,
            memory_environment=memory_environment,
            memory_horizon=(
                self.generation_spec.horizon
                if memory_environment is not None else None),
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms, batch=batch)

    def as_dict(self):
        return {
            "kind": "ordinary",
            "admission": self.task.handoff_identity,
            "task_contract": self.task.contract.digest,
            "artifact": self.task.realization.identity,
            "generation_spec": self.generation_spec.as_dict(),
            "analysis_bound": self.analysis_bound,
            "analysis_bound_unit": "transaction-batches",
            "frontend_catalogue": self.frontend_catalogue.digest(),
            "goal_policy": self.goal_policy.digest(),
            "goal_capability_use": self.goal_capability.as_dict(),
        }


def prepare_behavior_analysis_request(
        context, generation_spec, *, analysis_bound=None):
    """Freeze a post-score request without constructing a bounded encoding."""

    if type(context) is not BehaviorExactJobContext \
            or len(context.domains) >= 2:
        raise TypeError(
            "ordinary Behavior preparation needs a transaction-step context")
    if type(generation_spec) is not GenerationSpec:
        raise TypeError("ordinary Behavior preparation needs a GenerationSpec")
    frontend, policy, capability = context.source_goal_handoff()
    return BehaviorAnalysisRequest(
        context.task, context, generation_spec, frontend, policy, capability,
        analysis_bound=analysis_bound)
