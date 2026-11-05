"""Compile a structurally ordered source interval, without a history-pair proof.

The plan operates on the existing lowered actions, not a new HDL interpreter.
It drains blocking notifications, commits NBA queues, then drains their
notifications. A conditional action is retained even if a particular input
does not wake it. Coactive dependencies require either commuting actions or a
closed, initially active settling DAG with order-independent marks. Choosing a final value
order alone is not sufficient to preserve source-event goals.
"""

from contextlib import contextmanager
from dataclasses import dataclass, replace
from graphlib import CycleError, TopologicalSorter

from ..symbolic import ir
from ..symbolic import ranges
from ..core.refusal import Refusal, Refused
from .source_effects import (
    accesses, conditional_accesses, conflicts, observation_dependencies,
    read_masks, single_write_dependencies, single_write_inputs, total_write_inputs,
)
from .source_ir import _sequence


@dataclass(frozen=True)
class _DriveEffects:
    """A drive's access certificate and facts implied by each executing root."""

    accesses: tuple | None
    literals: tuple


class _PossibleFailure(Exception):
    """A may-fail query needs no transfer after its first possible failure."""


def _compact_boundary_arrays(value):
    """Normalize each input vector once, preserving its original cell terms."""
    if not isinstance(value, list):
        return value
    import z3
    from ..symbolic.functional_array import FunctionalArray

    if value and all(z3.is_bv(cell) for cell in value):
        return FunctionalArray.from_cells(value)
    return [_compact_boundary_arrays(cell) for cell in value]


