"""Production construction of Appendix A.3's two independent providers.

The scheduler in :mod:`behave.sim.generation` owns the worklists.  This module owns
only the capabilities selected for one admitted run: reference-Behavior guidance and
candidate guidance, both against the same replay artifact.  The candidate adapter is
Behavior exact-local for a Behavior artifact and RTL exact-local for RTL.  Failure to
construct either ancillary capability is represented by an unavailable provider, so
the common baseline and the other search still reach concrete scoring unchanged.
"""

from __future__ import annotations

from pathlib import Path

from .core.admission import AdmittedTask
from .core.catalogue import CatalogueManifest
from .core.frozen import Frozen
from .core.goal_provider import (
    BEHAVIOR_LOCAL_GUIDANCE_POLICY,
    LOCAL_GUIDANCE,
    RTL_LOCAL_GUIDANCE_POLICY,
    GoalCapabilityUse,
    GoalPolicy,
)
from .core.reference import LoadedReference
from .core.refusal import Refusal, Refused
from .generation import UnavailableGenerationProvider
from .stimulus_set import GenerationSpec

__all__ = [
    "ProductionGenerationCapabilities",
    "production_generation_providers",
]


class ProductionGenerationCapabilities(Frozen):
    """The reference-to-candidate and candidate-to-candidate directions."""

    __slots__ = ("candidate", "reference")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("ProductionGenerationCapabilities is a closed record")

    def __init__(self, reference, candidate):
        self.reference = reference
        self.candidate = candidate
        self._freeze()

    @property
    def providers(self):
        return self.reference, self.candidate

    def candidate_exact_context(self):
        context_of = getattr(self.candidate, "exact_job_context", None)
        return None if not callable(context_of) else context_of()


def reuse_rtl_request(request, providers):
    """Reuse one matching production context, without granting analysis authority.

    A caller may supply other search directions. Only the exact RTL provider and
    context types, bound to this handle's immutable inputs, qualify for reuse.
    Ambiguous or unrelated contexts leave the original request unchanged.
    """
    from .rtl.analysis import RTLAnalysisRequest

    if type(request) is not RTLAnalysisRequest or request.goal_capability is None:
        return request
    from .deferred_generation import DeferredGenerationProvider
    from .engines.rtl.generation import RTLGenerationProvider
    from .rtl.exact_job_context import RTLExactJobContext

    contexts = {}
    for provider in providers:
        if type(provider) is DeferredGenerationProvider:
            provider = provider.prepared_provider()
        if type(provider) is not RTLGenerationProvider:
            continue
        context = provider.exact_job_context()
        if type(context) is RTLExactJobContext \
                and context.artifact is request.artifact \
                and context.contract is request.task.contract \
                and context.generation_spec is request.generation_spec \
                and context.frontend_catalogue == request.frontend_catalogue \
                and context.complete_catalogue == request.goal_policy.manifest \
                and context.goal_policy is request.goal_policy \
                and request.matches_environment(context):
            contexts[id(context)] = context
    if len(contexts) != 1:
        return request
    context = next(iter(contexts.values()))
    context.bind_goal_policy(request.goal_policy, request.goal_capability)
    return RTLAnalysisRequest(
        request.task, request.artifact, request.generation_spec,
        request.frontend_catalogue, exact_context=context,
        clock_schedule=request._clock_schedule,
        memory_environment=request._memory_environment)


def _construction_refusal(kind, owner, exc):
    if isinstance(exc, Refused):
        return exc.refusal
    return Refusal(
        kind,
        f"the {owner} local guidance capability could not be constructed; its "
        "independent search remains visible but contributes no candidate",
        error=f"{type(exc).__name__}: {exc}")


def _require_bound_guidance(provider, owner):
    """Require the provider to carry the GoalPolicy handoff it actually consumes."""

    if type(provider) is UnavailableGenerationProvider:
        return provider
    binding_of = getattr(provider, "goal_capability_use", None)
    if not callable(binding_of):
        raise Refused(Refusal(
            "generation-provider-goal-capability-unbound",
            f"the {owner} local guidance implementation is not bound to a frozen "
            "source GoalPolicy"))
    binding = binding_of()
    if type(binding) is not GoalCapabilityUse \
            or binding.capability != LOCAL_GUIDANCE:
        raise Refused(Refusal(
            "generation-provider-goal-capability-invalid",
            f"the {owner} provider did not carry an exact local-guidance policy "
            "handoff"))
    manifest = provider.catalogue_manifest()
    targets = tuple(sorted(provider.targetable_goals))
    binding.reconcile(LOCAL_GUIDANCE, provider.policy, manifest, targets)
    return provider


