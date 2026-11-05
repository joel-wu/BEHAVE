"""Own frontend binding, capability selection and exact Analysis execution.

This boundary consumes the GoalPolicy frozen before generation, then delegates
closure to core.analysis. It cannot rewrite the concrete evaluation or score.
"""

from __future__ import annotations

from contextlib import nullcontext

from .analysis_budget import AnalysisStopped
from .core.catalogue import CatalogueManifest
from .core.conditional_evidence import (
    AnalysisPolicy, AnalysisStopEvidence, AnalysisStopKind, IssueEvidence,
)
from .core.contract import TaskContract
from .core.instance import BoundedInstance
from .core.goal_provider import EXACT_ANALYSIS_PHASE, EXACT_BOUNDED_HIT, GoalCapabilityUse, GoalPolicy
from .core.outcome import AnalysisOutcome, ConcreteEvaluation, Verdict
from .core.refusal import Refusal, Refused


def catalogue_from_exact(task, exact, analysis_bound, frontend_catalogue,
                          complete_catalogue):
    """Validate one internal exact capability and carry its frozen catalogue."""

    if exact is None:
        return complete_catalogue
    if type(exact) is not BoundedInstance:
        raise TypeError("a simulator command exact provider is a BoundedInstance")
    if exact.domain.artifact != task.realization.identity \
            or exact.domain.task_contract != task.contract.digest \
            or exact.domain.bound != analysis_bound:
        raise Refused(Refusal(
            "sim-command-exact-domain-mismatch",
            "the exact capability must already be bound to this admitted artifact, "
            "task contract and semantic-step bound",
            admitted_artifact=task.realization.identity,
            exact_artifact=exact.domain.artifact,
            admitted_contract=task.contract.digest,
            exact_contract=exact.domain.task_contract,
            analysis_bound=analysis_bound,
            exact_horizon=exact.domain.bound))

    from .behavior.instance import BehaviorInstance

    if type(exact.instance) is BehaviorInstance:
        behavior_frontend = exact.instance.frontend_catalogue_manifest()
        if behavior_frontend != frontend_catalogue:
            raise Refused(Refusal(
                "sim-command-behavior-frontend-catalogue-mismatch",
                "the command's frontend manifest must be the immutable manifest of "
                "the selected Behavior model",
                command=frontend_catalogue.digest(),
                behavior=behavior_frontend.digest()))
    manifest_of = getattr(exact.instance, "catalogue_manifest", None)
    if not callable(manifest_of):
        raise Refused(Refusal(
            "sim-command-exact-catalogue-missing",
            "the bounded provider must expose the exact complete manifest it encodes; "
            "absence cannot mean that the command may reconstruct it"))
    try:
        supplied = manifest_of()
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - provider boundary
        raise Refused(Refusal(
            "sim-command-exact-catalogue-unavailable",
            "the bounded provider could not expose the complete manifest it encodes",
            error=f"{type(exc).__name__}: {exc}")) from None
    if type(supplied) is not CatalogueManifest:
        raise TypeError(
            "the command's exact provider returned a non-CatalogueManifest")
    if supplied != complete_catalogue:
        raise Refused(Refusal(
            "sim-command-exact-catalogue-mismatch",
            "the bounded provider does not encode the complete manifest that this "
            "command will freeze before generation",
            command=complete_catalogue.digest(), exact=supplied.digest()))
    return supplied