class DirectSourceCompiler:
    """One compiler-owned machine; immutable interval plans cached by roots."""

    covers_native_orders = False
    closes_plan = True

    def __init__(self, machine):
        if machine.reports is not None or not machine.actions or machine.event_bound is None:
            raise Refused(Refusal('rtl-direct-event-regions-unsupported',
                'direct construction requires finite active/NBA regions without late reports'))
        self.machine = machine
        self.quiet_flags = (*machine.pending, *(write.valid for write, _, _ in machine.queue_edges))
        self.empty_queue_masks = tuple(name for write, rises, falls in machine.queue_edges
            if write.array is None for name in (write.mask, write.varied, rises, falls))
        self._plans = {}
        self._templates = {}
        self._relations = {}
        self._prefix_scope = False
        self._empty_queue_scope = False
        self._prefix_regions = None
        self._settling_observations = {}
        self._settling_values = {}
        self._settling_writes = {}
        self._settling_unobserved = set()
        self._context_effects = {}
        self._settled_state = False
        self._successful_only = False
        self._boundary_templates = {}
        self._fixed_point_certificate = None
        self._observed_marks = None
        self._observation_view = None
        self._phase_projection = None
        self._state_projection = None
        self._simplified = {}
        storage = set(machine.program.fields)
        self.effects = []
        self.failures = []
        self.blocking = []
        self.deferred = []
        for index, process in enumerate(machine.program.processes):
            read, write = accesses(process.activation, storage)
            # Trigger dependencies matter even when the body does not read the
            # changed field: coalescing can change which occurrences execute.
            read.update(s.storage for s in machine.subscriptions if s.process == index)
            self.effects.append((read, write))
            self.failures.append(None if any(isinstance(node, ir.Fail)
                for node in ir.walk(process.activation)) else False)
            immediate, queued = set(), set()
            for name, now, later in process.write_masks:
                for subscriber in machine.subscriptions:
                    if subscriber.storage != name:
                        continue
                    if now & subscriber.mask and subscriber.process != index:
                        immediate.add(subscriber.process)
                    if later & subscriber.mask:
                        queued.add(subscriber.process)
            self.blocking.append(immediate)
            self.deferred.append(queued)

    def for_request(self):
        """Share immutable compiled syntax, not an active request's permissions."""
        from copy import copy

        view = copy(self)
        view._prefix_scope = view._empty_queue_scope = False
        view._context_effects = {}
        return view

    def observing(self, marks):
        """Own one compiler view for precisely the query's exposed marks.

        Values, failures, subscriptions and active/NBA work stay unchanged.
        Only an unobserved mark ceases to obstruct the existing settling-DAG
        certificate. Never mutate a compiler retained by another goal batch
        or by full native-observation validation.
        """
        marks = frozenset(marks)
        if self._settled_state or marks == self._observed_marks:
            return self
        if (self._observed_marks is None and
                {mark for mark, _, _ in self.machine.occurrences} <= marks):
            return self
        if self._observation_view is None or self._observation_view[0] != marks:
            view = type(self)(self.machine)
            view._observed_marks = marks
            self._observation_view = marks, view
        return self._observation_view[1]

    def _may_fail(self, index):
        """Check local control once, without constructing a request history.

        Deferred source-domain guards remain in the executable IR. Their mere
        presence is not a possible failure: typed ranges and bounded loop
        indices can exclude them for every entry value. Unknown paths remain
        possible; no state invariant, sampled value or solver is assumed.
        """
        if self.failures[index] is None:
            process = self.machine.program.processes[index]
            def visit(command, _environment):
                if isinstance(command, ir.Fail):
                    raise _PossibleFailure

            procedure = process.procedure
            # Use the same typed, path-aware transfer as source domain checks.
            # Analyze the activation, NOT the standalone queue initializer:
            # a scheduler may enter with deferred writes already pending.
            activation = ir.Procedure(procedure.state, procedure.inputs,
                procedure.locals, procedure.outputs, process.activation, types=procedure.types)
            try:
                ranges.procedure_post(activation, dict(procedure.state_ranges),
                                      witness=visit, source_paths=True)
            except _PossibleFailure:
                self.failures[index] = True
            else:
                self.failures[index] = False
        return self.failures[index]

    @classmethod
    def for_settled_state(cls, machine, *, successful_only=False, observation=None):
        """State-only relation whose drives explicitly require a fixed-point cut.

        Unlike the whole-request prefix view, this cannot observe source hits.
        Its entry predicate is part of every encoded drive, not an assumption
        obtained from a baseline or from quiet scheduler flags alone. A caller
        proving a global property must establish and preserve that predicate;
        ``certifies_fixed_points`` can discharge those obligations for a
        validated direct interval plan with exclusive region writers.
        """
        result = cls(machine)
        result._settled_state = True
        # Optional local lemmas observe only continuable boundaries. Disjoint
        # actions commute there; a closed fallible DAG covers every original
        # successful final state, but can also complete when another ordering
        # fails. This is not a failure/event certificate: callers must never
        # use its failed payload, error identity or event image as evidence.
        # Ordinary state/event/history construction keeps the stricter plan.
        result._successful_only = successful_only
        if observation is not None:
            # Reuse the source owner's already-closed history projection, not
            # a new list of supposedly dead source variables. Both physical
            # drives and internal actions must own this same state boundary.
            # Full-state/native observation clients retain every coordinate.
            if (observation.source.procedure is not machine.procedure
                    or observation.drive.procedure is not machine.procedure
                    or observation.source.state_names != observation.drive.state_names):
                raise ValueError('settling projection requires a shared source/drive boundary')
            result._state_projection = observation.source, observation.drive
        return result

    def boundary_condition(self, state, *, check=None):
        """Pure-region fixed points; all register data stays arbitrary."""
        import z3
        from ..symbolic.bitvectors import widen
        from ..symbolic.functional_array import FunctionalArray
        from ..symbolic.step_template import StepTemplate

        if not self._settled_state:
            raise ValueError('only the settled-state relation restricts free cuts')
        context = next(value.ctx for value in state.values() if z3.is_expr(value))
        state = {name: _compact_boundary_arrays(value) for name, value in state.items()}

        def equal(before, after):
            if isinstance(before, FunctionalArray) and isinstance(after, FunctionalArray):
                # Reuse sparse store-chain equality; do not enumerate an entire
                # array for every process assigning one constant cell.
                return before.equal(after, context=context)
            if isinstance(before, list):
                return z3.And(z3.BoolVal(True, ctx=context),
                    *(equal(a, b) for a, b in zip(before, after, strict=True)))
            before, after = widen(before, after)
            return before == after

        roots = []
        for index in sorted(self._regions()):
            if check is not None:
                check()
            # The source owner may supply a dependency-closed projection.
            # A removed output has no boundary coordinate or predicate; do
            # not recreate the entire region just to constrain that output.
            names = tuple(sorted(self.effects[index][1] & state.keys()))
            if not names:
                continue
            # Compile only regions contributing to this cut. Cache by region,
            # not by the first cut's coordinates: a later, larger query must
            # still acquire every additional fixed-point predicate.
            template = self._boundary_templates.get(index)
            if template is None:
                process = self.machine.program.processes[index]
                environment = {**process.procedure.state_ranges, **self.machine.input_ranges,
                    **{name: kind.domain for name, kind in self.machine.program.kinds.items()}}
                template = StepTemplate(process.procedure, environment).normalized()
                self._boundary_templates[index] = template
            if template.context != context:
                template = template.translate(context)
                self._boundary_templates[index] = template
            encoded = template.instantiate(state, check=check)
            roots.append(encoded.ok)
            for name in names:
                roots.append(equal(state[name], encoded.state_post[name]))
        return z3.And(*roots) if roots else z3.BoolVal(True, ctx=context)

    def certifies_fixed_points(self):
        """A completed direct interval preserves its pure-region fixed points.

        Startup must execute every region; a later drive must start at the
        encoded fixed-point boundary. ``interval_plan`` checks the structural
        premises and ``encode_steps`` retains the drive-entry predicate,
        and its certified plan drains every changed dependency in DAG order.
        This additionally requires exclusive bit ownership across ALL source
        processes: a register/NBA writer outside a pure region could otherwise
        change its output without waking that region. Environment drives count
        as external writers too. Failure prefixes make no exit-state claim.

        This certificate is only about the compiler's own pure-region
        predicate, not another proposed invariant, source events, or a fallback
        scheduler relation. Build the ownership index in linear work.
        """
        if type(self) is not DirectSourceCompiler or not self._settled_state or not self.closes_plan:
            return False
        if self._fixed_point_certificate is None:
            written, shared = {}, {}
            for process in self.machine.program.processes:
                masks = {}
                for name, now, later in process.write_masks:
                    masks[name] = masks.get(name, 0) | now | later
                for name, mask in masks.items():
                    shared[name] = shared.get(name, 0) | (written.get(name, 0) & mask)
                    written[name] = written.get(name, 0) | mask
            driven = {name for name, _ in self.machine.drive_inputs}
            self._fixed_point_certificate = all(
                name not in driven and not mask & shared.get(name, 0)
                for index in self._regions()
                for name, mask in self._settling_writes[index].items())
        return self._fixed_point_certificate

    def _settling_reads(self, index):
        """Total value assignments and the reads controlling selected events.

        A conditional mux is not itself a value race. Event-observing views
        additionally need stable mark guards. The state-only view cannot expose
        marks, so their guards must not block a unique value fixed point.
        Disjoint constant packed writes use their compiler-owned bit masks.
        State-only closed DAGs may have several writes to a scalar. A query
        may also use their final values when it observes no mark in those
        activations and the complete notification graph is acyclic.
        Latches and unproved array traversals keep the ordinary path. A successful-
        boundary-only view may include fallible DAG nodes: their source checks
        remain executable and all guard inputs must be in the settling DAG.
        This does not preserve failed prefixes and is never an event view.
        """
        if index not in self._settling_observations:
            from .source_values import ArrayType

            program = self.machine.program
            process = program.processes[index]
            storage = set(program.fields)
            all_written = self.effects[index][1]
            written = all_written if self._state_projection is None else (
                all_written & set(self._state_projection[0].state_names))
            observed = observation_dependencies(process.activation, storage,
                marks=self._observed_marks,
                infallible=self._successful_only or not self._may_fail(index))
            values = (total_write_inputs(process.activation, storage, kinds=program.kinds)
                      if self._settled_state else single_write_inputs(process.activation, storage))
            values = {name: inputs for name, inputs in values.items() if name in written}
            if (not self._settled_state and observed == set()
                    and self._observed_marks is not None and written - values.keys()
                    and not any(isinstance(node, ir.Mark) and node.goal in self._observed_marks
                                for node in ir.walk(process.activation))):
                values = total_write_inputs(process.activation, storage)
                self._settling_unobserved.add(index)
            masks = {name: now for name, now, _later in process.write_masks if now}
            dependencies = single_write_dependencies(process.activation, program.kinds, masks)
            dependencies = {key: inputs for key, inputs in dependencies.items() if key[0] in written}
            masks = {name: mask for name, mask in masks.items() if name in written}
            certified = {}
            for name, mask in dependencies:
                certified[name] = certified.get(name, 0) | mask
            reads = {}
            complete = False
            exact_writes = certified == masks and certified.keys() == written
            if exact_writes:
                complete = True
                for inputs in dependencies.values():
                    for name, mask in inputs:
                        reads[name] = reads.get(name, 0) | mask
            elif {name for name, inputs in values.items() if inputs <= storage - all_written} == written:
                complete = True
                # Conditional total writes retain the existing whole-field
                # certificate. Do not infer partial writes through a mux.
                masks = {name: (1 << program.kinds[name].width) - 1 for name in written}
                syntax = read_masks(process.activation, program.kinds)
                reads = {name: syntax[name] for inputs in values.values() for name in inputs
                         if syntax.get(name)}
            else:
                observed = None
            if self._successful_only and self._may_fail(index):
                # Every successful settled execution has checked each node
                # against its final predecessors. A topological execution can
                # therefore cover its final values without enumerating failed
                # orders. Keep guard dependencies too: missing sensitivity or
                # cycles must still reject this certificate. Syntax may retain
                # extra self-reads; that only loses this optional fast path.
                for name, mask in read_masks(process.activation, program.kinds).items():
                    reads[name] = reads.get(name, 0) | mask
            if (self._settled_state and complete and not process.deferred
                    and observed is None and (self._successful_only or not self._may_fail(index))):
                # State-only cuts need the final value, not its source-event
                # image. Exact single-cell/bit writes have no transient
                # overwrites; their dependency masks suffice. Total loops and
                # repeated writes keep ALL notification edges, so an erased
                # intermediate value cannot hide an oscillating cycle.
                observed = set()
                if not exact_writes:
                    self._settling_unobserved.add(index)
            if written != all_written:
                # Projected final values do NOT erase transient notifications.
                # The original complete graph must still be a closed DAG.
                self._settling_unobserved.add(index)
            if process.deferred or observed is None:
                observed = None
            elif (any(isinstance(program.kinds[name], ArrayType) for name in written)
                    and not (self._settled_state and complete)):
                # Constant-cell writes already have exact dependency/ownership
                # masks. They may settle like packed writes in the state-only
                # DAG. Complete independent-cell traversals may also supply
                # final values, but retain all notification edges above.
                # Source-event views still require the ordinary scheduler.
                observed = None
            # A source-scoped temporary is still physical storage. After it
            # has been assigned, reading it is not a dependence on its entry
            # value and must not create a fictitious self-cycle. Incomplete
            # dataflow certificates retain conservative syntax-level reads.
            self._settling_values[index] = reads
            self._settling_writes[index] = masks
            if observed is not None and self._settled_state:
                observed = set()
            self._settling_observations[index] = observed
        return self._settling_observations[index]

    def _settling_order(self, roots):
        """A closed, bit-disjoint writer DAG with stable observation guards.

        A phase-event caller must establish that EVERY root is pending. A
        whole-prefix caller instead establishes those marks at startup. Each
        dependent reader must wake on every change to its predecessor, so the
        final values are the DAG's unique fixed point. Edge-triggered readers,
        overlapping writes, changing mark guards and open fanout retain the
        ordinary race check. No initial storage value is assumed here.
        """
        writers = {}
        for index in roots:
            if self._settling_reads(index) is None or not self.blocking[index] <= roots:
                return None
            for name, mask in self._settling_writes[index].items():
                if any(mask & previous for _, previous in writers.get(name, ())):
                    return None
                writers.setdefault(name, []).append((index, mask))
        predecessors = {index: set() for index in roots}
        if self._settling_unobserved & roots:
            # A repeated write can notify a reader even when its final value
            # is constant. Final-value dependencies alone cannot rule out an
            # oscillating notification cycle. Keep every blocking edge in the
            # same closed DAG, including edges erased by later overwrites.
            for index in roots:
                for child in self.blocking[index]:
                    predecessors[child].add(index)
        for index in roots:
            if self._settling_reads(index) & writers.keys():
                return None
            for name, read in self._settling_values[index].items():
                needed = [(writer, read & mask) for writer, mask in writers.get(name, ()) if read & mask]
                if not needed:
                    continue
                mask = 0
                for subscription in self.machine.subscriptions:
                    if subscription.process == index and subscription.storage == name:
                        if subscription.edge != 'change':
                            return None
                        mask |= subscription.mask
                if any(bits & ~mask for _, bits in needed):
                    return None
                predecessors[index].update(writer for writer, _ in needed)
        try:
            graph = {index: sorted(predecessors[index]) for index in sorted(roots)}
            return tuple(index + 1 for index in TopologicalSorter(graph).static_order())
        except CycleError:
            return None

    def _regions(self):
        """Closed pure combinational DAGs, initialized on every construction.

        Unlike a phase-event certificate, these regions permit redundant
        activations only after startup has established each guard's outcome.
        Guard changes wake their owner; intermediate DAG values cannot change
        guards. Repeating an unchanged outcome adds no prefix event.
        Every value read must be in the complete change sensitivity. This is
        essential even for inputs outside the region: otherwise an inactive
        process could legitimately retain an old value.
        """
        if self._prefix_regions is not None:
            return self._prefix_regions
        program = self.machine.program
        eligible = set()
        for index, process in enumerate(program.processes):
            if (not self.machine.startup[index] or process.initial_process is not None
                    or self._settling_reads(index) is None):
                continue
            read = dict(self._settling_values[index])
            for name in self._settling_reads(index):
                read[name] = (1 << program.kinds[name].width) - 1
            sensitivity = {}
            for subscription in self.machine.subscriptions:
                if subscription.process == index and subscription.edge == 'change':
                    sensitivity[subscription.storage] = sensitivity.get(subscription.storage, 0) | subscription.mask
            if all(not mask & ~sensitivity.get(name, 0) for name, mask in read.items()):
                eligible.add(index)
        # A glitch may not wake an edge-sensitive/conditional outsider. Remove
        # open fanout transitively, rather than considering only final values.
        while True:
            closed = {i for i in eligible if self.blocking[i] <= eligible}
            if closed == eligible:
                break
            eligible = closed
        neighbors = {i: set(self.blocking[i]) for i in eligible}
        for i in eligible:
            for child in self.blocking[i]:
                neighbors[child].add(i)
        # A packed word can have several disjoint writers without an event
        # edge between them. Certify them together, so the ordinary whole-
        # storage competitor check does not recreate a false write conflict.
        # _settling_order still rejects overlapping masks and real bit cycles.
        owners = {}
        for i in sorted(eligible):
            for name in self._settling_writes[i]:
                other = owners.setdefault(name, i)
                if other != i:
                    neighbors[i].add(other)
                    neighbors[other].add(i)
        regions = {}
        while eligible:
            component, pending = set(), [min(eligible)]
            while pending:
                i = pending.pop()
                if i not in component:
                    component.add(i)
                    pending.extend(neighbors[i] - component)
            eligible -= component
            order = self._settling_order(frozenset(component))
            if order is not None:
                for i in component:
                    regions[i] = tuple(action - 1 for action in order)
        self._prefix_regions = regions
        return regions

    @contextmanager
    def request_scope(self, initial, observation_scope):
        """Only a complete construction owns already-hit startup occurrences.

        This permission is scoped to one owned whole-request construction,
        never cached as a fact about an arbitrary boundary or obtained from a
        baseline. Pausing retains the scope; completion or closing releases it.
        Phase-event clients retain the original strict compiler.
        """
        import z3
        from .source_relation import SourceObservation

        if type(observation_scope) is not SourceObservation:
            raise TypeError('a direct request needs a typed observation scope')
        if self._prefix_scope:
            raise ValueError('direct whole-request construction is not reentrant')
        if observation_scope is SourceObservation.GOAL_PREFIX:
            for index in self._regions():
                pending = initial.state[self.machine.pending[index]]
                if not z3.is_true(z3.simplify(pending if z3.is_bool(pending) else pending != 0)):
                    raise Refused(Refusal('rtl-direct-prefix-startup-unbound',
                        'every pure-region occurrence must execute in this request startup'))
            self._prefix_scope = True
            # These are compiler-owned queue records, not DUT invariants.
            # Starting empty, enqueue makes valid iff its written mask is
            # nonzero, and a completed commit clears mask/change history.
            # No process interleaves within enqueue; failed/unfinished phases
            # cannot enter a later drive. Arbitrary cuts get no such fact.
            self._empty_queue_scope = all(name in initial.state and
                z3.is_true(z3.simplify(initial.state[name] == 0)) for name in self.empty_queue_masks)
        try:
            yield
        finally:
            self._prefix_scope = False
            self._empty_queue_scope = False

    def plan(self, roots, *, all_active=False, effects=None):
        """A sufficient static certificate; no solver or observed run is used."""
        if isinstance(effects, _DriveEffects):
            effects = effects.accesses
        roots = frozenset(roots)
        if not roots <= set(range(len(self.effects))):
            raise ValueError('direct roots must name compiler-owned processes')
        key = roots, all_active, self._prefix_scope, effects
        if key in self._plans:
            return self._plans[key]
        if all_active and (settled := self._settling_order(roots)) is not None:
            if len(settled) <= self.machine.event_bound:
                self._plans[key] = settled
                return settled
        pending, queued, actions = set(roots), set(), []
        commit_due = False
        machine = self.machine
        regions = self._regions() if self._prefix_scope or self._settled_state else {}
        while pending or commit_due:
            if len(actions) >= machine.event_bound:
                raise Refused(Refusal('rtl-direct-event-plan-unbounded',
                    'the structural active/NBA plan does not close within the source event bound'))
            if not pending:
                actions.append(machine.commit_action)
                pending, queued = queued, set()
                commit_due = False
                continue
            index = min(pending)
            bundle = {index}
            if index in regions:
                # Other roots in this same region must settle together. Include
                # their full forward cone, but not unchanged upstream writers.
                bundle = pending.intersection(regions[index])
                frontier = list(bundle)
                while frontier:
                    children = self.blocking[frontier.pop()] - bundle
                    bundle.update(children)
                    frontier.extend(children)
            # Another ready action may wake a dependent action BEFORE this
            # one runs. Checking only the initially pending pair misses that
            # transient ordering (j -> k -> i versus i -> j -> k).
            competitors = set(pending - bundle)
            frontier = list(competitors)
            while frontier:
                other = frontier.pop()
                for child in self.blocking[other] - competitors:
                    competitors.add(child)
                    frontier.append(child)
            for member in sorted(bundle):
                for other in sorted(competitors):
                    left = (*self.effects[member], self._may_fail(member)) if effects is None else effects[member]
                    right = (*self.effects[other], self._may_fail(other)) if effects is None else effects[other]
                    shared = conflicts(left[:2], right[:2])
                    if (other == member or shared
                            or ((left[2] or right[2]) and not self._successful_only)):
                        raise Refused(Refusal('rtl-direct-coactive-dependency',
                            'coactive source actions may change values, failure prefixes or goal occurrences',
                            processes=(member, other), storage=sorted(shared)))
            pending -= bundle
            if index in regions:
                actions.extend(i + 1 for i in regions[index] if i in bundle)
            else:
                actions.append(index + 1)
                pending.update(self.blocking[index])
            if machine.program.processes[index].deferred:
                # Commit even if no source process subscribes to its target.
                queued.update(self.deferred[index])
                commit_due = True
        result = tuple(actions)
        if len(result) > machine.event_bound:
            raise Refused(Refusal('rtl-direct-event-plan-unbounded',
                'the structural region plan exceeds the complete source event bound'))
        self._plans[key] = result
        return result

    def roots(self, state, pins, previous_pins=None):
        """Overapproximate activations using only constants and pin identity.

        ``previous_pins`` is the preceding drive in this same composition,
        never a sampled RTL state. Callers can omit it for an arbitrary entry.
        """
        import z3

        if pins is None:
            return frozenset(i for i, name in enumerate(self.machine.pending)
                             if not z3.is_false(z3.simplify(state[name] if z3.is_bool(state[name])
                                                           else state[name] != 0)))
        return frozenset(subscription.process for subscription in
                         self._driven_events(state, pins, previous_pins))

    def _driven_events(self, state, pins, previous_pins):
        """Possible source events of this drive, not a sampled execution."""
        import z3

        before = state if previous_pins is None else previous_pins
        events, drives, possible = [], {}, {}
        for subscription in self.machine.subscriptions:
            name = subscription.storage
            if name not in pins:
                continue
            old, new = before[name], pins[name]
            if new is None or old is None:
                events.append(subscription)
                continue
            # Many processes subscribe to the same bus. Decode its fixed
            # values (or widen its symbolic terms) once per drive, not once
            # per subscriber. Literal bit-vector edges need no SMT AST.
            if name not in drives:
                if z3.is_bv_value(old) and z3.is_bv_value(new):
                    drives[name] = old.as_long(), new.as_long()
                elif old.eq(new):
                    drives[name] = 0, 0  # A held term cannot change.
                else:
                    width = max(old.size(), new.size())
                    drives[name] = (z3.ZeroExt(width-old.size(), old) if old.size() < width else old,
                                    z3.ZeroExt(width-new.size(), new) if new.size() < width else new)
            key = name, subscription.edge, subscription.mask
            if key not in possible:
                old, new = drives[name]
                changed = old ^ new
                event = (changed & new if subscription.edge == 'rise' else
                         changed & old if subscription.edge == 'fall' else changed)
                possible[key] = (bool(event & subscription.mask) if type(event) is int else
                    not z3.is_false(z3.simplify(event & subscription.mask != 0)))
            if possible[key]:
                events.append(subscription)
        return tuple(events)

    def _drive_effects(self, pins, events):
        """Refine effects only when the unconditional plan cannot certify them.

        A successful static plan already covers every input value and failure
        prefix. Its existing cache is keyed by activation roots and scope; new
        datapath values do not require another contextual IR traversal. Edge
        implications can still simplify a guarded body without that traversal.
        """
        try:
            self.plan(frozenset(event.process for event in events))
        except Refused:
            accesses = self._phase_effects(pins, events)
        else:
            accesses = None
        literals = self._edge_literals(events)
        return _DriveEffects(accesses, literals) if any(literals) else accesses

    def _edge_literals(self, events):
        """Necessary input values, conditional on this root actually running.

        Only input-only subscribers qualify: no source action can retrigger
        them or change their pins. Alternative possible edges must agree.
        Startup and ordinary scheduler actions never use these drive facts.
        """
        program = self.machine.program
        written = {name for process in program.processes for name, now, later in process.write_masks
                   if now or later}
        inputs = {name for name, field in program.fields.items() if field.input} - written
        subscriptions = [[] for _ in program.processes]
        active = [[] for _ in program.processes]
        for subscription in self.machine.subscriptions:
            subscriptions[subscription.process].append(subscription)
        for subscription in events:
            active[subscription.process].append(subscription)
        result = []
        for index, choices in enumerate(active):
            facts = {}
            if choices and all(s.storage in inputs for s in subscriptions[index]):
                alternatives = []
                for subscription in choices:
                    name, edge = subscription.storage, subscription.edge
                    kind = program.kinds[name]
                    value = {}
                    if kind.width == 1 and subscription.mask == 1 and edge in {'rise', 'fall'}:
                        value[name] = (-1 if kind.signed else 1) if edge == 'rise' else 0
                    alternatives.append(value)
                facts = {name: value for name, value in alternatives[0].items()
                         if all(other.get(name) == value for other in alternatives)}
            result.append(tuple(sorted((name, value) for name, value in facts.items()
                                       if name in self.effects[index][0])))
        return tuple(result)

    def _simplify(self, term):
        """Simplify each pin term once; the entry retains the term so its id stays unique."""
        import z3

        key = term.ctx, term.get_id()
        if key not in self._simplified:
            if len(self._simplified) >= 4096:
                self._simplified.clear()
            self._simplified[key] = term, z3.simplify(term)
        return self._simplified[key][1]

    def _phase_effects(self, pins, events):
        """Use immutable drive facts and what a root's edge logically entails.

        No DUT state or reachability invariant is assumed. Input-only roots
        cannot be awakened again by a source write/NBA. If their only possible
        trigger is a one-bit rising edge, an executing body sees one (zero for
        falling); an alternative trigger removes that fact. These scheduling
        certificates do not replace the compiled relation's activation guards.
        """
        import z3

        program = self.machine.program
        written = {name for process in program.processes for name, now, later in process.write_masks
                   if now or later}
        inputs = {name for name, field in program.fields.items() if field.input} - written
        known = {}
        for name in inputs & pins.keys():
            value = pins[name]
            if value is not None and z3.is_bv_value(value := self._simplify(value)):
                kind = program.kinds[name]
                known[name] = value.as_signed_long() if kind.signed else value.as_long()
        subscriptions = {i: [] for i in range(len(program.processes))}
        for subscription in self.machine.subscriptions:
            subscriptions[subscription.process].append(subscription)
        edge_literals = self._edge_literals(events)
        result = []
        for index, process in enumerate(program.processes):
            facts = {**known, **dict(edge_literals[index])}
            # Facts irrelevant to this activation must not multiply the cache.
            key = index, tuple(sorted((name, value) for name, value in facts.items()
                                      if name in self.effects[index][0]))
            if not key[1]:
                result.append((*map(frozenset, self.effects[index]), self._may_fail(index)))
                continue
            if key not in self._context_effects:
                environment = {**process.procedure.state_ranges,
                               **{name: ranges.Range(value, value) for name, value in key[1]}}
                read, write, failure = conditional_accesses(process.activation, set(program.fields), environment)
                read |= frozenset(s.storage for s in subscriptions[index])
                self._context_effects[key] = read, write, failure
            result.append(self._context_effects[key])
        return tuple(result)

    def check_initial_layout(self, layout):
        """Private derived nets must be overwritten before any observation.

        Native-depositable variables and pins are already explicit request
        coordinates. This sufficient check covers the remaining derived nets;
        it never picks convenient initial values for them.
        """
        from .source_effects import input_constant_writes
        from .source_initial import source_storage_leaves

        program = self.machine.program
        leaves = {}
        for native, path, _width in source_storage_leaves(program):
            leaves.setdefault(path[0], set()).add(native)
        bound = {name for name, field in program.fields.items() if field.input}
        bound.update(name for name, names in leaves.items() if names <= layout.widths.keys())
        derived = set(program.fields) - bound
        # Propagate definitions in the SAME certified event order used for
        # startup execution. A derived combinational net can feed another net;
        # requiring each to read physical coordinates directly rejects ordinary
        # multi-level logic. Only initially pending roots are guaranteed to
        # execute: a conditional change notification cannot establish a value.
        roots = frozenset(i for i, active in enumerate(self.machine.startup) if active)
        plan = self.plan(roots, all_active=True)
        defined = set()
        for action in plan:
            index = action - 1
            if index in roots:
                process = program.processes[index]
                defined.update(input_constant_writes(
                    process.activation, set(program.fields), bound | defined))
        if derived - defined:
            raise Refused(Refusal('rtl-direct-private-initialization-unsupported',
                'private derived storage needs an unconditional startup definition from request coordinates',
                storage=sorted(derived - defined)))

    def relation(self, owner, roots, *, driving, all_active=False, effects=None):
        """Select from a compiled interval; goal batches do not recompile it."""
        key = (frozenset(roots), driving, all_active, self._prefix_scope, self._empty_queue_scope,
               owner.goal_names, owner.occurrence_names,
               owner.source.state_names, owner.source.context, effects)
        if key in self._relations:
            return self._relations[key]
        direct, template = self._template(roots, driving=driving, all_active=all_active, effects=effects)
        # The owner closed this state set over ALL source/drive actions. Keep
        # that common boundary through each macro phase, not all RTL storage
        # and not a phase-local cone that loses a later phase's predecessor.
        reads = owner.source.state_names
        if self._phase_projection is not None:
            source, reads, phases = self._phase_projection
            if (owner.source is not source or not driving or all_active
                    or (frozenset(roots), effects) not in phases):
                raise ValueError('a projected phase must belong to its closed request plan')
        relation = template.relation(direct, owner.goal_names, complete_state=False,
            state_reads=reads,
            occurrences=bool(owner.occurrence_names), context=owner.source.context)
        self._relations[key] = relation
        return relation

    def _template(self, roots, *, driving, all_active=False, effects=None):
        """Compile once per structural phase, independently of queried goals."""
        key = self._template_key(roots, driving=driving, all_active=all_active, effects=effects)
        if key not in self._templates:
            _, clear, actions = key
            commands = [ir.Assign('accepted', ir.Lit(True)), *self._clear_commands(clear)]
            if driving:
                commands.append(self.machine.actions[0][1])
            commands.extend(self._action_command(*action) for action in actions)
            self._templates[key] = self._compile_template(commands)
        return self._templates[key]

    def _clear_commands(self, names):
        masks = set(self.empty_queue_masks)
        return [ir.Assign(name, ir.Lit(0 if name in masks else False)) for name in names]

    def _template_key(self, roots, *, driving, all_active=False, effects=None):
        machine = self.machine
        # Recheck this activation's certificate before consulting compiled
        # code. Different read/write facts may justify the SAME action plan;
        # they must not force normalization/encoding of that body again, nor
        # may a previous certificate authorize an incompatible activation.
        plan = self.plan(roots, all_active=all_active, effects=effects)
        # A drive begins at a quiet boundary; startup has only the pending
        # roots selected from its actual entry. Expose these compiler-owned
        # facts locally instead of propagating scheduler bookkeeping through
        # the entire request. encode_steps retains the entry obligation.
        clear = set(self.quiet_flags) - (set(machine.pending[i] for i in roots) if not driving else set())
        if self._empty_queue_scope:
            clear.update(self.empty_queue_masks)
        regions = self._regions() if self._prefix_scope or self._settled_state else {}
        literals = effects.literals if driving and isinstance(effects, _DriveEffects) else ()
        actions = tuple((action, action - 1 in regions,
                         literals[action - 1] if 0 < action <= len(literals) else ())
                        for action in plan)
        return driving, tuple(sorted(clear)), actions

    def _action_command(self, action, unconditional, literals=()):
        enabled, body = self.machine.actions[action]
        if literals:
            from .source_constants import substitute_literals

            body = substitute_literals(body, {name: ir.Lit(value) for name, value in literals})
        if action == self.machine.commit_action:
            # Every queue body is already guarded by its own valid flag.
            # An empty commit is therefore identity. The global any-valid
            # guard couples otherwise independent queues (including wide
            # packed write masks) into every successor for no semantic gain.
            # Retain active-region ordering; only this private macro may skip
            # an empty commit, not the public scheduler admission predicate.
            enabled = ir.Op('not', ir._balanced_op('lor',
                (ir.Var(name) for name in self.machine.pending), ir.Lit(False)))
        return ir.If(ir.Lit(True) if unconditional else enabled, body, ir.Skip())

    def _compile_phases(self, phases, *, owner=None):
        """Encode shared action bodies once for this certified phase family.

        Each original ordered plan embeds in a common supersequence. A private
        finite selector disables precisely the other plans' command copies;
        specializing it restores the original IR, including failed prefixes.
        This is compilation sharing, not a choice of schedule by the solver.
        Certificates are still checked per activation before using a view.
        """
        from bisect import bisect_left
        from ..symbolic.validation import EncodingError

        keys = {phase: self._template_key(phase[0], driving=True, effects=phase[1])
                for phase in phases}
        for key in dict.fromkeys(keys.values()):
            if key not in self._templates:
                continue
            try:
                self._prepare_phase_template(self._templates[key][1], owner)
            except EncodingError:
                # A later consumer can demand a broader observation image.
                # Reuse must not turn a failure of shared encoding into a new
                # capability refusal; retain the original separate fallback.
                del self._templates[key]
        missing = tuple(dict.fromkeys(key for key in keys.values() if key not in self._templates))
        if len(missing) > 1:
            # Append-only subsequence embedding: O(L log L) placement for L
            # action occurrences, without a quadratic sequence-alignment table.
            positions, sequence = {}, []
            for variant in sorted(range(len(missing)), key=lambda i: -len(missing[i][2])):
                cursor = 0
                for action in missing[variant][2]:
                    available = positions.setdefault(action, [])
                    offset = bisect_left(available, cursor)
                    if offset == len(available):
                        available.append(len(sequence))
                        sequence.append((action, []))
                    position = available[offset]
                    sequence[position][1].append(variant)
                    cursor = position + 1
            original = self.machine.procedure
            selector = '_direct_phase'
            while selector in original.types or selector in (*original.state, *original.inputs, *original.locals):
                selector += '_'
            clear = sorted({name for _driving, names, _actions in missing for name in names})
            commands = [ir.Assign('accepted', ir.Lit(True)), *self._clear_commands(clear),
                        self.machine.actions[0][1]]
            for action, variants in sequence:
                command = self._action_command(*action)
                if len(variants) != len(missing):
                    choices = [ir.Op('eq', ir.Var(selector), ir.Lit(i)) for i in variants]
                    guard = ir._balanced_op('lor', choices, ir.Lit(False))
                    command = ir.If(guard, command, ir.Skip())
                commands.append(command)
            try:
                direct, template = self._compile_template(commands,
                    selector=(selector, len(missing)), normalize=False)
                # Share the demanded cone, not an eagerly encoded full family.
                # The conservative boundary still closes across every phase
                # below; no source failure or cross-phase predecessor is lost.
                # Keep preparation inside the original fallback boundary.
                self._prepare_phase_template(template, owner)
            except EncodingError:
                # Sharing can need a wider carrier/loop envelope than each
                # separate phase. It must not shrink the supported domain.
                # The original encoders below still surface genuine errors;
                # cancellation and preparation deadlines are not caught.
                pass
            else:
                for variant, key in enumerate(missing):
                    self._templates[key] = (direct,
                        template.specialize_inputs({selector: variant}).normalized())
        for phase, key in keys.items():
            if key not in self._templates:
                self._template(phase[0], driving=True, effects=phase[1])
            phases[phase] = self._templates[key]

    def _prepare_phase_template(self, template, owner):
        if owner is None:
            return template.source
        marks = {mark for mark, _, goal in self.machine.occurrences if goal in owner.goal_names}
        marks.update(mark for mark, _cause in self.machine.domain_checks)
        return template.observations(marks, outputs=('accepted', 'quiescent'),
                                     state_reads=owner.source.state_names)

    def _compile_template(self, commands, *, selector=None, normalize=True):
        from .source_relation import SourceIntervalTemplate

        machine = self.machine
        # plan() drains the conservative pending set and every queued NBA
        # notification. On normal completion these assignments are identities;
        # a Fail stops before them, preserving its original event prefix.
        if self.closes_plan:
            commands.extend(ir.Assign(name, ir.Lit(False)) for name in self.quiet_flags)
        # Processes absent from this structural phase cannot execute. Keep
        # their declared marks as guarded-false events in the SAME catalogue.
        present = {node.goal for command in commands for node in ir.walk(command)
                   if isinstance(node, ir.Mark)}
        missing = {mark for mark, _, _ in machine.occurrences} | {
            mark for mark, _ in machine.domain_checks}
        commands.extend(ir.If(ir.Lit(False), ir.Mark(mark), ir.Skip())
                        for mark in sorted(missing - present))
        original = machine.procedure
        # Every phase shares the scheduler's error alphabet. Otherwise dropping
        # inactive processes changes both error-tag widths AND numeric meanings
        # between startup and later drives. Keep unused original failures under
        # false control, just like the inactive occurrence declarations above.
        errors = {node.code for command in commands for node in ir.walk(command)
                  if isinstance(node, ir.Fail)}
        for node in ir.walk(original.body):
            if isinstance(node, ir.Fail) and node.code not in errors:
                commands.append(ir.If(ir.Lit(False), node, ir.Skip()))
                errors.add(node.code)
        for name, expression in (('accepted', ir.Var('accepted')), ('quiescent', machine.quiet)):
            commands.append(ir.Assign(original.output_vars[name],
                ir.Op('arraylit', ir.Op('toint', expression))))
        inputs, kinds, signature = original.inputs, original.types, original.signature
        environment = {**original.state_ranges, **machine.input_ranges}
        if selector is not None:
            from ..symbolic.procedure_contract import ProcedureSignature
            from ..symbolic.typecheck import INTEGER
            from ..core.contract import FiniteType

            name, count = selector
            domain = FiniteType(max(1, (count - 1).bit_length()))
            inputs = (*inputs, name)
            kinds = {**kinds, name: INTEGER}
            signature = ProcedureSignature(
                (*((binding.name, binding) for binding in signature.input_bindings), (name, domain)),
                tuple((binding.name, binding) for binding in signature.output_bindings))
            environment[name] = ranges.Range(0, (1 << domain.width) - 1)
        procedure = ir.Procedure(original.state, inputs, original.locals, original.outputs,
            _sequence(commands), types=kinds, output_vars=original.output_vars,
            output_capacity=1, signature=signature)
        procedure.state_ranges = original.state_ranges
        direct = replace(machine, procedure=procedure)
        template = SourceIntervalTemplate(procedure, environment)
        if normalize:
            template = template.normalized()
        return direct, template

    def interval_plan(self, state, pins, *, event_bound, previous_pins=None):
        """Validate the exact structural interval without building its datapath.

        Encoding and compiler-owned boundary certificates use the same entry,
        activation, settling-bound and NBA checks. This certifies no source
        event, arbitrary invariant or failed-prefix observation.
        """
        import z3

        events = None if pins is None else self._driven_events(state, pins, previous_pins)
        roots = (self.roots(state, None) if events is None else
                 frozenset(subscription.process for subscription in events))
        effects = None if pins is None else self._drive_effects(pins, events)
        all_active = pins is None and all(z3.is_true(z3.simplify(
            state[self.machine.pending[i]] if z3.is_bool(state[self.machine.pending[i]])
            else state[self.machine.pending[i]] != 0)) for i in roots)
        if self._settled_state and pins is None and (not all_active or not set(self._regions()) <= roots):
            raise Refused(Refusal('rtl-direct-settled-startup-unbound',
                'a state-only startup must activate every pure combinational region'))
        required = len(self.plan(roots, all_active=all_active, effects=effects)) + int(pins is not None)
        if type(event_bound) is not int or event_bound < max(1, required):
            raise Refused(Refusal('rtl-direct-interval-bound-insufficient',
                'the direct transition must contain the complete declared settling interval',
                required=max(1, required), supplied=event_bound))
        # Initial NBA work is not an activation root. The direct constructor
        # currently owns startup (empty NBA queues), not arbitrary mid-event
        # resumption. Do not silently clear a queued write supplied by a caller.
        if pins is None and any(not z3.is_false(z3.simplify(
                state[write.valid] if z3.is_bool(state[write.valid]) else state[write.valid] != 0))
                for write, _, _ in self.machine.queue_edges):
            raise Refused(Refusal('rtl-direct-initial-nba-unsupported',
                'direct startup requires empty NBA queues; a pending commit needs the event scheduler'))
        return roots, all_active, effects

    def encode_steps(self, owner, state, pins, *, previous_pins=None, **kwargs):
        import z3

        if self._settled_state and (owner.goal_names or owner.occurrence_names) \
                and self._phase_projection is None:
            raise ValueError('a settled-state cut cannot certify source-event observations')
        if self._state_projection is not None:
            source, drive = self._state_projection
            if owner.source is not source or owner.drive is not drive:
                raise ValueError('settling projection belongs to a different observation')
            if self._phase_projection is None:
                state = {name: state[name] for name in source.state_names}
        if self._phase_projection is not None:
            # Only the closed compiler plan below can supply this boundary.
            # Extra startup fields must not recreate discarded fixed points.
            state = {name: state[name] for name in self._phase_projection[1]}
        roots, all_active, effects = self.interval_plan(state, pins,
            event_bound=kwargs['event_bound'], previous_pins=previous_pins)
        encoded = yield from self._encode_plan_steps(owner, state, pins, roots=roots,
            all_active=all_active, effects=effects, **kwargs)
        if pins is not None:
            quiet = z3.And(*(z3.Not(state[name]) if z3.is_bool(state[name]) else state[name] == 0
                             for name in self.quiet_flags))
            if self._settled_state:
                quiet = z3.And(quiet, self.boundary_condition(state))
            # Keep the original legal-drive domain even if the body could
            # fail: an illegal drive must never bypass this obligation.
            # This is the same macro-transition's admission condition. Keep
            # one frame per action so the existing exact member constructor
            # can complete its definitions; a second fake frame disabled that
            # path and forced a full-history solver fallback.
            encoded = replace(encoded, constraints=(z3.And(encoded.constraints[0],
                              z3.Implies(encoded.entered, quiet)), *encoded.constraints[1:]))
        # A complete structural plan cannot end alive with pending work. This
        # is compiler construction, not an assumed/proved history invariant.
        # After failure/disabled entry quiescence is unobserved by continuable.
        return (replace(encoded, quiescent=z3.BoolVal(True, ctx=encoded.alive.ctx))
                if self.closes_plan else encoded)

    def _encode_plan_steps(self, owner, state, pins, *, roots, all_active, effects, **kwargs):
        relation = self.relation(owner, roots, driving=pins is not None, all_active=all_active, effects=effects)
        missing = set(relation.source.state_names) - set(state)
        if missing:
            # The compiled plan closes its source and drive views jointly. A
            # caller's boundary, such as a per-view control projection, can
            # omit retained state that this closure reads. That is a missing
            # certificate, not a malformed request: callers keep the cover.
            raise Refused(Refusal('rtl-direct-state-boundary-open',
                'the compiled interval reads retained state outside the supplied boundary',
                missing=tuple(sorted(missing))))
        # Internal active/NBA events are compiled, not truncated to one event.
        # The interval still exports every occurrence and its quiet/fault bits.
        kwargs['event_bound'] = 1
        return (yield from relation.encode_steps(state, pins, **kwargs))


