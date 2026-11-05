"""Task/goal binding for the RTL exact-analysis backend."""

from ..core.admission import AdmittedTask
from ..core.catalogue import CatalogueManifest
from ..core.refusal import Refusal, Refused
from ..stimulus_set import GenerationSpec


def bind_analysis(
    task, artifact, generation_spec, frontend_catalogue, *, exact_context=None,
    goal_policy=None, goal_capability=None, clock_schedule=None, memory_environment=None
):
    """Bind the supported RTL task product through the isolated exact service.

    This is the internal construction seam behind :func:`prepare_rtl`.  The task,
    artifact identity, horizon, queue bound and structural manifest are therefore the
    same records the command later rechecks. Source construction and independent
    RTL replay use isolated services; every Z3 operation remains in the named
    exact-analysis worker. Local generation keeps its separate query context.
    """

    from .analysis import RTLIsolatedAnalysisInstance
    from .artifact import RTLArtifact
    from .exact_job_context import RTLExactJobContext

    if type(task) is not AdmittedTask:
        raise TypeError("binding RTL exact analysis needs an AdmittedTask")
    if type(artifact) is not RTLArtifact:
        raise TypeError("binding RTL exact analysis needs an RTLArtifact")
    if type(generation_spec) is not GenerationSpec:
        raise TypeError("binding RTL exact analysis needs the frozen GenerationSpec")
    if type(frontend_catalogue) is not CatalogueManifest:
        raise TypeError("binding RTL exact analysis needs the frozen frontend CatalogueManifest")
    if task.realization.identity != artifact.identity:
        raise Refused(
            Refusal(
                "rtl-task-exact-artifact-mismatch",
                "the admitted realization and RTLArtifact name different source bytes",
                admitted=task.realization.identity,
                artifact=artifact.identity,
            )
        )
    if goal_policy is None or goal_capability is None:
        raise Refused(
            Refusal(
                "rtl-analysis-goal-capability-missing",
                "the RTL exact provider requires the GoalPolicy handoff frozen before generation",
            )
        )
    complete = goal_policy.manifest
    if exact_context is None:
        context = RTLExactJobContext(
            artifact, task.contract, generation_spec, frontend_catalogue, complete,
            workdir=artifact.workdir, clock_schedule=clock_schedule
        )
        if memory_environment is not None:
            context.bind_memory_environment(memory_environment)
        context.bind_goal_policy(goal_policy, goal_capability)
    else:
        if type(exact_context) is not RTLExactJobContext:
            raise TypeError("exact_context must be an RTLExactJobContext or absent")
        if (
            exact_context.artifact is not artifact
            or exact_context.contract is not task.contract
            or exact_context.generation_spec is not generation_spec
            or exact_context.frontend_catalogue != frontend_catalogue
            or exact_context.complete_catalogue != complete
        ):
            raise Refused(
                Refusal(
                    "rtl-analysis-exact-context-mismatch",
                    "post-score analysis must reuse the exact context frozen for this "
                    "task, artifact, generation policy and catalogue",
                )
            )
        if exact_context.goal_policy is not goal_policy or exact_context.goal_capability_use is not goal_capability:
            raise Refused(
                Refusal(
                    "rtl-analysis-goal-capability-context-mismatch",
                    "post-score RTL analysis must reuse the goal-policy capability frozen by generation",
                )
            )
        context = exact_context
    context.bind_admitted_task(task)
    instance = RTLIsolatedAnalysisInstance(context, generation_spec.horizon)
    return instance.bound()
