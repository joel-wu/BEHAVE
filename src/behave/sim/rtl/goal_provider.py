"""RTL-owned standard schema registration; no parsing or execution."""

from ..core.catalogue import CatalogueManifest, catalogue_subset
from ..core.goal_provider import (
    CONCRETE_HIT,
    DECLARATION,
    EXACT_BOUNDED_HIT,
    LOCAL_GUIDANCE,
    RTL_EXACT_BOUNDED_POLICY,
    RTL_LOCAL_GUIDANCE_POLICY,
    builtin_schema_providers,
    standard_goal_schemas,
)
from ..core.refusal import Refusal, Refused
from .source_declarations import VERSION as SOURCE_DECLARATIONS_VERSION
from .source_goals import SCHEMAS, is_source_goal

OWNER = "rtl-structural"
_FAMILIES = {
    "location": "location",
    "case-arm": "control-choice",
    **{schema: schema for schema in SCHEMAS},
}


def source_enumeration_gaps(catalogue: CatalogueManifest) -> frozenset[str]:
    """Legacy action rows cannot establish the source-policy denominator.

    Keep their diagnostic providers, but do not turn missing source schemas
    into declarations that no such sites exist. This also covers mixed old/new
    catalogues; ordinary source-only catalogues retain their own capabilities.
    """
    return (SCHEMAS if any(goal.provider == OWNER and not is_source_goal(goal)
                           for goal in catalogue.values()) else frozenset())


def goal_providers(catalogue: CatalogueManifest):
    """Assign canonical schemas to this frontend's already-declared rows."""
    definitions = {schema.name: schema for schema in standard_goal_schemas()}
    assignments = {}
    for goal_id, goal in catalogue.items():
        schema = _FAMILIES.get(goal.family)
        if goal.provider != OWNER or schema is None:
            raise Refused(Refusal(
                "goal-provider-builtin-owner-unknown",
                "the frontend manifest contains a goal with no registered standard provider owner",
                goal=goal_id, provider=goal.provider, family=goal.family, goal_class=goal.cls))
        assignments[goal_id] = definitions[schema]
    retained = {goal for goal in catalogue if not is_source_goal(catalogue[goal])}
    providers = list(builtin_schema_providers(
        catalogue_subset(catalogue, retained), assignments, name="rtl", version=3,
        capabilities=(
            (DECLARATION, "rtl-structural-catalogue/3"),
            (CONCRETE_HIT, "rtl-structural-replay/3"),
            (LOCAL_GUIDANCE, RTL_LOCAL_GUIDANCE_POLICY),
            (EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY),
        ),
    ))
    # Declaration/native observation remain independent of local lowering.
    # The source-local worker binds actual recorded boundaries; unsupported
    # grammar or event domains refuse that search, not baseline scoring.
    # Whole-source bounded analysis has its own worker and admission proofs;
    # local guidance alone never supplies those proofs or native certificates.
    for schema in sorted(SCHEMAS):
        goals = {goal for goal in catalogue if is_source_goal(catalogue[goal])
                 and catalogue[goal].family == schema}
        capabilities = ((DECLARATION, SOURCE_DECLARATIONS_VERSION),
                        (CONCRETE_HIT, "rtl-source-events/native/2"),
                        (LOCAL_GUIDANCE, RTL_LOCAL_GUIDANCE_POLICY),
                        (EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY))
        providers.extend(builtin_schema_providers(
            catalogue_subset(catalogue, goals), assignments, name="rtl-source", version=2,
            capabilities=capabilities))
    return tuple(providers)