def catalogue_from_request(task, artifact, request, generation_spec,
                                     analysis_bound, frontend_catalogue,
                                     complete_catalogue):
    """Validate one lazy exact request without constructing its provider."""

    if request is None:
        return complete_catalogue
    from .behavior.artifact import BehaviorArtifact
    from .behavior.atomic_analysis import BehaviorAtomicAnalysisRequest
    from .behavior.transaction_analysis import BehaviorAnalysisRequest
    from .rtl.analysis import RTLAnalysisRequest

    if type(request) is RTLAnalysisRequest:
        valid = (request.task is task
                 and request.artifact is artifact
                 and request.generation_spec is generation_spec
                 and request.frontend_catalogue is frontend_catalogue)
        expected_bound = generation_spec.horizon
    elif type(request) is BehaviorAnalysisRequest:
        valid = (type(artifact) is BehaviorArtifact
                 and request.task is task
                 and artifact.owns_analysis_request(request)
                 and request.generation_spec is generation_spec
                 and request.frontend_catalogue is frontend_catalogue)
        expected_bound = request.analysis_bound
    elif type(request) is BehaviorAtomicAnalysisRequest:
        valid = (type(artifact) is BehaviorArtifact
                 and request.task is task
                 and artifact.owns_atomic_request(request)
                 and request.generation_spec is generation_spec
                 and request.frontend_catalogue is frontend_catalogue)
        expected_bound = generation_spec.horizon
    else:
        raise TypeError(
            "a simulator command analysis request is a registered exact request")
    if not valid:
        raise Refused(Refusal(
            "sim-command-analysis-request-mismatch",
            "the lazy exact request must retain the command's admitted task, "
            "artifact, generation policy and frontend catalogue"))
    if analysis_bound != expected_bound:
        raise Refused(Refusal(
            "sim-command-lazy-analysis-bound-mismatch",
            "the lazy exact request and command use different semantic-step bounds",
            analysis_bound=analysis_bound,
            expected_bound=expected_bound))
    # The exact worker will reconcile this same complete catalogue again when it binds.
    # Before score persistence, this request supplies no formula-derived authority.
    return complete_catalogue


def bind_request(request, *, task, analysis_bound, frontend_catalogue,
                 catalogue):
    """Bind a closed post-score request, independent of Search providers.

    Composition supplies any shared preparation context. Behavior executes its
    analysis in one isolated job; RTL binds its scoped exact proxy here. The
    command owns construction-refusal reporting after preserving the score.
    """
    from .behavior.atomic_analysis import BehaviorAtomicAnalysisRequest
    from .behavior.transaction_analysis import BehaviorAnalysisRequest
    from .rtl.analysis import RTLAnalysisRequest

    if type(request) in {BehaviorAnalysisRequest, BehaviorAtomicAnalysisRequest}:
        return None, None, request
    if type(request) is not RTLAnalysisRequest:
        raise TypeError("a post-score binding needs a closed analysis request")
    exact = request.bind()
    catalogue_from_exact(
        task, exact, analysis_bound, frontend_catalogue, catalogue)
    return exact, None, None


def analyse_prepared(evaluation, *, exact=None, exact_refusal=None,
                     analysis_request=None, record_audit=None, goal_policy=None,
                     feasibility_timeout_ms=None, query_timeout_ms=None, batch=None):
    """Dispatch the prepared capability inside the caller's Analysis deadline.

    The report boundary owns persistence and concrete-result identity checks;
    this service alone selects how a closed frontend request executes.
    """
    if analysis_request is None:
        return analyse(
            evaluation, exact=exact, exact_refusal=exact_refusal,
            record_audit=record_audit, goal_policy=goal_policy,
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms, batch=batch)
    from .behavior.atomic_analysis import BehaviorAtomicAnalysisRequest
    from .behavior.transaction_analysis import BehaviorAnalysisRequest

    if type(analysis_request) not in {
            BehaviorAnalysisRequest, BehaviorAtomicAnalysisRequest}:
        raise TypeError("a direct analysis request is a closed Behavior request")
    if exact is not None or exact_refusal is not None or record_audit is not None:
        raise ValueError(
            "a direct analysis request cannot be combined with another exact capability")
    if goal_policy is not analysis_request.goal_policy:
        raise ValueError("a direct analysis request needs its pre-generation GoalPolicy")
    try:
        return analysis_request.analyse(
            evaluation, feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms, batch=batch)
    except Refused as exc:
        return analyse(
            evaluation, exact_refusal=exc.refusal, goal_policy=goal_policy,
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms, batch=batch)


