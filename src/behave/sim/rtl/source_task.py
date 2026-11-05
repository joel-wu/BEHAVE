"""Source events, registered protocol queues and external memory over a history.

The caller binds the initial state or recorded prefix and environment schedule.
Artifact handshakes, queue advancement and future memory responses follow the
execution. Startup has its own event image, never credited to a later slot.
This relation alone does not establish native scheduler correspondence or grant
an exact-analysis capability. Native replay remains witness authority.
"""

from copy import copy
from dataclasses import dataclass, field, replace
from functools import partial

import z3

from ..symbolic.encoder import validated_width
from ..core.construction_checkpoint import finish_construction
from ..core.contract import Kind
from ..core.monitor import INPUT_COMPLETION
from ..core.observation_symbolic import condition_terms, logical_payload, logical_scalar, logical_zero, predicate_term
from ..core.physical_value import packed_width
from ..core.stimulus import EventSlot
from .protocol_relation import ProtocolIntervalRelation
from .source_memory import SourceMemoryFrame, SourceMemoryRelation, SourceMemorySlot
from .source_relation import SourceObservation, SourcePhaseEncoding, _union_images
from .symbolic_state import physical_bits


@dataclass(frozen=True, slots=True)
class SourceTaskFrame:
    """One completed slot, including its protocol and observation obligations.

    Roots are deltas, not repeated copies of earlier frames; observations
    preserve the selected event/prefix scope. The first frame includes startup
    and protocol initialization. Request coordinates/initial bindings and final
    completion remain the request
    owner's obligations; a frame alone cannot classify the full domain.
    """

    index: int
    source: SourcePhaseEncoding
    constraints: tuple
    hits: dict
    domain_faults: dict
    environment_assumptions: dict
    unfinished: object
    memory_conflicts: tuple


@dataclass(frozen=True, slots=True)
class SourceTaskEncoding:
    source: object
    protocol: object
    constraints: tuple
    startup: object = None
    monitor_states: tuple = ()
    monitor_hits: tuple = ()
    transaction_states: tuple = ()
    transaction_hits: tuple = ()
    terminal_hits: dict | None = None
    terminal_assumptions: dict | None = None
    terminal_unresolved: dict | None = None
    monitor_faults: tuple = ()
    monitor_assumptions: tuple = ()
    monitor_constraints: tuple = ()
    monitor_frame_sizes: tuple = ()
    member_plan: object = None
    sample_frames: tuple = ()
    _monitor_frames_cache: object = field(default=None, init=False, repr=False, compare=False)

    def _monitor_frames(self, *, check):
        from .monitor_template import _MonitorFrames

        check()
        if self._monitor_frames_cache is None:
            plan = _MonitorFrames(self.monitor_constraints, self.monitor_frame_sizes, check=check)
            check()  # Never publish partial/cancelled preparation.
            object.__setattr__(self, '_monitor_frames_cache', plan)
        return self._monitor_frames_cache

    @property
    def environment_assumptions(self):
        images = (*self.monitor_assumptions, self.terminal_assumptions or {})
        return _union_images(images)

    @property
    def state(self):
        return self.source.state

    @property
    def alive(self):
        return self.source.alive

    @property
    def error(self):
        return self.source.error

    @property
    def hits(self):
        images = (self.source.hits, *self.protocol.hits, *self.monitor_hits, *self.transaction_hits)
        if self.terminal_hits is not None:
            images = (*images, self.terminal_hits)
        return _union_images(images if self.startup is None else (self.startup.hits, *images))

    @property
    def occurrences(self):
        images = (self.source.occurrences,)
        return _union_images(images if self.startup is None else (self.startup.occurrences, *images))

    @property
    def domain_faults(self):
        images = (self.source.domain_faults, *self.monitor_faults)
        return _union_images(images if self.startup is None else (self.startup.domain_faults, *images))

    @property
    def unfinished(self):
        """An actual history prefix exhausted an event bound while still live."""
        return (self.source.unfinished if self.startup is None else
                z3.Or(self.startup.unfinished, self.source.unfinished))

    @property
    def continuable(self):
        return self.source.continuable


TERMINAL_ENVIRONMENT_FAULT = 'environment.drain-missing'


