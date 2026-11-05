"""Project typed source declarations into the common goal/occurrence records.

This is the source part of the RTL catalogue, not an execution-derived inventory.
Neither compiler pruning nor observation/solver support changes these rows.
Source locations include statement, initializer and continuous-driver entries.
"""

import hashlib

from ..core.catalogue import COVERAGE, SAFETY, CatalogueManifest, Goal, OccurrenceKey
from ..core.record_value import canonical_json
from .source_declarations import VERSION as DECLARATION_VERSION

POLICY = "rtl-source-events/2"
OWNER = "rtl-structural"
SCHEMAS = frozenset({"location", "control-choice", "boolean-expression", "loop-activation", "artifact-failure",
                     "operation-event", "value-observation"})
# Historical action diagnostics add source failures to their old action rows.
# Public source catalogues use the full SCHEMAS set, never this legacy subset.
DEFAULT_SOURCE_SCHEMAS = frozenset({"artifact-failure"})


def is_source_goal(goal):
    return (goal.provider == OWNER and goal.family in SCHEMAS
            and goal.id.startswith("rtl-source:"))


def goal_of(site, outcome, *, filename):
    coordinates = {
        "file": filename, "start": site.start, "end": site.end,
        "schema": site.schema, "evaluation": site.evaluation,
    }
    if site.operation_rules:
        coordinates["operation"] = next(rule.as_dict() for rule in site.operation_rules
                                         if rule.goal.name == outcome)
    if site.read_rules:
        coordinates["read"] = next(rule.as_dict() for rule in site.read_rules if rule.name == outcome)
    digest = hashlib.sha256(canonical_json(coordinates).encode()).hexdigest()
    return Goal(
        f"rtl-source:{digest}:{outcome}",
        SAFETY if site.schema == "artifact-failure" else COVERAGE,
        family=site.schema, provider=OWNER, event=outcome,
        provenance=coordinates, policy=f"{POLICY}/{DECLARATION_VERSION}")


def occurrence_of(site, occurrence, outcome, *, filename):
    # Calls and declaration contexts stay distinct even when their source goal
    # is shared. AST addresses, generated wire ordinals and hit counts stay out.
    path = (filename, site.schema, site.start, site.end,
            "context", *occurrence.context,
            "calls", *(coordinate for call in occurrence.calls for coordinate in call))
    return OccurrenceKey(OWNER, path, outcome)


def manifest_of(declarations, *, filename, schemas=SCHEMAS):
    goals = {(index, outcome): goal_of(site, outcome, filename=filename)
             for index, site in enumerate(declarations.sites) if site.schema in schemas
             for outcome in site.outcomes}
    return CatalogueManifest(tuple(goals.values()), occurrences=tuple(
        (occurrence_of(declarations.sites[item.site], item, outcome, filename=filename),
         goals[(item.site, outcome)].id)
        for item in declarations.occurrences
        if declarations.sites[item.site].schema in schemas
        for outcome in declarations.sites[item.site].outcomes))
