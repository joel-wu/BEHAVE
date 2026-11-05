"""Post-score public binding for exact atomic Behavior analysis.

The request freezes the admitted task, source/construction context, generation policy
and frontend catalogue before scoring.  Only :meth:`bind` consumes the concrete
stimulus pool: it derives the one authenticated ``GlobalSchedule`` and constructs the
solver-backed proxy in the existing named Behavior analysis job.  No operation here
changes, wraps, or interprets the concrete score.
"""

from __future__ import annotations

from ..core.admission import AdmittedTask
from ..core.catalogue import CatalogueManifest
from ..core.clock_schedule import unique_global_schedule
from ..core.frozen import Frozen
from ..core.refusal import Refusal, Refused
from ..core.stimulus import Stimulus
from ..stimulus_set import GenerationSpec
from .exact_service import (
    BehaviorExactJobContext,
    BehaviorIsolatedAnalysisInstance,
)

__all__ = [
    "BehaviorAtomicAnalysisRequest",
    "prepare_atomic_analysis_request",
]


class BehaviorAtomicAnalysisRequest(Frozen):
    """Immutable inputs for one schedule-bound ancillary analysis."""

    __slots__ = (
        "context", "frontend_catalogue", "generation_spec",
        "goal_capability", "goal_policy", "task",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError(
            "BehaviorAtomicAnalysisRequest is a closed capability record")

    def __init__(self, task, context, generation_spec, frontend_catalogue,
                 goal_policy, goal_capability):
        if type(task) is not AdmittedTask:
            raise TypeError("an atomic Behavior request needs an AdmittedTask")
        if type(context) is not BehaviorExactJobContext \
                or context.task is not task or len(context.domains) < 2 \
                or context.clock_schedule is not None:
            raise TypeError(
                "an atomic Behavior request needs its unscheduled source context")
        if type(generation_spec) is not GenerationSpec:
            raise TypeError(
                "an atomic Behavior request needs one frozen GenerationSpec")
        if type(frontend_catalogue) is not CatalogueManifest:
            raise TypeError(
                "an atomic Behavior request needs one frozen frontend catalogue")
        from ..core.goal_provider import GoalCapabilityUse, GoalPolicy

        if type(goal_policy) is not GoalPolicy \
                or type(goal_capability) is not GoalCapabilityUse \
                or goal_policy.manifest is not goal_capability.source_catalogue \
                or goal_policy.digest() != goal_capability.policy_digest:
            raise TypeError(
                "an atomic Behavior request needs one frozen exact goal handoff")
        self.task = task
        self.context = context
        self.generation_spec = generation_spec
        self.frontend_catalogue = frontend_catalogue
        self.goal_policy = goal_policy
        self.goal_capability = goal_capability
        self._freeze()

    def _run_context(self, stimuli):
        stimuli = tuple(stimuli)
        if any(type(stimulus) is not Stimulus for stimulus in stimuli):
            raise TypeError("an exact schedule pool contains only Stimulus records")
        schedules = []
        for stimulus in stimuli:
            faults = stimulus.faults_against(self.task.contract)
            if faults:
                raise Refused(Refusal(
                    "behavior-atomic-schedule-stimulus-outside-contract",
                    "atomic Behavior analysis cannot consume a stimulus outside "
                    "the admitted task",
                    first=faults[0], count=len(faults)))
            if stimulus.clock_schedule is None:
                raise Refused(Refusal(
                    "behavior-atomic-exact-schedule-unbound",
                    "atomic Behavior analysis needs each concrete stimulus's "
                    "canonical GlobalSchedule"))
            schedules.append(stimulus.clock_schedule)
        schedule = unique_global_schedule(
            schedules, horizon=self.generation_spec.horizon)
        from .memory_binding import memory_environment_for_stimuli

        memory_environment = memory_environment_for_stimuli(
            self.task.contract, stimuli, self.generation_spec.horizon)
        context = self.context.for_schedule(schedule)
        return context, memory_environment

    def bind(self, stimuli):
        """Construct the diagnostic operation proxy for this frozen request."""

        context, memory_environment = self._run_context(stimuli)
        instance = BehaviorIsolatedAnalysisInstance(
            context, self.generation_spec.horizon,
            goal_policy=self.goal_policy,
            goal_capability=self.goal_capability,
            memory_environment=memory_environment,
            memory_horizon=(
                self.generation_spec.horizon
                if memory_environment is not None else None))
        if instance.frontend_catalogue_manifest() != self.frontend_catalogue:
            raise Refused(Refusal(
                "behavior-atomic-analysis-catalogue-changed",
                "schedule binding changed the frozen atomic frontend catalogue"))
        return instance.bound()

    def analyse(self, evaluation, *, feasibility_timeout_ms=None,
                query_timeout_ms=None, batch=None):
        """Run one complete schedule-bound analysis in one exact judge job."""

        from ..core.outcome import ConcreteEvaluation

        if type(evaluation) is not ConcreteEvaluation \
                or evaluation.task is not self.task:
            raise TypeError(
                "an atomic Behavior request consumes its task's concrete evaluation")
        context, memory_environment = self._run_context(evaluation.stimuli)
        return context.analyse(
            evaluation, self.analysis_bound,
            goal_policy=self.goal_policy, goal_capability=self.goal_capability,
            memory_environment=memory_environment,
            memory_horizon=(
                self.generation_spec.horizon
                if memory_environment is not None else None),
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms, batch=batch)

    @property
    def analysis_bound(self):
        return self.generation_spec.horizon

    def as_dict(self):
        return {
            "kind": "atomic",
            "admission": self.task.handoff_identity,
            "task_contract": self.task.contract.digest,
            "artifact": self.task.realization.identity,
            "generation_spec": self.generation_spec.as_dict(),
            "analysis_bound": self.analysis_bound,
            "analysis_bound_unit": "schedule-slots",
            "frontend_catalogue": self.frontend_catalogue.digest(),
            "goal_policy": self.goal_policy.digest(),
            "goal_capability_use": self.goal_capability.as_dict(),
            "clock_domains": list(self.context.domains),
            "clock_schedule": None,
        }


def prepare_atomic_analysis_request(context, generation_spec):
    """Freeze the source manifest without constructing the atomic product."""

    if type(context) is not BehaviorExactJobContext or len(context.domains) < 2:
        raise TypeError(
            "atomic Behavior preparation needs a multi-domain exact context")
    if type(generation_spec) is not GenerationSpec:
        raise TypeError("atomic Behavior preparation needs a GenerationSpec")
    frontend, policy, capability = context.source_goal_handoff()
    return BehaviorAtomicAnalysisRequest(
        context.task, context, generation_spec, frontend, policy, capability)