def production_generation_providers(
        task, artifact, reference, generation_spec, frontend_catalogue, *,
        workdir, clock_schedule=None, enable_local_guidance=True, defer=False,
        goal_policy=None):
    """Return the source-to-artifact and artifact-to-artifact provider pair.

    Public handles defer each provider's preparation into its shared-budget lease;
    legacy internal consumers may explicitly prepare eagerly. Recorded guidance
    reuses completed candidate traces and the reference-source observations retained
    by comparison. Each direction reads its own catalogue and hit evidence before
    Select. Their frozen source policies precede local-query construction; a
    covered baseline needs no lowered layout. A score-only caller may disable both
    ancillary solvers while retaining the two named directions in generation provenance.
    """

    if type(task) is not AdmittedTask:
        raise TypeError("production generation needs the exact AdmittedTask")
    if type(reference) is not LoadedReference:
        raise TypeError("production generation needs the admitted LoadedReference")
    if generation_spec is not None and type(generation_spec) is not GenerationSpec:
        raise TypeError("production generation needs the frozen GenerationSpec")
    if type(frontend_catalogue) is not CatalogueManifest:
        raise TypeError("production generation needs the exact frontend catalogue")
    if type(enable_local_guidance) is not bool:
        raise TypeError("enable_local_guidance must be an exact bool")
    if clock_schedule is not None:
        from .core.clock_schedule import GlobalSchedule

        if type(clock_schedule) is not GlobalSchedule:
            raise TypeError("guidance requires a frozen GlobalSchedule")
    workdir = Path(workdir)
    if goal_policy is None:
        from .goal_policy import evaluation_policy

        goal_policy = evaluation_policy(task, frontend_catalogue)
    elif type(goal_policy) is not GoalPolicy:
        raise TypeError("production generation needs the exact frozen GoalPolicy")

    reference_name = f"behavior/{reference.digest}->{task.realization.identity}"
    candidate_name = (
        f"behavior/candidate:{task.realization.identity}->"
        f"{task.realization.identity}"
        if getattr(artifact, "kind", None) == "behavior"
        else "rtl/exact-local"
    )
    if not enable_local_guidance:
        # Concrete scoring needs the canonical baseline and replay, not either local
        # solver.  Keep both A.3 directions in the provenance while avoiding a
        # representation plan and solver context that no Probe can consume.
        disabled = Refusal(
            "generation-local-guidance-disabled",
            "this command disabled ancillary local guidance and retains only its "
            "canonical generation baseline")
        return ProductionGenerationCapabilities(
            UnavailableGenerationProvider(
                reference_name, BEHAVIOR_LOCAL_GUIDANCE_POLICY, disabled),
            UnavailableGenerationProvider(
                candidate_name,
                (BEHAVIOR_LOCAL_GUIDANCE_POLICY
                 if getattr(artifact, "kind", None) == "behavior"
                 else RTL_LOCAL_GUIDANCE_POLICY),
                disabled,
                catalogue=goal_policy.manifest),
        )

    # The command owns shared concrete executions; the two searches own their
    # separate Select/Probe state. Standalone providers retain their local trace
    # service when there is no command execution to share.
    selection_context = None

    def replay_many(seeds, execution=None):
        if execution is not None:
            return execution.traces(seeds)
        if selection_context is not None:
            return selection_context.selection_traces(tuple(seeds))
        if getattr(artifact, "kind", None) == "rtl":
            return artifact.traces_per_process(task.contract, tuple(seeds))
        return tuple(artifact.trace(task.contract, seed) for seed in seeds)

    def make_reference(spec, execution, schedule, *, worker=None):
        from .engines.behavior.generation import BehaviorIsolatedGenerationProvider

        try:
            from . import api

            provider = api.prepare_behavior_generation(
                task, spec, reference,
                lambda seed: replay_many((seed,), execution)[0],
                traces_of=lambda seeds: replay_many(seeds, execution),
                cache_root=workdir / "behavior-context-cache")
            return _require_bound_guidance(provider, "reference Behavior")
        except Exception as exc:  # noqa: BLE001 - optional capability boundary
            return UnavailableGenerationProvider(
                reference_name, BehaviorIsolatedGenerationProvider.policy,
                _construction_refusal(
                    "behavior-generation-provider-unavailable", "reference Behavior", exc))

    def make_candidate(spec, execution, schedule, *, worker=None):
        nonlocal selection_context
        from .engines.behavior.generation import BehaviorIsolatedGenerationProvider
        from .engines.rtl.generation import RTLGenerationProvider

        try:
            if getattr(artifact, "kind", None) == "behavior":
                candidate = BehaviorIsolatedGenerationProvider.from_behavior_artifact(
                    artifact, task, spec, frontend_catalogue,
                    goal_policy=goal_policy,
                    _execution=execution,
                    cache_root=workdir / "candidate-behavior-context-cache")
                return _require_bound_guidance(candidate, "candidate Behavior")
            candidate = RTLGenerationProvider.from_artifact(
                artifact, task.contract, spec, frontend_catalogue,
                workdir=(workdir / "rtl-exact" / spec.identity
                         if worker is None else
                         workdir / "rtl-exact" / spec.identity / f"worker-{worker}"),
                clock_schedule=schedule, goal_policy=goal_policy,
                _execution=execution)
            candidate = _require_bound_guidance(candidate, "candidate RTL")
            if worker is None and spec is generation_spec:
                context_of = getattr(candidate, "exact_job_context", None)
                selection_context = None if context_of is None else context_of()
            return candidate
        except Exception as exc:  # noqa: BLE001 - optional capability boundary
            return UnavailableGenerationProvider(
                candidate_name, (BEHAVIOR_LOCAL_GUIDANCE_POLICY
                                 if artifact.kind == "behavior" else RTL_LOCAL_GUIDANCE_POLICY),
                _construction_refusal(
                    ("behavior-candidate-generation-provider-unavailable"
                     if artifact.kind == "behavior" else "rtl-generation-provider-unavailable"),
                    "candidate", exc),
                catalogue=goal_policy.manifest)

    def prepare(factory, execution, baseline):
        if not baseline:
            return factory(generation_spec, execution, clock_schedule)
        from .runtime_guidance import RecordedExecutions, RuntimeGuidance

        name, policy = ((reference_name, BEHAVIOR_LOCAL_GUIDANCE_POLICY)
                        if factory is make_reference else
                        (candidate_name, BEHAVIOR_LOCAL_GUIDANCE_POLICY
                         if artifact.kind == "behavior" else RTL_LOCAL_GUIDANCE_POLICY))
        source_catalogue = None
        observe = (None if execution is None else
                   execution.feedback if factory is make_candidate else execution.reference_feedback)
        try:
            if factory is make_reference:
                from .goal_policy import evaluation_goal_policy, source_guidance_catalogue

                source_catalogue = reference.source_catalogue_for(task.contract)
                if source_catalogue is None:
                    raise Refused(Refusal(
                        "generation-source-catalogue-unavailable",
                        "the reference adapter supplied no native source declaration"))
                source_policy = evaluation_goal_policy(
                    task.contract, source_catalogue, artifact_kind="behavior")
                source_catalogue = source_guidance_catalogue(
                    task.contract, source_catalogue, artifact_kind="behavior")
            else:
                from .goal_policy import source_guidance_catalogue

                source_policy = goal_policy
                source_catalogue = source_guidance_catalogue(
                    task.contract, frontend_catalogue, artifact_kind=artifact.kind)
            capability = source_policy.bind_implementation(
                LOCAL_GUIDANCE, policy, source_policy.manifest,
                context_catalogue=source_catalogue)
            records = RecordedExecutions(
                lambda seeds: replay_many(seeds, execution), feedback=observe)
            runtime = any(seed.replay_policy is not None for seed in baseline)
            recorded_schedule = None
            if factory is make_candidate and artifact.kind == "rtl" and task.contract.clocks:
                from .rtl.exact_product import global_schedule_for_stimuli, needs_global_schedule_product

                if needs_global_schedule_product(task.contract):
                    # Single-clock sampled/bus products also need the actual
                    # action sequence. Its slots live in the recorded trace even
                    # when concrete replay did not require a GlobalSchedule.
                    recorded_schedule = lambda seed: global_schedule_for_stimuli(task.contract, (seed,))
            return RuntimeGuidance(
                factory, records, baseline, task.contract,
                name=name, capability=capability, observe=observe,
                fixed_spec=None if runtime else generation_spec,
                clock_schedule=clock_schedule, recorded_schedule=recorded_schedule,
                worker_factory=lambda index: (
                    lambda spec, records, schedule: factory(
                        spec, records, schedule, worker=index)))
        except Refused as exc:
            return UnavailableGenerationProvider(
                name, policy, exc.refusal, catalogue=source_catalogue,
                observe=observe if source_catalogue is not None else None)

    def build_reference(execution=None, baseline=()):
        return prepare(make_reference, execution, baseline)

    def build_candidate(execution=None, baseline=()):
        return prepare(make_candidate, execution, baseline)

    if defer:
        from .deferred_generation import deferred_provider

        binding = {"version": "generation-recipe/9", "contract": task.contract.digest,
                   "artifact": task.realization.identity, "reference": reference.digest,
                   "generation_spec": None if generation_spec is None else generation_spec.identity,
                   "frontend_catalogue": frontend_catalogue.digest(),
                   "goal_policy": goal_policy.digest(),
                   "workdir": str(workdir),
                   "clock_schedule": None if clock_schedule is None else clock_schedule.as_dict()}
        return ProductionGenerationCapabilities(
            deferred_provider(reference_name, BEHAVIOR_LOCAL_GUIDANCE_POLICY,
                              binding, build_reference),
            deferred_provider(candidate_name,
                              BEHAVIOR_LOCAL_GUIDANCE_POLICY if artifact.kind == "behavior"
                              else RTL_LOCAL_GUIDANCE_POLICY, binding, build_candidate))
    return ProductionGenerationCapabilities(build_reference(), build_candidate())