def _coordinator_failure(evaluation, exc, *, feasibility_timeout_ms=None,
                         query_timeout_ms=None, batch=None):
    """Close an analyzer exception without giving it score authority.

    This is the last boundary around the public coordinator, not a substitute for its
    typed refusals.  It exists so an implementation exception is still a closed analysis
    result bound to the decided evaluation, rather than an unrelated prose field in the
    report.  Invalid resource arguments normally become a typed refusal inside
    ``api.analyse``; if construction itself failed before that point, omitting the policy
    is more honest than inventing one.
    """

    try:
        policy = AnalysisPolicy(
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms, batch=batch,
            artifact_timeout_ms=(max(1, int(exc.wall_seconds * 1000))
                                 if isinstance(exc, AnalysisStopped) else None))
    except (TypeError, ValueError):
        policy = None
    context = ({"notes": list(exc.__notes__)} if getattr(exc, "__notes__", ()) else {})
    issue = (IssueEvidence(
        "analysis-budget-exhausted",
        "the shared analysis wall budget ended before a complete audited classification",
        {"wall_seconds": exc.wall_seconds, "elapsed_seconds": exc.elapsed_seconds, **context})
        if isinstance(exc, AnalysisStopped) else IssueEvidence(
            "analysis-coordinator-failed",
            "the bounded-analysis coordinator raised before producing its typed result",
            {"error": f"{type(exc).__name__}: {exc}", **context}))
    evidence = AnalysisStopEvidence(
        AnalysisStopKind.REFUSED, policy=policy, issue=issue)
    return AnalysisOutcome(
        Verdict.REFUSED, why=issue.why, evidence=evidence,
        concrete=evaluation.fingerprint)



def analyse(
    evaluation: ConcreteEvaluation, exact=None, record_audit=None, *,
    exact_refusal=None, goal_policy=None, feasibility_timeout_ms=None,
    query_timeout_ms=None, batch=None, wall_timeout_seconds=None,
) -> AnalysisOutcome:
    """Public exact-instance entry; nested calls retain the invocation deadline."""
    from .analysis_budget import analysis_scope

    if type(evaluation) is not ConcreteEvaluation:
        from .core.analysis import analyse as analyse_bounded

        return analyse_bounded(evaluation)
    try:
        with analysis_scope(wall_timeout_seconds):
            return _analyse(evaluation, exact=exact, record_audit=record_audit,
                exact_refusal=exact_refusal, goal_policy=goal_policy,
                feasibility_timeout_ms=feasibility_timeout_ms,
                query_timeout_ms=query_timeout_ms, batch=batch)
    except AnalysisStopped as exc:
        return _coordinator_failure(evaluation, exc,
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms, batch=batch)


