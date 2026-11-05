"""Compose frontend declarations and task monitors at preparation, not in core.

No source is parsed or executed here. Each frontend owns its standard-schema
assignments; the prepared GoalPolicy remains the identity used by all phases.
"""

from .behavior import goal_provider as behavior_goals
from .core.admission import AdmittedTask
from .core.catalogue import CatalogueManifest, catalogue_subset, merge_catalogues
from .core.goal_provider import (
    CONCRETE_HIT,
    CONCRETE_PHASE,
    DECLARATION,
    ENABLED,
    INAPPLICABLE,
    INCOMPLETE,
    GoalPolicy,
    GoalPolicySelection,
    GoalProviderRegistry,
    VersionedReason,
    standard_goal_schemas,
)
from .core.refusal import Refusal, Refused
from .rtl import goal_provider as rtl_goals

EVALUATION_GOAL_POLICY_VERSION = "evaluation-goals/27"
_FRONTENDS = {
    behavior_goals.OWNER: behavior_goals.goal_providers,
    rtl_goals.OWNER: rtl_goals.goal_providers,
}


def builtin_frontend_goal_providers(catalogue):
    """Compose frontend-owned schema assignments without changing declarations."""
    if type(catalogue) is not CatalogueManifest:
        raise TypeError("built-in frontend providers need the exact CatalogueManifest")
    groups = {}
    for goal_id, goal in catalogue.items():
        if goal.provider not in _FRONTENDS:
            raise Refused(Refusal(
                "goal-provider-builtin-owner-unknown",
                "the frontend manifest contains a goal with no registered standard provider owner",
                goal=goal_id, provider=goal.provider, family=goal.family, goal_class=goal.cls))
        groups.setdefault(goal.provider, []).append(goal_id)
    return tuple(
        provider
        for owner, goals in sorted(groups.items())
        for provider in _FRONTENDS[owner](catalogue_subset(catalogue, goals))
    )


def evaluation_policy(task: AdmittedTask, frontend_catalogue: CatalogueManifest) -> GoalPolicy:
    """Freeze the admitted artifact's goal denominator and capability matrix.

    Consumers retain this object; reconstructing it from the manifest would
    create a second capability authority.
    """

    if type(task) is not AdmittedTask:
        raise Refused(
            Refusal(
                "catalogue-task-not-admitted",
                "building an evaluation manifest requires the immutable task/artifact pair produced by admit()",
                got=type(task).__name__,
            )
        )
    if type(frontend_catalogue) is not CatalogueManifest:
        raise TypeError("frontend_catalogue must be the exact CatalogueManifest produced by the admitted frontend")
    return evaluation_goal_policy(
        task.contract,
        frontend_catalogue,
        artifact_kind=task.realization.kind,
    )


def evaluation_manifest(task: AdmittedTask, frontend_catalogue: CatalogueManifest) -> CatalogueManifest:
    """The declaration denominator of :func:`evaluation_policy`."""

    return evaluation_policy(task, frontend_catalogue).manifest


def source_guidance_catalogue(contract, frontend_catalogue, *, artifact_kind):
    """The existing source query's declaration, not the scoring denominator.

    Behavior source IR owns source marks and its logical transaction boundary;
    RTL source events and protocol-product queries share the fixed declaration.
    Both query paths retain declared logical transaction predicates.
    """
    from .core.monitor import monitor_for

    if artifact_kind not in ("behavior", "rtl", "device"):
        raise ValueError("source guidance needs a known artifact kind")
    protocol = (() if artifact_kind == "behavior" else
                (CatalogueManifest(monitor_for(contract).goals()),))
    return merge_catalogues(frontend_catalogue, *protocol, CatalogueManifest(
        tuple(rule.declaration() for rule in contract.observation_goals)
        + tuple(goal for spec in contract.transaction_monitors if spec.applicable(artifact_kind)
                for goal in spec.declarations())
        + tuple(goal for rule in contract.latency_goals if rule.queryable(artifact_kind)
                for goal in rule.declarations())))


_DECLARED_SCHEMAS = frozenset({
    "value-observation", "operation-event", "state-activity",
    "sequence", "latency", "schedule-interleaving",
})


