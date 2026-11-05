"""Behavior-owned standard schema registration; no parsing or execution."""

from ..core.catalogue import SAFETY, CatalogueManifest
from ..core.goal_provider import (
    BEHAVIOR_EXACT_BOUNDED_POLICY,
    BEHAVIOR_LOCAL_GUIDANCE_POLICY,
    CONCRETE_HIT,
    DECLARATION,
    EXACT_BOUNDED_HIT,
    LOCAL_GUIDANCE,
    builtin_schema_providers,
    standard_goal_schemas,
)
from ..core.refusal import Refusal, Refused

OWNER = "python-structural"
_FAMILIES = {
    "location": "location",
    "decision": "control-choice",
    "condition": "boolean-expression",
    "loop": "loop-activation",
    "operation-event": "operation-event",
    "value-observation": "value-observation",
}


def goal_providers(catalogue: CatalogueManifest):
    """Assign canonical schemas to this frontend's already-declared rows."""
    definitions = {schema.name: schema for schema in standard_goal_schemas()}
    assignments = {}
    for goal_id, goal in catalogue.items():
        schema = "artifact-failure" if goal.cls == SAFETY else _FAMILIES.get(goal.family)
        if goal.provider != OWNER or schema is None:
            raise Refused(Refusal(
                "goal-provider-builtin-owner-unknown",
                "the frontend manifest contains a goal with no registered standard provider owner",
                goal=goal_id, provider=goal.provider, family=goal.family, goal_class=goal.cls))
        assignments[goal_id] = definitions[schema]
    return builtin_schema_providers(
        catalogue, assignments, name="behavior", version=1,
        capabilities=(
            (DECLARATION, "python-structural-catalogue/1"),
            (CONCRETE_HIT, "python-structural-replay/2"),
            (LOCAL_GUIDANCE, BEHAVIOR_LOCAL_GUIDANCE_POLICY),
            (EXACT_BOUNDED_HIT, BEHAVIOR_EXACT_BOUNDED_POLICY),
        ),
    )
