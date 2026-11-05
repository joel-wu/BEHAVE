"""Prepare closed execution handles from admitted source records.

These are the implementation functions re-exported by ``sim.api``. Preparation
owns source/catalogue binding; concrete evaluation and post-score analysis keep
their separate execution owners. Frontends are imported only when requested.
"""

from __future__ import annotations

from .core.admission import AdmittedTask
from .core.catalogue import CatalogueManifest
from .core.failure import FailureRaised, RunFailure
from .core.refusal import Refusal, Refused
from .public_handle import BehaviorConfig, RTLConfig
from .stimulus_set import GenerationSpec

__all__ = ["prepare_behavior", "prepare_rtl"]


def prepare_behavior(task, config, generation_spec=None, *, bounded_analysis=False, analysis_bound=None):
    """Construct an isolated Behavior execution handle from public records.

    Runtime baseline scoring does not need ``generation_spec``. Supplying one
    names a finite domain for explicit finite stimuli or optional analysis; it
    does not shorten a runtime baseline's input sequences.

    The verifier snapshots bytes; a named source judge derives the catalogue and
    signature without running the model. Scoring executes the candidate in its own
    child and does not require successful construction or SolverIR lowering at
    preparation time. ``bounded_analysis=True`` also freezes a post-score request:
    construction authentication, lowering, planning and solver binding belong there.
    An ordinary Behavior may select a positive ``analysis_bound`` no
    larger than ``generation_spec.queue_bound``; its default is that input bound,
    independently of reset/drain slots and ``generation_spec.horizon``. A
    multi-clock request binds its complete global schedule only after concrete scoring
    and therefore does not admit a shorter bound.
    """

    from .behavior.exact_service import BehaviorExactJobContext
    from .behavior.transaction_analysis import prepare_behavior_analysis_request
    from .public_handle import _behavior_handle, _behavior_source_snapshot

    if type(task) is not AdmittedTask:
        raise TypeError("preparing a Behavior handle needs an AdmittedTask")
    if type(config) is not BehaviorConfig:
        raise TypeError("preparing a Behavior handle needs a BehaviorConfig")
    if generation_spec is not None and type(generation_spec) is not GenerationSpec:
        raise TypeError("generation_spec must be a GenerationSpec or absent")
    if type(bounded_analysis) is not bool:
        raise TypeError("bounded_analysis must be an exact bool")
    if analysis_bound is not None and not bounded_analysis:
        raise ValueError("analysis_bound requires bounded_analysis=True")
    if bounded_analysis and generation_spec is None:
        raise ValueError("bounded analysis requires an explicit GenerationSpec")
    if task.realization.kind != "behavior":
        raise Refused(
            Refusal(
                "behavior-public-exact-artifact-kind-mismatch",
                "a Behavior exact service can bind only a transaction-level Behavior realization",
                artifact_kind=task.realization.kind,
            )
        )
    if not task.realization.identity:
        raise Refused(
            Refusal(
                "behavior-public-exact-artifact-unbound",
                "the admitted Behavior realization has no stable transition-system identity",
            )
        )
    source = _behavior_source_snapshot(config)
    context = BehaviorExactJobContext(
        task, source, config.file, timeout_s=config.timeout_s, model=config.model)
    prepared = context.prepare()
    if isinstance(prepared, Refusal):
        raise Refused(prepared)
    if not bounded_analysis:
        from .behavior.artifact import BehaviorArtifact

        frontend = context.source_catalogue
        if type(frontend) is not CatalogueManifest:
            raise Refused(
                Refusal(
                    "behavior-source-catalogue-unavailable",
                    "the isolated source judge did not return a concrete Behavior catalogue",
                )
            )
        artifact = BehaviorArtifact(task, context, generation_spec, frontend)
        return _behavior_handle(task, context, artifact, generation_spec, frontend)
    if len(context.domains) >= 2:
        from .behavior.artifact import BehaviorArtifact
        from .behavior.atomic_analysis import prepare_atomic_analysis_request

        if analysis_bound is not None and analysis_bound != generation_spec.horizon:
            raise Refused(
                Refusal(
                    "behavior-atomic-analysis-bound-unsupported",
                    "atomic Behavior analysis is bound to the complete global schedule",
                    requested=analysis_bound,
                    schedule_horizon=generation_spec.horizon,
                )
            )
        request = prepare_atomic_analysis_request(context, generation_spec)
        artifact = BehaviorArtifact(task, context, generation_spec, request.frontend_catalogue)
        return _behavior_handle(
            task, context, artifact, generation_spec, request.frontend_catalogue, analysis_request=request
        )
    # An ordinary Behavior transition is one complete accepted procedure call, not
    # one protocol slot.  Freeze only its catalogue here; the representation plan is
    # ancillary analysis and is constructed after the concrete score is persisted.
    request = prepare_behavior_analysis_request(context, generation_spec, analysis_bound=analysis_bound)
    from .behavior.artifact import BehaviorArtifact

    frontend = request.frontend_catalogue
    artifact = BehaviorArtifact(task, context, generation_spec, frontend)
    return _behavior_handle(task, context, artifact, generation_spec, frontend, analysis_request=request)


