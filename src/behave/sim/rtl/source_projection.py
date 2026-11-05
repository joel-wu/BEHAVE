"""One source-declaration projection for native events and symbolic conditions.

The public source occurrence is an instance/process/call context. Several
unrolled entries can implement it; their hits are ORed, not confused with an
absent implementation. A proved empty enclosing loop is the only empty image.
"""

from dataclasses import dataclass

from ..core.catalogue import CatalogueManifest, OccurrenceKey
from ..core.refusal import Refusal
from .source_goals import SCHEMAS, manifest_of, occurrence_of
from .source_plan import SourcePlan


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    occurrence: OccurrenceKey
    goal: str
    events: tuple[int, ...]
    issue: str | None = None
    ancestors: tuple[tuple[int, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class SourceProjection:
    plan: SourcePlan
    invocations: tuple[int, ...]
    catalogue: CatalogueManifest
    outcomes: tuple[SourceOutcome, ...]

    def require_complete(self):
        for outcome in self.outcomes:
            if outcome.issue:
                raise ValueError(f"{outcome.issue}: {outcome.goal}; {outcome.occurrence.as_dict()}")

    def predicates(self, encoding, explorer, *, active_events=None):
        """Per-occurrence formulas; never merge distinct contexts before audit."""
        import z3

        if encoding.plan is not self.plan or encoding.invocations != self.invocations:
            raise ValueError("source projection and encoding belong to different invocation plans")
        result = {}
        for outcome in self.outcomes:
            terms = [encoding.predicate_for(event, explorer, active_events=active_events)
                     for event in outcome.events]
            issue = (Refusal("rtl-source-occurrence-unbound", outcome.issue,
                             occurrence=outcome.occurrence.as_dict()) if outcome.issue else
                     next((term for term in terms if isinstance(term, Refusal)), None))
            result[outcome.occurrence] = issue if issue is not None else z3.Or(*terms)
        return result

    def goal_predicates(self, encoding, explorer, *, active_events=None):
        """Set-valued public hits, aggregated only after occurrence attribution."""
        import z3

        occurrences = self.predicates(encoding, explorer, active_events=active_events)
        groups = {goal: [] for goal in self.catalogue}
        for outcome in self.outcomes:
            groups[outcome.goal].append(occurrences[outcome.occurrence])
        result = {}
        for goal, terms in groups.items():
            issue = next((term for term in terms if isinstance(term, Refusal)), None)
            result[goal] = issue if issue is not None else z3.Or(*terms)
        return result


def project_source(plan, bindings, *, filename, schemas=SCHEMAS):
    """Bind the entire typed declaration, including unimplemented outcomes.

    `bindings` is the already-checked compiler/native mapping. Source contexts
    are matched by selected HDL scope, exact process origin and lexical call
    stack; a same-sized or same-named neighbour is never a substitute.
    """
    if plan.declarations is None:
        raise ValueError("source projection requires typed declarations")
    declarations = plan.declarations
    catalogue = manifest_of(declarations, filename=filename, schemas=schemas)
    origins = {origin.key: origin for origin in plan.controls}
    contexts = {context.key: context for context in declarations.contexts}
    source_root = {}
    for event in plan.events:
        root = plan.events[event.ancestors[0]] if event.ancestors else event
        source_root[event.ordinal] = origins[root.origin].start
    producers = {}
    for producer in plan.declared_events:
        event = plan.events[producer.event]
        key = (producer.site, producer.outcome, source_root[event.ordinal], event.calls)
        producers.setdefault(key, []).append(event.ordinal)
    physical = {}
    if bindings.coordinates:
        coordinates = ((coordinate.scope.removeprefix("TOP."), coordinate.source_event)
                       for coordinate in bindings.coordinates)
    else:
        # A context-free native collector is valid only for the one top scope.
        scopes = {context.scope for context in declarations.contexts if context.scope is not None}
        scope = next(iter(scopes)) if len(scopes) == 1 else None
        coordinates = ((None if scope is None else ".".join(scope), index)
                       for index in range(bindings.event_count))
    coordinates = tuple(coordinates)
    coordinate_ids = {coordinate: index for index, coordinate in enumerate(coordinates)}
    invocation_ids = {(call.source_event, call.iterations): index for index, call in enumerate(plan.invocations)}
    for physical_id, (scope, invocation) in enumerate(coordinates):
        source = plan.invocations[invocation].source_event
        physical.setdefault((scope, source), []).append(physical_id)
    source_invocations = {invocation.source_event for invocation in plan.invocations}
    empty_contexts = {loop.context for loop in plan.loops if not loop.values}
    goals = dict(catalogue.occurrences)
    outcomes = []
    for item in declarations.occurrences:
        site = declarations.sites[item.site]
        if site.schema not in schemas:
            continue
        context = contexts[item.context]
        scope = None if context.scope is None else ".".join(context.scope)
        for outcome in site.outcomes:
            occurrence = occurrence_of(site, item, outcome, filename=filename)
            events, issue = [], None
            sources = producers.get((item.site, outcome, context.process_start, item.calls), ())
            if not sources:
                issue = "the declared source outcome has no native producer"
            if scope is None:
                issue = "the declared source context has no native scope binding"
            for source in sources:
                mapped = physical.get((scope, source), ()) if scope is not None else ()
                if not mapped and (source in source_invocations or
                                   not empty_contexts.intersection(plan.events[source].contexts)):
                    issue = "the source producer has no bound physical execution context"
                events.extend(mapped)
            events = tuple(sorted(set(events)))
            ancestry = []
            for event in events:
                physical_scope, invocation = coordinates[event]
                call = plan.invocations[invocation]
                parents = []
                for parent in plan.events[call.source_event].ancestors:
                    parent_invocation = invocation_ids[(parent, call.iterations[:len(plan.events[parent].contexts)])]
                    parents.append(coordinate_ids[(physical_scope, parent_invocation)])
                ancestry.append(tuple(parents))
            outcomes.append(SourceOutcome(occurrence, goals[occurrence], events, issue, tuple(ancestry)))
    return SourceProjection(plan, tuple(invocation for _, invocation in coordinates), catalogue, tuple(outcomes))
