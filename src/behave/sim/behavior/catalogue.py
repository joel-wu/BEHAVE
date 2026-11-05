"""The Behavior frontend's source-stable pre-generation goal manifest.

The selected source closure includes resolved callable values as well as direct calls
and declares their structural and partial-operation safety goals. Lowering accounts
for each declaration by producing a guard or by
proving the operation total on its finite carrier, but that proof never adds or removes
a public goal.  Replay, guidance and bounded analysis therefore share one immutable
catalogue whose identity does not depend on a range plan or concrete schedule.
"""

from __future__ import annotations

from ..core.catalogue import (
    COVERAGE,
    SAFETY,
    CatalogueManifest,
    Goal,
    catalogue_subset,
)
from ..core.refusal import Refusal, Refused
from ..core.source_rules import EMPTY_SOURCE_RULES
from . import goals as source_goals
from ..symbolic import ir
from .source_plan import PROVIDER

__all__ = [
    "frontend_catalogue",
    "source_frontend_catalogue",
]


def _refuse(kind, why, **detail):
    raise Refused(Refusal(kind, why, **detail))


def source_frontend_catalogue(model, source, file="behavior.py", *,
                              source_rules=EMPTY_SOURCE_RULES):
    """The source-owned manifest, without lowering or native preparation."""
    from .source_plan import plan_source

    return plan_source(model, source, file, source_rules=source_rules).manifest


def _procedure_goal_sets(proc, limits):
    from ..symbolic.procedure_contract import ANALYSIS_ERROR_CODES

    if type(proc) is not ir.Procedure:
        raise TypeError("a Behavior catalogue requires the exact Procedure type")
    fail_commands = tuple(command for command in ir.walk(proc.body)
                          if isinstance(command, ir.Fail) and command.goal)
    # These guards stop the representation, not the source program. Use the
    # same error classification as ProcedureContract, never goal-ID spelling.
    limits = frozenset(str(goal) for goal in limits) | {
        command.goal for command in fail_commands
        if command.code in ANALYSIS_ERROR_CODES}
    discharged = set(getattr(proc, "discharged", ()))
    declared = (
        ir.goals_of(proc.body) | set(proc.impossible) | discharged
    ) - limits
    failures = {
        command.goal
        for command in fail_commands if command.goal not in limits
    }
    return declared, failures, discharged


def check_source_producers(proc, manifest, *, limits=()):
    """Validate one component's producers and return its declared goal IDs.

    Multi-handler callers check the union for completeness. No catalogue is
    constructed here: schemas and occurrence identities belong to the source.
    """

    if type(manifest) is not CatalogueManifest:
        raise TypeError("producer accounting needs an exact CatalogueManifest")
    declared, failures, discharged = _procedure_goal_sets(proc, limits)
    unknown = sorted(set(declared) - set(manifest))
    if unknown:
        _refuse(
            "behavior-catalogue-goal-undeclared",
            "a lowered Behavior goal is absent from the selected source closure",
            goals=unknown[:8],
        )

    wrong_failures = sorted(
        goal for goal in failures if manifest[goal].cls != SAFETY)
    missing_failures = sorted(
        goal for goal in declared
        if manifest[goal].cls == SAFETY
        and goal not in failures and goal not in discharged)
    if wrong_failures or missing_failures:
        _refuse(
            "behavior-catalogue-goal-class-conflict",
            "the lowered command and source catalogue disagree on which goals are "
            "failures",
            coverage_as_failure=wrong_failures[:8],
            safety_without_failure_or_discharge=missing_failures[:8],
        )

    return declared


def _project_source_frontend(proc, manifest, *, limits=(), complete=False):
    """Retain the source manifest, or project a diagnostic component's subset."""
    declared = check_source_producers(proc, manifest, limits=limits)
    if declared == set(manifest):
        return manifest
    if complete:
        _refuse(
            "behavior-frontend-catalogue-incomplete",
            "the lowered procedure does not account for every source goal",
            missing=sorted(set(manifest) - declared)[:8])
    return catalogue_subset(manifest, declared)


def frontend_catalogue(proc, source=None, file="behavior.py", *, limits=(), cls=None,
                       source_rules=EMPTY_SOURCE_RULES):
    """Freeze the structural and safety goals of one selected Behavior step.

    ``proc`` is the completed lowering, after its declaration/producer audits.  Every
    source safety site is either emitted or recorded in ``proc.discharged``; both remain
    in the public catalogue.  A hand-built procedure with no source retains typed goals
    but cannot claim source-occurrence evidence.
    """

    declared, failures, _discharged = _procedure_goal_sets(proc, limits)
    if source is not None:
        from .extract import source_model

        manifest = source_frontend_catalogue(
            source_model(source, file, cls=cls), source, file, source_rules=source_rules)
        return _project_source_frontend(proc, manifest, limits=limits)

    goals = []
    for goal_id in sorted(declared):
        provenance = dict(proc.provenance.get(goal_id, {}))
        family = provenance.get("kind")
        if family is None:
            # A hand-built procedure has no source occurrence authority. Preserve its
            # typed goal for interpreter/encoder tests, but make no occurrence claim.
            family = "failure" if goal_id in failures else "structural"
        is_failure = goal_id in failures
        goals.append(Goal(
            goal_id,
            SAFETY if is_failure else COVERAGE,
            family=str(family),
            provider=PROVIDER,
            event="failure" if is_failure else None,
            provenance=provenance or {"file": file},
            policy=f"{source_goals.POLICY_VERSION}/{source_goals.POLICY}",
        ))

    return CatalogueManifest(goals)
