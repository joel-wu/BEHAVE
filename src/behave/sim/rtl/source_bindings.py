"""Bind retained RTLIL occurrences to source control-entry events.

This preparation layer does not enable a Driver capability or parse submitted
RTL on the host. It consumes the whole-document compiler-origin check and the
catalogue's own region table. A source assignment is not an elaborated action:
an action is observed by entry into the region that executes it.

Repeated source controls require hierarchy / loop / call contexts. Until those
contexts are bound, this entry point refuses the complete mapping; it must not
broadcast one source event to all elaborated copies or drop their goals.
"""

from dataclasses import dataclass, replace
from functools import cached_property, lru_cache
from itertools import pairwise

from behave.core import runtime_defaults as defaults

from ..core.catalogue import OccurrenceKey
from ._rtlil_sites import process_syncs, rtlil_site_document, runtime_syncs, scalar_input_aliases
from .catalogue import Site, goal_id_of, occurrence_key_of
from .control_condition import ControlCondition
from .source_contexts import execution_contexts
from .source_encoding import SourceEncoding
from .source_expression_binding import expression_cells, expression_levels
from .source_loops import MAX_EVENTS
from .source_origins import compiler_origins
from .source_plan import SourcePlan
from .source_projection import SourceOutcome, project_source

_BYTE_BITS = tuple(tuple(bit for bit in range(8) if value & (1 << bit))
                   for value in range(256))


@dataclass(frozen=True, slots=True)
class BoundOccurrence:
    occurrence: OccurrenceKey
    goal: str
    event: int
    ancestors: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class NativeHits:
    occurrences: frozenset[OccurrenceKey]
    goals: frozenset[str]


@dataclass(frozen=True, slots=True)
class _EventRoute:
    requirements: tuple[tuple[str, frozenset[int]], ...]
    hits: NativeHits


def _byte_masks(indices):
    """Sparse byte masks: work scales with mapped entries, not catalogue width."""
    masks = {}
    for index in indices:
        byte, bit = divmod(index, 8)
        masks[byte] = masks.get(byte, 0) | (1 << bit)
    return tuple(sorted(masks.items()))


@dataclass(frozen=True, slots=True)
class EventCoordinate:
    """A source event in an exact HDL context under the logical root TOP.

    The launcher's name for that root is a separate native build parameter.
    """
    scope: str
    source_event: int