class NativeMemberSourceCompiler(DirectSourceCompiler):
    """A legal-order proposal for native membership, NEVER a query cover.

    Conditional source actions retain their original guards and notifications.
    Unlike a certified direct plan, this view neither clears pending work at
    exit nor asserts quiescence. A caller must check the full native image and
    every fault/unfinished predicate; a failed proposal says nothing about the
    other orders. Only the native-history binding may own this compiler.
    """

    closes_plan = False

    def __init__(self, machine, *, actions=None):
        super().__init__(machine)
        if actions is not None and (type(actions) is not tuple or
                any(type(action) is not int or not 0 < action < len(machine.actions) for action in actions)):
            raise ValueError('a native order proposal names only compiler-owned internal actions')
        self._proposed_actions = actions
        self._action_views = {}
        from ..symbolic import typecheck as types

        # Compact scalar plans benefit from one cached interval image. Memory
        # and retained loops can make recompiling that concatenated body far
        # more expensive than composing already encoded actions. This choice
        # depends only on syntax/types, never a task name or a query answer.
        self._reuse_actions = (actions is not None or any(isinstance(machine.procedure.types.get(name), types.Array)
            for name in machine.procedure.state)
            or any(isinstance(node, ir.For) for node in ir.walk(machine.procedure.body)))

    def _phase_effects(self, pins, events):
        # plan() proposes a guarded order; it does not use per-input
        # commutativity certificates. Computing those facts would only repeat
        # access analysis and split identical relation-cache entries. Keep
        # every source guard and the final native-history checks unchanged;
        # the universal compiler still computes and checks its certificates.
        return None

    def _encode_plan_steps(self, owner, state, pins, *, roots, all_active, effects, **kwargs):
        method = (self._encode_action_views_steps if self._reuse_actions else
                  super()._encode_plan_steps)
        return (yield from method(owner, state, pins, roots=roots,
            all_active=all_active, effects=effects, **kwargs))

    def _encode_action_views_steps(self, owner, state, pins, *, roots, all_active, effects, **kwargs):
        """Compose the existing scheduler kernel, not another expanded IR body.

        One constant-action view is shared by all phases of this observation
        owner. Inactive proposed actions stutter, just like the guarded macro;
        a physical drive still requires a quiet entry. This is only a native
        membership proposal and cannot classify an unseen goal as unreachable.
        """
        from behave.isolation.preparation_control import check_preparation

        plan = ((0,) if pins is not None else ()) + self.plan(roots, all_active=all_active, effects=effects)
        if not plan:
            return (yield from super()._encode_plan_steps(owner, state, pins, roots=roots,
                all_active=all_active, effects=effects, **kwargs))
        actions = []
        for action in plan:
            check_preparation()
            key = owner.source, action
            if key not in self._action_views:
                view = owner.source.specialize_inputs({'action': action}).normalized().successor_deltas()
                check_preparation()
                self._action_views[key] = view
            actions.append((action, self._action_views[key]))
        kwargs['event_bound'] = len(actions)
        return (yield from replace(owner, direct=None)._encode_schedule_steps(
            state, pins, proposed_actions=tuple(actions), **kwargs))

    @contextmanager
    def request_scope(self, initial, observation_scope):
        from .source_relation import SourceObservation

        if type(observation_scope) is not SourceObservation:
            raise TypeError('a member proposal needs a typed observation scope')
        # Do not acquire the universal compiler's pure-region permissions.
        try:
            yield
        finally:
            # Retained compiler syntax is shared across records. Literal
            # input-dependent access summaries belong to this record only.
            self._context_effects.clear()

    def plan(self, roots, *, all_active=False, effects=None):
        roots = frozenset(roots)
        if not roots <= set(range(len(self.effects))):
            raise ValueError('member roots must name compiler-owned processes')
        if self._proposed_actions is not None:
            return self._proposed_actions
        if roots in self._plans:
            return self._plans[roots]
        # Prefer producers before consumers, without assuming that order is
        # equivalent to any other. Cycles simply use the original stable order.
        graph = {i: set() for i in range(len(self.effects))}
        for i, children in enumerate(self.blocking):
            for child in children:
                graph[child].add(i)
        try:
            order = tuple(TopologicalSorter(graph).static_order())
        except CycleError:
            order = tuple(graph)
        rank = {process: index for index, process in enumerate(order)}
        pending, queued, actions, commit_due = set(roots), set(), [], False
        while pending or commit_due:
            if len(actions) >= self.machine.event_bound:
                raise Refused(Refusal('rtl-native-member-plan-unbounded',
                    'the proposed order does not close within the source event bound'))
            if not pending:
                actions.append(self.machine.commit_action)
                pending, queued, commit_due = queued, set(), False
                continue
            index = min(pending, key=rank.__getitem__)
            pending.remove(index)
            actions.append(index + 1)
            pending.update(self.blocking[index])
            if self.machine.program.processes[index].deferred:
                queued.update(self.deferred[index])
                commit_due = True
        self._plans[roots] = tuple(actions)
        return self._plans[roots]