class SourceTaskRelation:
    def __init__(self, template, machine, contract, goals, *, context=None,
                 complete_state=False, occurrences=False, monitors=None, transactions=None,
                 monitor_fields=None, query_projection=False,
                 observation_scope=SourceObservation.PHASE_EVENTS):
        if type(observation_scope) is not SourceObservation:
            raise TypeError('a source task relation needs a typed observation scope')
        self.observation_scope = observation_scope
        self.machine, self.contract, self.context = machine, contract, context
        if query_projection and template.direct is None:
            raise ValueError('a query projection requires the complete direct transition compiler')
        self.query_projection = query_projection
        self.complete_state = complete_state and not query_projection
        self.monitors = monitors
        self.transactions = transactions
        self.monitor_fields = ({} if monitors is None else
            {name: (name,) for name in monitors.state_widths}) if monitor_fields is None else dict(monitor_fields)
        if set(self.monitor_fields) != (set() if monitors is None else set(monitors.state_widths)):
            raise ValueError('source monitors require exactly their declared state fields')
        from .source_initial import source_storage_leaves

        leaves = ({path: (native, width) for native, path, width in source_storage_leaves(machine.program)}
                  if self.monitor_fields else {})
        self.monitor_native_names = {}
        for name, path in self.monitor_fields.items():
            if type(path) is not tuple or path not in leaves or leaves[path][1] != monitors.state_widths[name]:
                raise ValueError('source monitor state must name a compiler-owned physical leaf of equal width')
            self.monitor_native_names[name] = leaves[path][0]
        self.protocol = ProtocolIntervalRelation(contract, context=context,
            compact_queues=template.direct is not None)
        ports = {field.port: name for name, field in machine.program.fields.items()
                 if field.port is not None}
        reads = {ports[pin] for pin in self.protocol.observed_widths}
        if transactions is not None:
            reads.update(ports[field.port] for endpoint in transactions.groups for field in endpoint.fields)
        if monitors is not None:
            reads.update(path[0] for path in self.monitor_fields.values())
            reads.update(ports[pin] for component in monitors.protocol
                         for pin in (*component.sink.adapter.artifact_controls,
                                     *(field.port for field in component.payload_fields)))
        self.source = SourceMemoryRelation(template, machine, contract.memories, goals,
            context=context, complete_state=self.complete_state, occurrences=occurrences,
            state_reads=reads, project_phases=query_projection)

    def monitor_state(self, state):
        """Project reset state through typed array positions, never name parsing."""
        from ..symbolic.functional_array import FunctionalArray

        result = {}
        for name, (field, *positions) in self.monitor_fields.items():
            value = state[field]
            for position in positions:
                value = (value.select(z3.BitVecVal(position, 32, ctx=self.context))
                         if isinstance(value, FunctionalArray) else value[position])
            result[name] = value
        return result

    def compare_orders(self, state, boundary, queues, slots, environment, *, memory_state,
                       slot_offset=0, atomic=False, actions=None, queue_lengths=None, startup=False,
                       alive=None, error=None, monitor_state=None, transaction_state=None,
                       times=None, reset_terms=None, check=None, tag='source_orders'):
        """Two independent scheduler histories, with every external choice shared.

        The result is a counterexample formula consumed by the existing solver
        owner. Initial-state and environment restrictions remain its premises.
        No baseline state, quiescent order or memory-validity branch is assumed.
        Keep unfinished prefixes: the owner must establish domain feasibility
        and rule out ``unfinished`` before interpreting an UNSAT ``different``
        query as order independence over complete histories.
        """
        from .source_confluence import compare_histories

        if not self.complete_state:
            raise ValueError('source order comparison requires complete state, not a goal cone')
        slots = tuple(slots)
        actions = None if actions is None else tuple(actions)
        times = None if times is None else tuple(times)
        histories = tuple(self.encode(state, boundary, queues, slots, environment,
            memory_state=memory_state, slot_offset=slot_offset, atomic=atomic, actions=actions,
            queue_lengths=queue_lengths, startup=startup, alive=alive, error=error,
            monitor_state=monitor_state, transaction_state=transaction_state, times=times,
            reset_terms=reset_terms, require_quiescence=False, check=check, tag=f'{tag}_{side}')
            for side in ('left', 'right'))
        return compare_histories(*histories, tuple(self.machine.program.fields), check=check)

    def initial_state(self, values, *, check=None, tag='source_task_initial'):
        """Bind a whole-history start to HDL declarations, not a replay sample.

        The lower-level interval API also accepts arbitrary recorded boundaries.
        A whole-history owner must not confuse that physical support with HDL
        initialization. Unencoded initial processes remain an explicit refusal.
        """
        return self.source.relation.initial_state(
            self.machine, values, declarations=True, check=check, tag=tag)

    def encode_request(self, request, initial, initialization, *, check=None, tag='source_request', native=None):
        steps = self.encode_request_steps(request, initial, initialization,
            check=check, tag=tag, native=native)
        try:
            return finish_construction(steps, check)
        finally:
            # This caller returns no continuation. Release compiler scopes
            # immediately on cancellation outside the suspended generator too.
            steps.close()

    def encode_request_steps(self, request, initial, initialization, *, check=None,
                             tag='source_request', native=None):
        """Build the whole history over the existing public request coordinates.

        The owner supplies the complete HDL initial support and its explicit
        physical-coordinate binding. Private initialization/order independence
        and native correspondence remain admission obligations. No observed
        baseline state, quiescence or successful DUT completion is assumed.
        Intermediate task frames are construction boundaries only;
        return the full task result after all terminal obligations are attached.
        Each iterator owns its compiler scope; immutable syntax remains shared.
        """
        from ..core.memory import MemoryEnvironmentState
        from .protocol_relation import ProtocolBoundary
        from .protocol_relation import SymbolicRequest
        from .source_initial import SourceInitialState

        if type(request) is not SymbolicRequest or request.contract != self.contract:
            raise ValueError('source request and relation must have the same task contract')
        if not (self.complete_state or self.query_projection) or type(initial) is not SourceInitialState:
            raise ValueError('a whole source request needs complete state and explicit initialization')
        if initial.bits.keys() != self.machine.program.fields.keys():
            raise ValueError('the initial support must include every compiler-owned source field')
        constraints = request.bind_initial(initialization)
        # A fixed request is useful independently of native state observations:
        # a failed replay supplies inputs but no invented post-failure samples.
        # Fold only literal coordinates, retaining their original binding
        # constraints above. Universal inputs and private initial bits stay free.
        bindings = []
        for name, term in initialization.items():
            value = request.valuations[0][name]
            if z3.is_true(value) or z3.is_false(value):
                value = z3.BitVecVal(int(z3.is_true(value)), term.size(), ctx=term.ctx)
            if z3.is_bv_value(value):
                bindings.append((term, value))
        if bindings:
            initial = initial.fixed(bindings, check=check)
        environment = request.codec.memory_environment
        if environment is not None:
            faults = environment.faults_against(self.contract.memories, len(request.slots))
            if faults:
                raise ValueError(f'source request memory environment is outside the contract: {faults[0]}')
        from contextlib import ExitStack
        with ExitStack() as scopes:
            def bind(interval):
                if interval.direct is None:
                    return interval
                compiler = interval.direct.for_request()
                scopes.enter_context(compiler.request_scope(initial, self.observation_scope))
                return replace(interval, direct=compiler)

            # Own mutable permissions at execution, not at template selection.
            # The stable selection can then share normalized/native templates
            # while suspended requests keep independent compiler scopes.
            view = copy(self)
            view.source = copy(self.source)
            view.source.relation = bind(self.source.relation)
            if native is not None:
                native = copy(native)
                if native.cover_relation is not None:
                    native.cover_relation = bind(native.cover_relation)
                native.phase_relations = {phase: bind(interval)
                    for phase, interval in native.phase_relations.items()}
            encoded = yield from view.encode_steps(initial.state, ProtocolBoundary.initial(self.contract),
                request.queues, request.slots, environment,
                memory_state=MemoryEnvironmentState.initial(self.contract.memories, environment),
                actions=request.actions, queue_lengths=request.lengths, reset_terms=request.resets,
                atomic=request.atomic, times=request.times, startup=True, release_controls=True,
                require_quiescence=False,
                tag=tag, native=native)
        member_plan = encoded.member_plan
        if member_plan is not None:
            bindings = []
            for coordinate, term in initialization.items():
                value = request.valuations[0][coordinate]
                if z3.is_bool(value):
                    value = z3.If(value, z3.BitVecVal(1, term.size(), ctx=term.ctx),
                                  z3.BitVecVal(0, term.size(), ctx=term.ctx))
                bindings.append((term, value))
            member_plan = replace(member_plan, initial=(*bindings, *member_plan.initial))
        # Terminal progress is judged only after the complete request. An
        # offered/released input is not an accepted input. A missing drain is
        # an environment fault, and an unfinished event history is unresolved;
        # neither is silently constrained away or credited as a safety hit.
        algebra = self.protocol.algebra
        pending = algebra.lor(*(encoded.protocol.pointers[-1][endpoint.name]
            < request.lengths[endpoint.name] for endpoint in self.contract.inputs()
            if endpoint.kind is Kind.TRANSACTION))
        drain = algebra.lor(*request.drain_slots)
        completed = encoded.continuable
        return replace(encoded, constraints=(*request.constraints, *constraints, *encoded.constraints),
            member_plan=member_plan,
            terminal_hits={INPUT_COMPLETION: algebra.land(completed, pending, drain)},
            terminal_assumptions={TERMINAL_ENVIRONMENT_FAULT:
                algebra.land(completed, pending, algebra.lnot(drain))},
            terminal_unresolved={INPUT_COMPLETION: algebra.land(algebra.lnot(completed), pending)})

    def compare_request(self, request, *, check=None, tag='source_request_orders',
                        observation_scope=SourceObservation.PHASE_EVENTS):
        """Audit private construction and event orders for one complete request.

        Only native writable registers and environment pins share request
        coordinates. Private source storage remains independently free in each
        history, under HDL initialization. The existing domain auditor must
        prove these choices cannot change any observed state/event or product.
        """
        from .protocol_relation import SymbolicRequest
        from .source_confluence import compare_histories
        self.require_observation_scope(observation_scope)
        if type(request) is not SymbolicRequest or request.codec.initial is None:
            raise ValueError('source request comparison requires complete construction coordinates')
        histories = []
        for side in ('left', 'right'):
            initial = self.initial_state(dict.fromkeys(self.machine.program.fields),
                                         check=check, tag=f'{tag}_{side}_initial')
            layout, binding = initial.request_layout(self.machine.program,
                                                      request.codec.initial.artifact, check=check)
            if (layout.widths != request.codec.initial.widths
                    or layout.startup_pins != request.codec.initial.startup_pins):
                raise ValueError('source request comparison requires the complete compiler layout')
            histories.append(self.encode_request(request, initial, binding,
                                                  check=check, tag=f'{tag}_{side}'))
        return compare_histories(*histories, tuple(self.machine.program.fields), check=check,
                                 observation_scope=observation_scope)

    def abstract_order_comparison(self, request, *, check=None,
                                  observation_scope=SourceObservation.PHASE_EVENTS):
        """Optional universal-proof product, never a goal/native-witness relation.

        Both histories use the same fresh pure functions. Their interpretations
        include the original arithmetic for all inputs; every legal exact
        history therefore remains. Request bounds, initial support, scheduler,
        protocol, memory and complete observation images are unchanged.
        """
        interval = self.source.relation
        abstract = interval.source.abstract_state_functions(self.machine.program.fields, check=check)
        if abstract is None:
            return None
        view = copy(self)
        view.source = copy(self.source)
        view.source.relation = replace(interval, source=abstract,
                                        drive=abstract.specialize_inputs({'action': 0}))
        return view.compare_request(request, check=check, tag='source_abstract_orders',
                                    observation_scope=observation_scope)

    def compare_native(self, request, rows, trace, catalogue, *, observation=None,
                       check=None, tag='source_native',
                       observation_scope=SourceObservation.PHASE_EVENTS):
        """Build a fresh full-domain relation, then bind the recorded request.

        Never initialize from its observed successor or select a symbolic
        history that happens to match the final output. The native comparator
        retains separate membership and all-orders agreement obligations.
        """
        from .observation_audit import compare_native_history

        self.require_observation_scope(observation_scope)

        encoded = self.bind_native_request(request, check=check, tag=tag)
        return compare_native_history(self, request, encoded, rows, trace, catalogue,
                                      observation=observation, check=check,
                                      observation_scope=observation_scope)

    def require_observation_scope(self, scope):
        if (self.observation_scope is SourceObservation.GOAL_PREFIX
                and scope is not SourceObservation.GOAL_PREFIX):
            raise ValueError('a goal-prefix construction cannot certify phase-event observations')

    def bind_native_request(self, request, *, check=None, tag='source_native'):
        """Independent full-domain formula; no recorded state or audit answers.

        A closed Analysis session may retain this formula for its fixed request.
        Each native history still requires a fresh projection and all proof
        obligations, including membership and all-orders agreement. A closed
        worker may reuse an identical fully proved obligation, never a replay
        or an unproved observation.
        """
        from .protocol_relation import SymbolicRequest

        if type(request) is not SymbolicRequest or request.codec.initial is None:
            raise ValueError('native source audit requires complete construction coordinates')
        initial = self.initial_state(dict.fromkeys(self.machine.program.fields),
                                     check=check, tag=f'{tag}_initial')
        layout, binding = initial.request_layout(self.machine.program,
                                                  request.codec.initial.artifact, check=check)
        if layout.widths != request.codec.initial.widths or layout.startup_pins != request.codec.initial.startup_pins:
            raise ValueError('native source audit requires the complete compiler construction layout')
        return self.encode_request(request, initial, binding, check=check, tag=tag)

    def encode(self, state, boundary, queues, slots, environment, *, memory_state,
               slot_offset=0, atomic=False, actions=None, queue_lengths=None,
               startup=False, release_controls=False, alive=None, error=None, require_quiescence=True,
               monitor_state=None, transaction_state=None, times=None, reset_terms=None,
               check=None, tag='source_task', native=None):
        return finish_construction(self.encode_steps(state, boundary, queues, slots, environment,
            memory_state=memory_state, slot_offset=slot_offset, atomic=atomic, actions=actions,
            queue_lengths=queue_lengths, startup=startup, release_controls=release_controls,
            alive=alive, error=error,
            require_quiescence=require_quiescence, monitor_state=monitor_state,
            transaction_state=transaction_state, times=times, reset_terms=reset_terms, tag=tag, native=native), check)

    def encode_steps(self, state, boundary, queues, slots, environment, *, memory_state,
                     slot_offset=0, atomic=False, actions=None, queue_lengths=None,
                     startup=False, release_controls=False, alive=None, error=None, require_quiescence=True,
                     monitor_state=None, transaction_state=None, times=None, reset_terms=None, tag='source_task',
                     native=None):
        """Compose explicit boundaries without resetting DUT or failure state.

        ``startup`` drains compiler-pending work before the first external
        drive. The caller supplies all initial storage/pin values and their
        admissibility constraints; this operation does not infer them from a
        baseline. Whole requests then ``release_controls`` just as ordinary
        replay does: park clocks and release reset after HDL construction.
        Both intervals stay in startup, not the first scheduled slot.
        Settling uses the scheduler's proved event bound, not a
        deadline or the number of events observed in a seed.
        Optional ``actions`` makes offers/ready symbolic in the same protocol
        product. ``reset_terms`` optionally supplies one assertion per domain
        and slot, shared by the DUT, queues, memory and all monitors. The caller
        owns prefix constraints; clock edges and memory environment stay explicit.
        With ``require_quiescence=False``, retain unfinished prefixes and stop
        all later source/protocol/memory transitions. The existing solver owner
        can query ``unfinished`` under the actual domain, without assuming the
        completeness being checked. This does not prove native correspondence.
        A monitored continuation must supply its preceding monitor state;
        only a new startup history initializes these independent automata.
        """
        if self.monitors is not None and not startup and monitor_state is None:
            raise ValueError('a monitored continuation requires its monitor boundary')
        if self.transactions is not None and not startup and transaction_state is None:
            raise ValueError('a transaction continuation requires its transaction boundary')
        if release_controls and not startup:
            raise ValueError('startup control release requires a construction history')
        slots = tuple(slots)
        reset_terms = self.protocol.reset_choices(slots, atomic=atomic, terms=reset_terms)
        times = (None,) * len(slots) if times is None else tuple(times)
        if len(times) != len(slots):
            raise ValueError('source history timestamps must match its declared slots')
        context, machine = self.context, self.machine
        if alive is None:
            alive = z3.BoolVal(True, ctx=context)
        startup_encoding = None
        previous_pins = None
        continuation = None
        if startup:
            startup_relation = (self.source.relation if native is None else
                                native.phase_relation(self.source.relation))
            startup_bound = (machine.event_bound if native is None else
                             native.phase_event_bound(machine.event_bound))
            startup_encoding = yield from startup_relation.encode_steps(state, None, event_bound=startup_bound,
                alive=alive, error=error, require_quiescence=require_quiescence,
                tag=f'{tag}_startup')
            if release_controls and self.contract.clocks:
                pins = {name: startup_encoding.state[name] for name, _ in machine.drive_inputs}
                for clock in self.contract.clocks:
                    for pin, value in ((clock.clock, int(clock.edge == 'falling')),
                                       (clock.reset, int(clock.reset_active_low))):
                        name = self.source.ports[pin]
                        pins[name] = z3.BitVecVal(value, pins[name].size(), ctx=context)
                released = yield from startup_relation.encode_steps(
                    startup_encoding.state, pins, event_bound=startup_bound,
                    alive=startup_encoding.alive, error=startup_encoding.error,
                    enabled=startup_encoding.continuable, require_quiescence=require_quiescence,
                    tag=f'{tag}_startup_release')
                previous_pins = released.inputs
                phases = (startup_encoding, released)
                startup_encoding = SourcePhaseEncoding(phases,
                    tuple(term for phase in phases for term in phase.constraints),
                    _union_images(phase.hits for phase in phases),
                    _union_images(phase.occurrences for phase in phases))
            if native is not None:
                startup_encoding = native.bind_startup(startup_encoding)
            state, alive, error = startup_encoding.state, startup_encoding.alive, startup_encoding.error
            if not require_quiescence:
                continuation = startup_encoding.continuable
        sample = tuple({pin: (z3.Bool(f'{tag}_sample_{i}_{pin}', ctx=context) if width == 1 else
                              z3.BitVec(f'{tag}_sample_{i}_{pin}', width, ctx=context))
                        for pin, width in self.protocol.observed_widths.items()} for i in range(len(slots)))
        enabled = tuple(z3.Bool(f'{tag}_sample_enabled_{i}', ctx=context) for i in range(len(slots)))
        running = (alive if continuation is None else continuation, *(
            z3.Bool(f'{tag}_running_{i}', ctx=context) for i in range(1, len(slots))))
        if native is not None:
            sample = native.samples(self.protocol.observed_widths, context)
            enabled = running = (z3.BoolVal(True, ctx=context),) * len(slots)
        protocol = yield from self.protocol.encode_steps(boundary, queues, slots, sample, atomic=atomic,
            actions=actions, queue_lengths=queue_lengths, reset_terms=reset_terms,
            enabled=running, sampled=enabled, normalize=native is not None)
        ports, driven = self.source.ports, dict(machine.drive_inputs)
        env = self.source.relation.source.environment

        def typed(pin, value):
            name = ports[pin]
            if z3.is_bool(value):
                value = z3.If(value, z3.BitVecVal(1, 1, ctx=context), z3.BitVecVal(0, 1, ctx=context))
            width = validated_width(env[driven[name]], driven[name])
            extension = width - value.size()
            if extension < 0:
                raise ValueError(f'physical pin {pin!r} exceeds its source input carrier')
            extend = z3.SignExt if machine.program.kinds[name].signed else z3.ZeroExt
            return extend(extension, value) if extension else value

        source_slots = []
        inactive = {ports[clock.clock]: int(clock.edge == 'falling') for clock in self.contract.clocks}
        for index, (slot, drive) in enumerate(zip(slots, protocol.drives, strict=True)):
            yield
            active = set(slot.ticks) if atomic else {clock.name for clock in self.contract.clocks}
            reset = set(slot.resets) if atomic else active if slot.reset else set()
            pins = {ports[pin]: typed(pin, value) for pin, value in drive.items()}
            for clock in self.contract.clocks:
                assertion = reset_terms[clock.name][index]
                value = z3.Not(assertion) if clock.reset_active_low else assertion
                pins[ports[clock.reset]] = typed(clock.reset, value)
                pins[ports[clock.clock]] = typed(clock.clock, z3.BoolVal(bool(inactive[ports[clock.clock]]), ctx=context))
            clocked = dict(pins)
            for clock in self.contract.clocks:
                if clock.name in active:
                    clocked[ports[clock.clock]] = typed(clock.clock,
                        z3.BoolVal(clock.edge == 'rising', ctx=context))
            # Every non-clock pin may change on the control drive, then holds.
            # Bound all such changes, not the seed's observed event count.
            initial = {**dict.fromkeys(driven, 0), **inactive}
            edge = {**initial, **{ports[clock.clock]: int(clock.edge == 'rising')
                                  for clock in self.contract.clocks if clock.name in active}}
            bounds = machine.phase_event_bounds(initial, (initial, edge, initial),
                                                set(driven)-set(inactive), machine.event_bound)
            if not source_slots and not all(z3.is_bv_value(state[name]) and state[name].as_long() == value
                                             for name, value in inactive.items()):
                # The first boundary may have a different clock level. Its
                # control drive can trigger a return edge before the first
                # active edge. Keep the trimmed bound for a recorded literal
                # inactive boundary; later slot boundaries are inactive too.
                bounds = (machine.event_bound, *bounds[1:])
            source_slots.append(SourceMemorySlot((pins, clocked, pins), bounds,
                                                  tuple(active), tuple(reset)))
        source_steps = self.source.encode_steps(state, source_slots, environment, initial_state=memory_state,
            slot_offset=slot_offset, alive=alive, error=error, enabled=continuation,
            require_quiescence=require_quiescence, reset_terms=reset_terms, tag=tag,
            previous_pins=previous_pins, native=native)
        links, member_steps, sample_frames = [], [], []
        if startup_encoding is not None:
            member_steps.extend(getattr(startup_encoding, 'phases', (startup_encoding,)))
        monitor_states = ([] if self.monitors is None else
            [self.monitors.initial(self.protocol.algebra, self._monitor_word)
             if monitor_state is None else monitor_state])
        transaction_states = ([] if self.transactions is None else
            [self.transactions.initial(self.protocol.algebra, zero=partial(logical_zero, context=context))
             if transaction_state is None else transaction_state])
        monitor_hits, monitor_faults, monitor_assumptions = [], [], []
        monitor_constraints, monitor_frame_sizes, transaction_hits = [], [], []
        # Partition once, not by rescanning the entire protocol at each slot.
        # The final encoding retains its original root order.
        protocol_roots = [[] for _ in slots]
        initial_roots = list(() if startup_encoding is None else startup_encoding.constraints)
        for term, owner in zip(protocol.constraints, protocol.constraint_slots, strict=True):
            (initial_roots if owner == -1 else protocol_roots[owner]).append(term)
        previous = None
        try:
            while True:
                try:
                    frame = next(source_steps)
                except StopIteration as done:
                    source = done.value
                    break
                if not isinstance(frame, SourceMemoryFrame):
                    yield  # Retain cancellation checkpoints inside a slot.
                    continue
                i, encoded = frame.index, frame.source
                first_link = len(links)
                control = encoded.phases[0]
                links.append(enabled[i] == (control.alive if require_quiescence else control.continuable))
                if previous is not None:
                    prior = previous.alive if require_quiescence else previous.continuable
                    links.append(running[i] == prior)
                    member_steps.append(((running[i], prior),))
                member_steps.append(control)
                sampled = [(enabled[i], control.alive if require_quiescence else control.continuable)]
                for pin, width in self.protocol.observed_widths.items():
                    bits = physical_bits(control.state[ports[pin]], width)
                    value = bits == 1 if width == 1 else bits
                    links.append(z3.Implies(enabled[i], sample[i][pin] == value))
                    sampled.append((sample[i][pin], value))
                member_steps.append(tuple(sampled))
                # The named protocol successor follows the actual pre-clock
                # sample, before later drives read that boundary.
                member_steps.append(tuple(tuple(term.children()) for term in protocol.step_definitions[i]))
                member_steps.extend(encoded.phases[1:])
                sample_frames.append(tuple(links[first_link:]))
                event = slots[i] if atomic else EventSlot(
                    ticks=tuple(clock.name for clock in self.contract.clocks),
                    resets=tuple(clock.name for clock in self.contract.clocks) if slots[i].reset else ())
                resets = {name: row[i] for name, row in reset_terms.items()}
                fired = {**protocol.accepted[i], **protocol.emitted[i]}
                observed, faults, assumptions, equations, transactions = {}, {}, {}, (), {}
                if self.monitors is not None:
                    successor, observed, faults, assumptions, equations = self._monitor_step(
                        monitor_states[-1], encoded, slot_offset+i, event, fired, protocol.drives[i], resets)
                    monitor_states.append(successor)
                    monitor_hits.append(observed)
                    monitor_faults.append(faults)
                    monitor_assumptions.append(assumptions)
                    monitor_constraints.extend(equations)
                    monitor_frame_sizes.append(len(equations))
                if self.transactions is not None:
                    successor, transactions = self._transaction_step(
                        transaction_states[-1], encoded, fired, event.ticks, resets, times[i])
                    transaction_states.append(successor)
                    transaction_hits.append(transactions)
                startup = startup_encoding if i == 0 else None
                roots = (*encoded.constraints, *frame.memory_constraints,
                         *protocol_roots[i], *sample_frames[-1], *equations)
                images = (encoded.hits, protocol.hits[i], observed, transactions)
                domains = (encoded.domain_faults, faults)
                yield SourceTaskFrame(i, encoded, (*initial_roots, *roots) if i == 0 else roots,
                    _union_images(images if startup is None else (startup.hits, *images)),
                    _union_images(domains if startup is None else (startup.domain_faults, *domains)),
                    assumptions, encoded.unfinished if startup is None else
                        z3.Or(startup.unfinished, encoded.unfinished), frame.memory_conflicts)
                previous = encoded
        finally:
            source_steps.close()
        from .source_member import SourceMemberPlan

        # Memory response bindings need their own ordered construction plan.
        # Omit this optional hint there; the complete relation and solver stay.
        member_plan = None if self.contract.memories else SourceMemberPlan(protocol.table_definitions, tuple(member_steps))
        return SourceTaskEncoding(source, protocol,
            (*(() if startup_encoding is None else startup_encoding.constraints),
             *source.constraints, *protocol.constraints, *links, *monitor_constraints), startup_encoding,
             tuple(monitor_states), tuple(monitor_hits), tuple(transaction_states), tuple(transaction_hits),
             monitor_faults=tuple(monitor_faults), monitor_assumptions=tuple(monitor_assumptions),
             monitor_constraints=tuple(monitor_constraints), monitor_frame_sizes=tuple(monitor_frame_sizes),
             member_plan=member_plan, sample_frames=tuple(sample_frames))

    def _transaction_step(self, state, encoded, fired, ticks, resets, time):
        """Consume actual accepted transactions with the shared typed monitors.

        Quiet slots and DUT reset are not new runs. Their history effects come
        only from the declared finite monitor/timebase, never a sampled seed.
        A cutoff before sampling leaves the monitor semantically unchanged;
        latency retains canonical inactive cells for the fixed finite unrolling.
        """
        algebra, product, ports = self.protocol.algebra, self.transactions, self.source.ports
        control, samples = encoded.phases[0], {}
        for endpoint in product.groups:
            packed = [physical_bits(control.state[ports[field.port]], packed_width(field.type))
                      for field in endpoint.fields]
            samples[endpoint.name] = ((fired[endpoint.name], logical_payload(endpoint, packed)),)
        return product.advance(state, samples, algebra,
            predicate=partial(predicate_term, context=self.context),
            condition=partial(condition_terms, context=self.context),
            logical=partial(logical_scalar, context=self.context), ticks=ticks,
            resets=resets, time=time, enabled=control.continuable)

    def _monitor_word(self, value, width):
        if type(value) is int:
            return (z3.BoolVal(bool(value), ctx=self.context) if width == 1 else
                    z3.BitVecVal(value, width, ctx=self.context))
        if z3.is_bool(value):
            return value
        bits = physical_bits(value, width)
        return bits == 1 if width == 1 else bits

    def _monitor_step(self, state, encoded, index, event, fired, drives, resets):
        """Use the shared monitor transition at its actual observation boundary.

        A protocol sample can complete before a clock/return interval is cut
        short; reset restoration needs the complete post-slot state. Preserve
        this distinction instead of treating a bound cutoff as an RTL failure.
        Reset component names are bound source storage names, not RTLIL names.
        """
        from .monitor_template import define_monitor_state

        algebra, ports = self.protocol.algebra, self.source.ports
        raw_widths = {pin: 1 for component in self.monitors.protocol
                      for pin in component.sink.adapter.artifact_controls}
        raw_widths.update({field.port: packed_width(field.type)
                          for component in self.monitors.protocol for field in component.payload_fields})
        sample, faults, assumptions = encoded.phases[0], {}, {}
        keys = {component.source.name: drives[component.identifier_field.port]
                for component in self.monitors.protocol if component.identifier_field is not None}
        successor, observed = self.monitors.step(state, index, event, fired,
            {pin: self._monitor_word(sample.state[ports[pin]], width) for pin, width in raw_widths.items()},
            self.monitor_state(encoded.state), algebra=algebra, boolean=lambda value: value, word=self._monitor_word,
            controls=drives, sampled=sample.continuable, completed=encoded.continuable,
            reset_terms=resets, faults=faults, input_keys=keys, assumptions=assumptions)
        successor, equations = define_monitor_state(successor)
        return successor, observed, faults, assumptions, equations