@dataclass(frozen=True)
class EventBindings:
    event_count: int
    occurrences: tuple[BoundOccurrence, ...]
    coordinates: tuple[EventCoordinate, ...] = ()
    projections: tuple[SourceOutcome, ...] = ()

    def __reduce__(self):
        # Trace workers receive the immutable mapping, not the coordinator's
        # process-local indexes or bound LRU functions after baseline replay.
        return type(self), (self.event_count, self.occurrences,
                            self.coordinates, self.projections)

    @property
    def catalogue_pairs(self):
        return tuple((item.occurrence, item.goal) for item in (*self.occurrences, *self.projections))

    @cached_property
    def goal_catalogue(self):
        return tuple(sorted({goal for _, goal in self.catalogue_pairs}))

    @cached_property
    def _bitmap_routes(self):
        positions = {goal: index for index, goal in enumerate(self.goal_catalogue)}
        # Derive both representations from the same occurrence-complete routes.
        # No per-event full-width masks or possible interval enumeration.
        return {
            event: (
                tuple((kind, _byte_masks(parents)) for kind, parents in route.requirements),
                _byte_masks(positions[goal] for goal in route.hits.goals),
            )
            for event, route in self._event_routes.items()
        }

    def decode_bitmap(self, data: bytes) -> bytes:
        """Validate one complete event interval and project its goal bitmap.

        Native event coordinates remain in the original evidence. Named
        occurrence projection for audit still uses ``decode`` below.
        """
        if type(data) is not bytes or len(data) != (self.event_count + 7) // 8:
            raise ValueError("native event bitset has the wrong width")
        if self.event_count % 8 and data[-1] >> (self.event_count % 8):
            raise ValueError("unknown native source event")
        return self._bitmap_decoder(data)

    @cached_property
    def _bitmap_decoder(self):
        # Only identical, immutable bytes under this exact binding can reuse
        # ancestry validation and projection. Exceptions are not cached. The
        # native collector still validates every interval's sequence/callbacks.
        payload_bytes = (self.event_count + 7) // 8 + (len(self.goal_catalogue) + 7) // 8
        capacity = min(defaults.SIM_RTL_EVENT_PROJECTION_CACHE_ENTRIES,
                       defaults.SIM_RTL_EVENT_PROJECTION_CACHE_BYTES // max(1, payload_bytes))
        # Wrap the bound function per instance: do not hash the entire mapping
        # on each call or retain observer plans in a process-global LRU.
        return lru_cache(maxsize=capacity)(self._project_bitmap)

    def _project_bitmap(self, data: bytes) -> bytes:
        goals = bytearray((len(self.goal_catalogue) + 7) // 8)
        project = self._byte_decoder
        for index, value in enumerate(data):
            if not value:
                continue
            requirements, masks = project(index, value)
            for kind, parents in requirements:
                if any(data[byte] & mask != mask for byte, mask in parents):
                    raise ValueError(f"a native {kind} is missing its enclosing control entry")
            for byte, mask in masks:
                goals[byte] |= mask
        return bytes(goals)

    @cached_property
    def _byte_decoder(self):
        # Full vectors can differ every cycle while most bytes name the same
        # active statements. Memoize sparse routes, not validation: enclosing
        # event bits still come from THIS interval, including other bytes.
        return lru_cache(maxsize=defaults.SIM_RTL_EVENT_BYTE_PROJECTION_CACHE_ENTRIES)(
            self._project_byte)

    def _project_byte(self, index, value):
        required, goals = {}, {}
        routes = self._bitmap_routes
        for bit in _BYTE_BITS[value]:
            route = routes.get(index * 8 + bit)
            if route is None:
                continue
            requirements, masks = route
            for kind, parents in requirements:
                joined = required.setdefault(kind, {})
                for byte, mask in parents:
                    joined[byte] = joined.get(byte, 0) | mask
            for byte, mask in masks:
                goals[byte] = goals.get(byte, 0) | mask
        return (tuple((kind, tuple(parents.items())) for kind, parents in required.items()),
                tuple(goals.items()))

    @cached_property
    def _event_routes(self):
        """Index the immutable mapping once, shared by all replay intervals.

        Several source occurrences can use the same native event, and every
        occurrence's ancestor requirements remain separate. This derived index
        is not part of the serialized mapping or its identity.
        """
        routes = {}
        for index, binding in enumerate(self.occurrences):
            routes.setdefault(binding.event, []).append((index, binding.ancestors))
        for index, item in enumerate(self.projections, start=len(self.occurrences)):
            for event, parents in zip(item.events, item.ancestors, strict=True):
                routes.setdefault(event, []).append((index, parents))
        entries = (*self.occurrences, *self.projections)
        compiled = {}
        for event, targets in routes.items():
            required = {"event": set(), "source outcome": set()}
            active = set()
            for index, parents in targets:
                kind = "event" if index < len(self.occurrences) else "source outcome"
                required[kind].update(parents)
                active.add(index)
            # Every route for this event fires together. Requiring the union
            # of its ancestors is exactly the conjunction of the old checks.
            # Keep the two diagnostic kinds, and every distinct occurrence.
            compiled[event] = _EventRoute(
                tuple((kind, frozenset(parents)) for kind, parents in required.items() if parents),
                NativeHits(frozenset(entries[index].occurrence for index in active),
                           frozenset(entries[index].goal for index in active)))
        return compiled

    def decode(self, events) -> NativeHits:
        """Project one collector interval without collapsing occurrence identity.

        Parent entry is necessary for a nested event. The union of an interval
        may contain both arms (several native activations), so mutual exclusion
        is deliberately not imposed on the collected set. Transport bounds and
        phase completeness remain the collector's separate responsibilities.
        """
        seen = set()
        for event in events:
            if type(event) is not int or not 0 <= event < self.event_count:
                raise ValueError("unknown native source event")
            seen.add(event)
        if not seen:
            return NativeHits(frozenset(), frozenset())
        occurrences, goals = set(), set()
        routes = self._event_routes
        for event in seen:
            route = routes.get(event)
            if route is None:
                continue
            for kind, parents in route.requirements:
                if not seen.issuperset(parents):
                    raise ValueError(f"a native {kind} is missing its enclosing control entry")
            occurrences.update(route.hits.occurrences)
            goals.update(route.hits.goals)
        return NativeHits(frozenset(occurrences), frozenset(goals))


@dataclass(frozen=True, slots=True)
class SourceExecutionBindings:
    native: EventBindings
    encoding: SourceEncoding | None


def bind_declared_source_events(plan: SourcePlan, *, filename: str, schemas=None) -> EventBindings:
    """Bind source callbacks to Slang's selected instance/process contexts.

    Source outcomes are not synthesized actions: observing them needs no RTLIL
    or symbolic relation. The compiler binder below remains responsible for
    elaborated locations and for encoding source events in a relation.
    """
    if plan.declarations is None:
        raise ValueError("native source binding requires typed declarations")
    plan.native()
    scopes = {}
    for context in plan.declarations.contexts:
        if context.scope is None:
            raise ValueError("the selected source context has no native scope binding")
        scopes.setdefault(context.process_start, set()).add("TOP." + ".".join(context.scope))
    origins = {origin.key: origin for origin in plan.controls}
    coordinates = []
    for index, invocation in enumerate(plan.invocations):
        event = plan.events[invocation.source_event]
        root = plan.events[event.ancestors[0]] if event.ancestors else event
        for scope in scopes.get(origins[root.origin].start, ()):
            coordinates.append(EventCoordinate(scope, index))
    coordinates = tuple(sorted(coordinates, key=lambda item: (item.scope, item.source_event)))
    bindings = EventBindings(len(coordinates), (), coordinates)
    from .source_goals import SCHEMAS

    projection = project_source(plan, bindings, filename=filename,
                                schemas=SCHEMAS if schemas is None else schemas)
    projection.require_complete()
    return replace(bindings, projections=projection.outcomes)


def _source_range(plan, filename, start, end):
    first, last = (plan.compiler.original_position(offset) for offset in (start, end))
    return f"{filename}:{first[0]}.{first[1]}-{last[0]}.{last[1]}"


def _choice_of(origin, row, ranges, by_range, switch_src):
    """Require exact compiler anchors and explicit default/if polarity."""
    outcome = row["outcome"]
    if origin.kind == "ConditionalStatement":
        if outcome not in {"1'1", "default", "no-match"}:
            raise ValueError("an if control changed its Boolean arm convention")
        key = "true" if outcome == "1'1" else "false"
    elif origin.kind == "CaseStatement":
        if outcome in {"default", "no-match"}:
            key = "default" if "default" in ranges else "no-match"
        else:
            candidates = {by_range[part] for part in row["src"].split("|") if part in by_range}
            if len(candidates) != 1:
                raise ValueError("a case arm has no unique exact source anchor")
            key = next(iter(candidates))
            if key in {"default", "no-match"}:
                raise ValueError("a case arm changed its default role")
    else:
        raise ValueError("a switch has no source choice semantics")
    # Yosys can replace a pruned else/default body with an empty fallback,
    # whose provenance is the switch itself. Its meaning is still precisely
    # the complement of the retained explicit arms, not a nearby statement.
    fallback = outcome in {"default", "no-match"} and (
        row["src"] == switch_src
        # Yosys adds a source-less fallback for exhaustive enum cases. It is
        # the complement of the exactly bound arms, not a new written arm.
        or key == "no-match" and row["src"] == "?")
    if not fallback and ranges[key] not in row["src"].split("|"):
        raise ValueError("a compiler arm does not name its exact source choice")
    return key


def bind_source_events(plan: SourcePlan, original: str, annotated: str,
                       *, filename: str, scoped: str | None = None) -> EventBindings:
    """Bind all formal occurrences or refuse; never change the catalogue."""
    return _bind_source_execution(plan, original, annotated, filename=filename,
                                  scoped=scoped, encode=False).native


def bind_source_execution(plan: SourcePlan, original: str, annotated: str,
                          *, filename: str, scoped: str | None = None) -> SourceExecutionBindings:
    """Bind native coordinates and symbolic conditions in the same traversal.

    Native-only preparation uses the same binder without allocating symbolic
    metadata. Both paths require the identical compiler-origin/context checks.
    Neither constructs a solver or changes the public declaration.
    """
    return _bind_source_execution(plan, original, annotated, filename=filename,
                                  scoped=scoped, encode=True)


def _bind_source_execution(plan, original, annotated, *, filename, scoped, encode):
    plan.require_compiler_binding()
    controls = compiler_origins(plan, original, annotated, filename=filename)
    scopes, cell_scopes = execution_contexts(annotated, scoped) if scoped is not None else ({}, {})
    iterations = _control_iterations(plan, controls, scopes)
    if scoped is None and any(c.process.startswith("$flatten\\") for c in controls):
        raise ValueError("elaborated source events require occurrence contexts")
    by_line = {control.line: control for control in controls}
    origins = {origin.key: origin for origin in plan.controls}
    ranges, anchors = {}, {}
    for origin in plan.controls:
        ranges[origin.key] = {choice.key: _source_range(plan, filename, choice.start, choice.end)
                              for choice in origin.choices}
        anchors[origin.key] = {location: key for key, location in ranges[origin.key].items()}
        if len(anchors[origin.key]) != len(origin.choices):
            raise ValueError("source choices have ambiguous provenance anchors")
    events = {}
    for event in plan.events:
        if event.role == "process" or event.choice is not None:
            key = (event.origin, event.choice)
            events.setdefault(key, []).append(event.ordinal)
    invocation_ids = {(call.source_event, call.iterations): index
                      for index, call in enumerate(plan.invocations)}
    if len(invocation_ids) != len(plan.invocations):
        raise ValueError("duplicate source invocation coordinate")
    invocation_owners = {}

    def invoked(control, choice, parents=()):
        """Resolve one copy by complete source/control ancestry, not inline order."""
        values = iterations[control.line]
        candidates = []
        for event in events.get((control.origin, choice), ()):
            entry = plan.events[event]
            if len(values) != len(entry.contexts):
                continue
            ancestor_ids = tuple(invocation_ids.get(
                (parent, values[:len(plan.events[parent].contexts)])) for parent in entry.ancestors)
            if ancestor_ids == parents and (event, values) in invocation_ids:
                candidates.append(invocation_ids[(event, values)])
        if len(candidates) != 1:
            raise ValueError("compiler/source control ancestry has no unique invocation")
        invocation, = candidates
        key = (scopes.get(control.process, ""), invocation)
        owner = (control.module, control.process, control.line)
        if invocation_owners.setdefault(key, owner) != owner:
            raise ValueError("elaborated source events require distinct occurrence contexts")
        return invocation

    by_root = {}
    if encode or any(scopes.values()):
        for index, call in enumerate(plan.invocations):
            event = plan.events[call.source_event]
            root = event.ancestors[0] if event.ancestors else event.ordinal
            by_root.setdefault(root, []).append(index)
    coordinates = ()
    if any(scopes.values()):
        tops = {c.module for c in controls}
        if len(tops) != 1:
            raise ValueError("native contexts require one flattened top module")
        top, = tops
        expanded = set()
        for control in controls:
            if control.kind != "process":
                continue
            root, = events[(control.origin, None)]
            path = ".".join(filter(None, ("TOP", top, scopes[control.process])))
            if len(expanded) + len(by_root[root]) > MAX_EVENTS:
                raise ValueError("native context expansion exceeds the collector storage bound")
            expanded.update((path, event) for event in by_root[root])
        coordinates = tuple(EventCoordinate(*pair) for pair in sorted(expanded))
    coordinate_ids = {(c.scope, c.source_event): index for index, c in enumerate(coordinates)}

    def contextual(event, process):
        if not coordinates:
            return event
        path = ".".join(filter(None, ("TOP", process.module, scopes[process.process])))
        return coordinate_ids[(path, event)]
    document = rtlil_site_document(original)
    rows, regions = document["sites"], document["regions"]
    arm_events, emitted = {}, {}
    for row, region in zip(rows, regions, strict=True):
        if row["kind"] != "case-arm":
            continue
        line, label = region["arms"][-1]
        control = by_line[line]
        choice = _choice_of(origins[control.origin], row,
                            ranges[control.origin], anchors[control.origin], control.src)
        arm = (line, label)
        if arm in arm_events:
            raise ValueError("duplicate elaborated arm identity")
        process = by_line[region["process"]]
        parents = (invoked(process, None),
                   *(arm_events[tuple(parent)] for parent in region["arms"][:-1]))
        arm_events[arm] = invoked(control, choice, parents)
        emitted.setdefault(line, []).append(choice)
    # Exact positions alone do not preserve priority. Yosys moves default to
    # the end and may prune constant-dead arms. The retained arms must be an
    # ordered subsequence; completeness is against the ORIGINAL catalogue,
    # not a new catalogue minted from every source statement.
    for line, control in by_line.items():
        if control.kind != "switch":
            continue
        choices = origins[control.origin].choices
        expected = [c.key for c in choices if c.key not in {"default", "no-match"}]
        expected += [c.key for c in choices if c.key in {"default", "no-match"}]
        order = {key: index for index, key in enumerate(expected)}
        ranks = [order[key] for key in emitted.get(line, [])]
        if any(first >= second for first, second in pairwise(ranks)):
            raise ValueError("compiler/source choices differ in priority")
    bindings, conditions = [], {}
    for row, region in zip(rows, regions, strict=True):
        process = by_line[region["process"]]
        if process.kind != "process":
            raise ValueError("a structural region has no process entry")
        chain = (invoked(process, None),
                 *(arm_events[tuple(arm)] for arm in region["arms"]))
        call = plan.invocations[chain[-1]]
        event = plan.events[call.source_event]
        expected_parents = tuple(invocation_ids[(parent, call.iterations[:len(plan.events[parent].contexts)])]
                                 for parent in event.ancestors)
        if expected_parents != chain[:-1]:
            raise ValueError("compiler/source control ancestry differs")
        site = Site.from_json(row)
        expanded_chain = tuple(contextual(event, process) for event in chain)
        bindings.append(BoundOccurrence(occurrence_key_of(site), goal_id_of(site),
                                        expanded_chain[-1], expanded_chain[:-1]))
        if encode:
            condition = ControlCondition.of_site(site)
            if conditions.setdefault(expanded_chain[-1], condition) != condition:
                raise ValueError("one source invocation has different compiler control conditions")
    if len({binding.occurrence for binding in bindings}) != len(bindings):
        raise ValueError("duplicate structural occurrence identity")
    native = EventBindings(len(coordinates) if coordinates else len(plan.invocations), tuple(bindings), coordinates)
    encoding = None
    if encode:
        phases, aliases = process_syncs(original), scalar_input_aliases(original)
        cells = expression_cells(original, cell_scopes)
        loops = {(loop.start, loop.end): loop for loop in plan.loops}
        for process in controls:
            if process.kind != "process":
                continue
            process_id = invoked(process, None)
            root_id = contextual(process_id, process)
            names = aliases.get(process.module, {})
            triggers = tuple((edge, names.get(signal, signal))
                             for edge, signal in runtime_syncs(phases[(process.module, process.process)]))
            root_condition = ControlCondition(triggers)
            if conditions.setdefault(root_id, root_condition) != root_condition:
                raise ValueError("a source process has inconsistent compiler trigger conditions")
            source_root = plan.invocations[process_id].source_event
            for invocation in by_root[source_root]:
                call = plan.invocations[invocation]
                event = plan.events[call.source_event]
                if not event.role.startswith(("loop-", "expression-", "boolean-")):
                    continue
                parent = event.ancestors[-1]
                parent_id = invocation_ids[(parent, call.iterations[:len(plan.events[parent].contexts)])]
                condition = conditions.get(contextual(parent_id, process))
                if event.role.startswith(("expression-", "boolean-")):
                    levels = expression_levels(plan, event, process, cells, scopes,
                                               names, filename=filename)
                    if condition is not None and levels is not None:
                        conditions[contextual(invocation, process)] = replace(
                            condition, levels=(*condition.levels, *levels))
                    continue
                loop = loops[(event.start, event.end)]
                enabled = bool(loop.values) == (event.choice == "nonzero")
                # Literal, nonwrapping iteration is proved by SourcePlan. The
                # impossible entry outcome is false even if its parent is not
                # encoded. The possible outcome still needs the complete guard.
                if not enabled:
                    condition = replace(root_condition, enabled=False)
                conditions[contextual(invocation, process)] = condition
        encoding = SourceEncoding(
            plan,
            tuple(conditions.get(event) for event in range(native.event_count)),
            tuple(c.source_event for c in coordinates) if coordinates else tuple(range(native.event_count)))
    return SourceExecutionBindings(native, encoding)


def bind_source_goals(plan, bindings, *, filename, schemas=None):
    """Attach the source schemas selected by the frozen catalogue.

    Source choices replace compiler choices only when explicitly selected.
    Failure-only attachment preserves every existing structural occurrence.
    Selection never depends on observed hits, constants or solver support.
    """
    from .source_goals import SCHEMAS

    schemas = SCHEMAS if schemas is None else schemas
    projection = project_source(plan, bindings, filename=filename, schemas=schemas)
    projection.require_complete()
    retained = tuple(item for item in bindings.occurrences
                     if "control-choice" not in schemas or item.occurrence.path[2] == "location")
    return replace(bindings, occurrences=retained, projections=projection.outcomes)


def _control_iterations(plan, controls, scopes):
    """Match complete finite control expansions within each retained process.

    This uses the locked frontend's ordered procedural unrolling, not suffixes
    of generated wire names or the number of times a public goal occurs. Any
    missing/reordered source origin or different control ancestry refuses the
    mapping. Runtime callbacks independently select the actual counter values.
    """
    loop_roots = {plan.events[event.ancestors[0]].origin
                  for event in plan.events if event.contexts and event.ancestors}
    groups = {}
    for control in controls:
        groups.setdefault((control.module, control.process), []).append(control)
    iterations = {}
    for group in groups.values():
        root = group[0]
        if root.kind != "process":
            raise ValueError("an elaborated control has no process context")
        if root.origin in loop_roots:
            expected = [visit for visit in plan.visits if visit.process == root.origin]
            if [control.origin for control in group] != [visit.origin for visit in expected]:
                raise ValueError("compiler/source loop control expansion differs")
            iterations.update((control.line, visit.iterations)
                              for control, visit in zip(group, expected, strict=True))
        else:
            iterations.update((control.line, ()) for control in group)
    # Repeated helpers may share an origin and loop coordinate. The caller
    # resolves them using the full compiler region ancestry, independently of
    # compiler traversal order. Ambiguous paths still cannot acquire a binding.
    return iterations