def evaluation_goal_policy(contract, frontend_catalogue, *, artifact_kind="rtl"):
    """Freeze the one production policy that owns an evaluation denominator.

    Implemented providers retain their declarations even when schema enumeration
    is incomplete. Such a policy describes the implemented subset, not an
    appendix-complete denominator, and cannot pass release validation. Concrete
    and exact capabilities of retained instances are checked separately.
    """

    from .core.contract import TaskContract
    from .core.observation_monitor import execution_monitor_for

    if type(contract) is not TaskContract:
        raise TypeError("an evaluation goal policy needs the exact TaskContract")
    if type(frontend_catalogue) is not CatalogueManifest:
        raise TypeError(
            "the frontend catalogue must be an exact CatalogueManifest")

    if type(artifact_kind) is not str or artifact_kind not in {
            "behavior", "device", "rtl"}:
        raise Refused(Refusal(
            "goal-policy-artifact-kind-unknown",
            "the evaluation goal policy must know whether the admitted artifact is "
            "transaction-level Behavior or a signal-level RTL/device realization",
            artifact_kind=repr(artifact_kind),
        ))

    # A Behavior procedure consumes and emits logical transactions.  It has no pin
    # ready/valid timing, so adding the task's signal-level protocol monitor to its
    # denominator would require the bounded frontend to invent timing the procedure
    # never declared.  RTL/device realizations are driven slot by slot and therefore do
    # own those task-monitor goals.
    monitor = execution_monitor_for(contract, artifact_kind)
    monitor_providers = () if monitor is None else monitor.goal_providers()
    # Diagnose a reused public identifier before provider classification.  This is a
    # disjointness check only; the policy below remains the sole manifest constructor.
    if monitor_providers:
        merge_catalogues(
            frontend_catalogue,
            *(provider.catalogue for provider in monitor_providers))
    providers = list(builtin_frontend_goal_providers(frontend_catalogue))
    providers.extend(provider for provider in monitor_providers
                     if provider.catalogue)
    schemas = standard_goal_schemas()
    selections = []
    source_gaps = rtl_goals.source_enumeration_gaps(frontend_catalogue)
    for schema in schemas:
        selected = tuple(provider for provider in providers
                         if provider.schema == schema)
        if schema.name in source_gaps:
            selections.append(GoalPolicySelection(
                schema, INCOMPLETE, selected,
                reason=VersionedReason(
                    "legacy-rtl-action-catalogue", EVALUATION_GOAL_POLICY_VERSION,
                    "legacy RTL action rows do not enumerate the source schema's occurrences"),
            ))
        elif selected:
            selections.append(GoalPolicySelection(schema, ENABLED, selected))
        elif schema.name == "operation-event" and contract.operation_rules:
            selections.append(GoalPolicySelection(
                schema, INAPPLICABLE, reason=VersionedReason(
                    "no-matching-source-operation-sites", EVALUATION_GOAL_POLICY_VERSION,
                    "the declared operation rules have no matching source occurrences")))
        elif schema.name == "value-observation" and contract.source_read_rules:
            selections.append(GoalPolicySelection(
                schema, INAPPLICABLE, reason=VersionedReason(
                    "no-matching-source-read-sites", EVALUATION_GOAL_POLICY_VERSION,
                    "the declared read rules have no matching scalar source occurrences")))
        elif schema.name == "latency" and contract.latency_goals and artifact_kind == "behavior":
            selections.append(GoalPolicySelection(
                schema, INAPPLICABLE, reason=VersionedReason(
                    "untimed-behavior-no-physical-latency", EVALUATION_GOAL_POLICY_VERSION,
                    "the task declares physical latency, but untimed Behavior has no "
                    "clock-edge or global-time observations; its invocation count is not physical time")))
        else:
            declared = schema.name in _DECLARED_SCHEMAS
            selections.append(GoalPolicySelection(
                schema,
                INAPPLICABLE,
                reason=VersionedReason(
                    "no-task-goal-declarations" if declared else "no-source-instances",
                    EVALUATION_GOAL_POLICY_VERSION,
                    ("the built-in profile has no task predicates or monitors declared "
                     "for this schema" if declared else
                     "the implemented frontend or applicable protocol emitter "
                     "declares no instances of this schema"),
                ),
            ))

    policy = GoalProviderRegistry(schemas, tuple(providers)).freeze(
        EVALUATION_GOAL_POLICY_VERSION, tuple(selections))
    policy.validate(CONCRETE_PHASE)
    for capability in (DECLARATION, CONCRETE_HIT):
        policy.reconcile_all(capability, {
            provider.identity: provider.binding(capability).adapter
            for provider in policy.enabled_providers(capability)
        })
    return policy