class NativeCoverSourceCompiler(DirectSourceCompiler):
    """Direct intervals where certified, otherwise retain every source order.

    This is a cover of native executions, not an event-order equivalence claim.
    Private derived initial bits remain existential. UNSAT of a goal plus all
    unencoded exits is sound over this larger domain; SAT is only a proposal
    until native execution confirms the goal. No schedule is chosen by fiat.
    """

    covers_native_orders = True

    def __init__(self, machine):
        super().__init__(machine)
        self._state_view = None
        self._state_owner = None

    def project_phases(self, owner, state, phases, *, state_reads, previous_pins=None, check=None):
        from ..core.construction_checkpoint import finish_construction

        return finish_construction(self.project_phases_steps(owner, state, phases,
            state_reads=state_reads, previous_pins=previous_pins), check)

    def project_phases_steps(self, owner, state, phases, *, state_reads, previous_pins=None):
        """Close the common boundary over compiled drives BEFORE unrolling.

        Generic scheduler successors can depend on storage only to decide
        which action to execute. A certified macro has already discharged that
        bookkeeping. Re-close its actual successor dependencies across EVERY
        physical phase, including reads needed by another phase. Startup stays
        on the original relation. An uncertified phase leaves the whole batch
        unchanged, never a truncated or partly projected request.
        """
        from copy import copy

        from ..symbolic.step_template import template_state_dependencies

        if not self._prefix_scope:
            return None
        names = (*owner.goal_names, *owner.occurrence_names)
        inactive = owner.source.literal_false_hits(names) == set(names)
        compiler = (DirectSourceCompiler.for_settled_state(self.machine, observation=owner)
                    if inactive else self)
        plans = {}
        previous = previous_pins
        for pins, bound in phases:
            yield
            if any(value is None for value in pins.values()):
                return None  # Fresh/held physical values need their own plan.
            events = compiler._driven_events(state, pins, previous)
            roots = frozenset(event.process for event in events)
            effects = compiler._drive_effects(pins, events)
            plan = compiler.plan(roots, effects=effects)
            if bound < max(1, len(plan) + 1):
                return None
            key = roots, effects
            if key not in plans:
                plans[key] = None
            previous = pins
        if not plans:
            return None
        compiler._compile_phases(plans, owner=owner)
        # Quietness is an entry obligation; physical pins are needed to decide
        # later activations. Neither is optional merely because the DUT's
        # output payload is absent from this particular goal batch.
        reads = set(state_reads) | set(dict(self.machine.drive_inputs)) | set(compiler.quiet_flags)
        # Prepare each phase once with the original conservative boundary.
        # A later phase may need a predecessor unobserved by this phase alone;
        # close that shared cone before projecting any of its state away.
        views = []
        for machine, template in plans.values():
            yield
            relation = template.relation(machine, owner.goal_names,
                occurrences=bool(owner.occurrence_names), state_reads=owner.source.state_names)
            views.extend((relation.source, relation.drive))
        reads = template_state_dependencies(views, outputs=('accepted', 'quiescent'),
                                            state_reads=reads)
        if not reads < set(owner.source.state_names):
            return None
        # Private compiler state: no other goal batch or native observation
        # view acquires this projection. Template/plan caches contain syntax
        # only; each query still encodes every original physical phase.
        projected = copy(compiler)
        projected._relations = {}
        projected._phase_projection = owner.source, frozenset(reads), frozenset(plans)
        return replace(owner, direct=projected)

    def encode_steps(self, owner, state, pins, **kwargs):
        if self._prefix_scope:
            if self._state_owner is None or self._state_owner[0] is not owner:
                names = (*owner.goal_names, *owner.occurrence_names)
                inactive = owner.source.literal_false_hits(names)
                # This is an identity of the unrestricted one-step template,
                # not a sampled hit set or a conjectured reachable-state range.
                value_owner = (replace(owner, goal_names=(), occurrence_names=())
                               if inactive == set(names) else None)
                self._state_owner = owner, value_owner
                self._state_view = None
            value_owner = self._state_owner[1]
        else:
            value_owner = None
        if value_owner is not None:
            # A whole-request value/control query cannot observe transient
            # source events. Reuse the state-only compiler's checked startup
            # and fixed-point cuts instead of expanding those unused orders.
            # Arbitrary interval clients and event queries retain the cover.
            if self._state_view is None:
                self._state_view = DirectSourceCompiler.for_settled_state(
                    self.machine, observation=value_owner)
            try:
                encoded = yield from self._state_view.encode_steps(value_owner, state, pins, **kwargs)
                import z3

                false = z3.BoolVal(False, ctx=encoded.alive.ctx)
                return replace(encoded, hits={name: false for name in owner.goal_names},
                               occurrences={name: false for name in owner.occurrence_names})
            except Refused:
                # Lack of a state-only certificate is not a domain rejection.
                # Fall back without changing request constraints or goals.
                pass
        try:
            return (yield from super().encode_steps(owner, state, pins, **kwargs))
        except Refused as exc:
            if exc.refusal.kind not in {
                    'rtl-direct-coactive-dependency', 'rtl-direct-event-plan-unbounded',
                    'rtl-direct-state-boundary-open'}:
                raise
        # Prove the quiet suffix BEFORE allocating the fallback scheduler.
        # Keep every event order, NBA commit, failure prefix and occurrence;
        # only an already-quiescent suffix can disappear. Drives
        # already require an empty scheduler; startup must also have no queued
        # NBA work outside those counted activations. Insufficient bounds and
        # arbitrary queued boundaries retain their original unfinished bit.
        import z3
        from .source_scheduler import _event_bound

        empty_nba = pins is not None or all(z3.is_false(z3.simplify(
            state[write.valid] if z3.is_bool(state[write.valid]) else state[write.valid] != 0))
            for write, _, _ in self.machine.queue_edges)
        required = _event_bound(self.machine.predecessors,
            tuple(bool(process.deferred) for process in self.machine.program.processes),
            self.roots(state, pins, kwargs.get('previous_pins')), self.machine.notification_caps)
        complete = empty_nba and required is not None and kwargs['event_bound'] >= required
        if complete:
            kwargs = {**kwargs, 'event_bound': required}
        encoded = yield from replace(owner, direct=None).encode_steps(state, pins, **kwargs)
        if complete:
            # Only entered, surviving intervals consume quiescence. All
            # original action constraints, fault predicates and prefix hits
            # remain; neither success nor a particular order is assumed.
            encoded = replace(encoded, quiescent=z3.BoolVal(True, ctx=encoded.alive.ctx))
        return encoded