def prepare_rtl(task, config, generation_spec=None, *,
                analysis_clock_schedule=None, analysis_memory_environment=None):
    """Construct a lazy RTL execution handle from public frozen records only.

    Omit ``generation_spec`` for runtime baseline scoring. A supplied finite
    domain also prepares the optional post-score analysis capability. General
    protocols need an explicit ``analysis_clock_schedule`` (GlobalSchedule);
    external memory needs ``analysis_memory_environment`` (MemoryEnvironment).
    These declare the analysis domain, not the runtime baseline workload.

    The caller neither imports nor receives ``RTLArtifact``.  Structural elaboration
    is needed before generation to freeze the catalogue.  Relation construction and
    solver binding are deliberately deferred until after the concrete score is durable;
    the returned handle can create a :class:`CommandRequest` while retaining the same
    private artifact and typed post-score request.
    """

    from .public_handle import _rtl_handle
    from .rtl.analysis import RTLAnalysisRequest
    from .rtl.artifact import RTLArtifact
    from .rtl.frontend_error import SourceRejected, tool_failure

    if type(task) is not AdmittedTask:
        raise TypeError("preparing an RTL handle needs an AdmittedTask")
    if type(config) is not RTLConfig:
        raise TypeError("preparing an RTL handle needs an RTLConfig")
    if generation_spec is not None and type(generation_spec) is not GenerationSpec:
        raise TypeError("generation_spec must be a GenerationSpec or absent")
    if generation_spec is None and (analysis_clock_schedule is not None
                                    or analysis_memory_environment is not None):
        raise ValueError("an analysis environment needs a finite GenerationSpec")
    run_policy = config.as_dict()["run_policy"]
    domain_policy = {} if generation_spec is None else {
        "horizon": generation_spec.horizon,
        "queue_bound": generation_spec.queue_bound,
        "reset_hold_cycles": generation_spec.reset_hold_cycles,
        "drain": generation_spec.drain,
        "stall_every": generation_spec.stall_every,
    }
    conflicts = {
        name: {"config": run_policy[name], "generation": value}
        for name, value in domain_policy.items()
        if name in run_policy and run_policy[name] != value
    }
    if conflicts:
        raise Refused(
            Refusal(
                "rtl-public-run-policy-mismatch",
                "RTLConfig and the admitted GenerationSpec state different run-domain or construction-policy values",
                conflicts=conflicts,
            )
        )
    run_policy.update(domain_policy)
    artifact = RTLArtifact(
        config.rtl_path,
        config.top_module,
        config.workdir,
        period_ns=config.period_ns,
        timeout_s=config.timeout_s,
        verify_every=config.verify_every,
        profile_replay=config.profile_replay,
        run_policy=run_policy,
        source_rules=task.contract.source_rules,
    )
    if artifact.identity != task.realization.identity:
        raise Refused(
            Refusal(
                "rtl-public-exact-artifact-mismatch",
                "the public RTL source and admitted realization name different bytes",
                admitted=task.realization.identity,
                artifact=artifact.identity,
            )
        )
    try:
        frontend_catalogue = artifact.frontend_catalogue(expected_identity=task.realization.identity)
    except SourceRejected as exc:
        raise FailureRaised(RunFailure.candidate("candidate-frontend", exc.reason)) from None
    except FailureRaised:
        raise
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - isolated frontend boundary
        from .rtl.tool_protocol import ToolIsolationError

        if isinstance(exc, ToolIsolationError):
            raise FailureRaised(tool_failure(exc)) from None
        owner = RunFailure.infrastructure if isinstance(exc, OSError) else RunFailure.verifier
        raise FailureRaised(owner(
            "rtl-frontend", Refusal(
                "rtl-frontend-catalogue-unavailable",
                "the isolated RTL frontend could not construct the structural catalogue required by this handle",
                error=f"{type(exc).__name__}: {exc}",
            ))) from None
    if type(frontend_catalogue) is not CatalogueManifest:
        raise FailureRaised(RunFailure.verifier(
            "rtl-frontend", Refusal(
                "rtl-frontend-catalogue-invalid",
                "the isolated RTL frontend did not return a CatalogueManifest",
            )))
    artifact.bind_observation(task.contract)
    analysis_request = (None if generation_spec is None else
                        RTLAnalysisRequest(
                            task, artifact, generation_spec, frontend_catalogue,
                            clock_schedule=analysis_clock_schedule,
                            memory_environment=analysis_memory_environment))
    return _rtl_handle(task, artifact, generation_spec, frontend_catalogue, analysis_request)