def _analyse(
    evaluation: ConcreteEvaluation,
    exact=None,
    record_audit=None,
    *,
    exact_refusal=None,
    goal_policy=None,
    feasibility_timeout_ms=None,
    query_timeout_ms=None,
    batch=None,
) -> AnalysisOutcome:
    """Bounded analysis of an immutable concrete result.

    ``exact`` is an already-bound exact instance; ``record_audit`` is the frontend's
    typed conversion from each raw ``RunResult`` to a trace of that instance's domain.
    When construction of the bound provider itself was unavailable after scoring,
    ``exact_refusal`` retains that exact typed reason.  Omitting both remains the generic
    missing-capability refusal.  In neither case can analysis alter the concrete score.

    The concrete record is read-only and returned nowhere in reconstructed form.  A
    nonempty audited pool establishes feasibility without asking the solver; an empty
    one causes exactly one feasibility query, whose SAT witness is replayed and audited
    before the goal closure starts.
    """
    from .core.analysis import _AnalysisSubject, _analyse_subject, analyse as analyse_bounded

    # The strict worker reader also produces this closed, score-owned view.
    # It must traverse exactly the same capability and audit selection below;
    # the public entry above still requires a live ConcreteEvaluation.
    valid_subject = type(evaluation) in {ConcreteEvaluation, _AnalysisSubject}
    if type(evaluation) is _AnalysisSubject:
        analyse_bounded = _analyse_subject

    # The concrete score has already been computed over ``evaluation.catalogue``.
    # Validate only the additional capability this post-score phase consumes; a
    # missing exact row becomes this analysis branch's typed refusal and cannot remove
    # goals from the concrete denominator or reconstruct the concrete record.
    # A failed construction already names the artifact/domain-specific reason the
    # exact capability is unavailable.  Do not replace it with the less specific
    # catalogue-capability preflight: that check is useful only when a caller actually
    # supplied an instance that claims it can analyse this run.
    if goal_policy is not None and type(goal_policy) is not GoalPolicy:
        raise TypeError("bounded analysis needs the exact frozen GoalPolicy")
    exact_capability_use = None
    if (
        exact_refusal is None
        and exact is not None
        and valid_subject
        and type(getattr(evaluation.task, "contract", None)) is TaskContract
    ):
        try:
            instance = getattr(exact, "instance", None)
            required_of = getattr(instance, "goal_capability_required", None)
            requires_handoff = callable(required_of) and required_of() is True
            carried_policy_of = getattr(instance, "goal_policy", None)
            carried_policy = carried_policy_of() if callable(carried_policy_of) else None
            if requires_handoff:
                if type(carried_policy) is not GoalPolicy:
                    raise Refused(
                        Refusal(
                            "analysis-goal-policy-missing",
                            "the production exact provider carries no GoalPolicy frozen before generation",
                        )
                    )
                if goal_policy is not None and goal_policy is not carried_policy:
                    raise Refused(
                        Refusal(
                            "analysis-goal-policy-reconstructed",
                            "bounded analysis cannot replace the exact provider's frozen "
                            "GoalPolicy with an equal reconstruction",
                        )
                    )
                policy = carried_policy
                capability_of = getattr(instance, "goal_capability_use", None)
                implementation_of = getattr(instance, "exact_goal_implementation", None)
                capability = capability_of() if callable(capability_of) else None
                implementation = implementation_of() if callable(implementation_of) else None
                if type(capability) is not GoalCapabilityUse:
                    raise Refused(
                        Refusal(
                            "analysis-exact-goal-capability-missing",
                            "the production exact provider carries no closed bounded-hit GoalCapabilityUse",
                        )
                    )
                if capability.policy_digest != policy.digest() or capability.source_catalogue is not policy.manifest:
                    raise Refused(
                        Refusal(
                            "analysis-exact-goal-capability-policy-mismatch",
                            "the exact bounded-hit capability was not minted by the provider's frozen GoalPolicy",
                        )
                    )
                capability.reconcile(EXACT_BOUNDED_HIT, implementation, policy.manifest, tuple(policy.manifest))
                exact_capability_use = capability
            else:
                if goal_policy is None:
                    raise Refused(
                        Refusal(
                            "analysis-goal-policy-missing",
                            "bounded analysis requires the GoalPolicy frozen before "
                            "generation; a catalogue cannot reconstruct its capability "
                            "matrix",
                        )
                    )
                policy = goal_policy
            if policy.manifest != evaluation.catalogue:
                raise Refused(
                    Refusal(
                        "analysis-goal-policy-catalogue-mismatch",
                        "bounded analysis received a goal policy for a different concrete denominator",
                        policy=policy.manifest.digest(),
                        concrete=evaluation.catalogue.digest(),
                    )
                )
            policy.validate(EXACT_ANALYSIS_PHASE)
        except Refused as exc:
            exact = None
            exact_refusal = exc.refusal

    # Each exact frontend selects its own fixed raw-record audit here.  Dispatch is on
    # the exact provider type, never on the record's shape: only that provider knows how
    # a concrete record denotes one member of its trace domain.
    if record_audit is None and exact is not None and valid_subject:
        from .behavior.instance import BehaviorInstance
        from .core.instance import BoundedInstance

        if type(exact) is BoundedInstance and type(exact.instance) is BehaviorInstance:
            from .behavior.analysis import audit_record as audit_behavior_record

            record_audit = lambda result, bound: audit_behavior_record(result, bound, task=evaluation.task)
        elif type(exact) is BoundedInstance:
            from .behavior.exact_service import BehaviorIsolatedAnalysisInstance

            if type(exact.instance) is BehaviorIsolatedAnalysisInstance:
                record_audit = lambda result, bound: bound.instance.audit_record(result, evaluation.task)
            else:
                from .rtl.analysis import RTLIsolatedAnalysisInstance

                if type(exact.instance) is RTLIsolatedAnalysisInstance:
                    record_audit = lambda result, bound: bound.instance.audit_record(result, evaluation.task)

    from .analysis_budget import active_analysis
    from .core.instance import BoundedInstance
    from .rtl.analysis import RTLIsolatedAnalysisInstance

    scope = nullcontext()
    if type(exact) is BoundedInstance and type(exact.instance) is RTLIsolatedAnalysisInstance \
            and valid_subject:
        scope = exact.instance.execution_scope()
    with scope:
        return analyse_bounded(
            evaluation,
            exact=exact,
            record_audit=record_audit,
            exact_refusal=exact_refusal,
            goal_capability_use=exact_capability_use,
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms,
            batch=batch,
            _artifact_timeout_ms=max(1, int(active_analysis().wall_seconds * 1000)),
        )
