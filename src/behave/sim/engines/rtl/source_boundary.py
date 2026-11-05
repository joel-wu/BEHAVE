"""Bind existing native records to source-local queries, without replaying RTL.

The protocol program supplies actual presentations and control values. Source
storage is joined through the compiler's port/storage identities, not through a
source variable's unqualified spelling. Samples and successor state are distinct:
only a clockless settled sample can stand in for a successor observation.

The first-presentation binding includes its physical clock phases. Longer
intervals additionally obtain the complete protocol/memory prefix for the
shared transition product, never the seed's future ready/valid decisions.
The one-slot fast path can use recorded memory pins; longer paths consume
prefix writes and pending reads, then construct their own future responses.
"""

from bisect import bisect_left
from dataclasses import dataclass

from ...core.memory_trace import trace_memory_drives
from ...core.physical_value import packed_width, unpack_value
from ...core.frozen import frozen_map
from ...core.protocol import AdapterReplayBoundary, payload_signals
from ...core.refusal import Refusal, Refused
from ...core.stimulus import Stimulus
from ...generation_trace import protocol_trace_steps
from ...rtl.source_boundary_capture import boundaries_of


def _refuse(code, message, **details):
    raise Refused(Refusal(code, message, **details))


@dataclass(slots=True)
class SourceTraceQuery:
    seed: Stimulus
    slot: int
    state: dict
    phases: tuple[dict, ...]
    fields: dict

    @property
    def mutable_inputs(self):
        return tuple(self.fields)

    def proposal(self, candidate):
        """Replace only selected queue cells; the scheduler owns acceptance."""
        queues = {name: list(rows) for name, rows in self.seed.queues}
        for name, (coordinate, field) in self.fields.items():
            row = list(queues[coordinate.endpoint][coordinate.transaction])
            # Source signed integers and task values meet at packed bits, using
            # the same recursive value codec as the physical Driver.
            bits = candidate[name] & ((1 << packed_width(field.type)) - 1)
            row[coordinate.field] = unpack_value(field.type, bits)
            queues[coordinate.endpoint][coordinate.transaction] = tuple(row)
        return Stimulus(queues=queues, slots=self.seed.slots,
            memory_policy=self.seed.memory_policy, memory_environment=self.seed.memory_environment,
            initial_state=self.seed.initial_state, source=self.seed.source,
            clock_schedule=self.seed.clock_schedule)


class SourceTraceBindings:
    """One read-only protocol reconstruction, reusable across a seed's queries."""

    def __init__(self, machine, contract, seed, trace):
        self.machine, self.contract, self.seed, self.trace = machine, contract, seed, trace
        self.atomic = len(contract.clocks) > 1 or seed.clock_schedule is not None
        self.boundaries = boundaries_of(trace.query_boundaries)
        self.endpoints = {endpoint.name: endpoint for endpoint in contract.inputs()}
        self.releases = {}
        self.presented = {}
        self.protocol_rows = {}
        drives = [dict(slot.controls) for slot in seed.slots]
        if contract.memories:
            # Only this first-presentation slot is queried. Its memory drive is
            # determined by the unchanged prefix and remains held through all
            # clock phases. Never extend these recorded pins into later slots:
            # that requires composing the new requests with the memory product.
            for drive, memory in zip(drives, trace_memory_drives(contract, trace), strict=True):
                drive.update(memory)
        for domain, row in protocol_trace_steps(contract, seed, trace):
            index = row.index
            if contract.clocks:
                boundary = AdapterReplayBoundary(domain, frozen_map(row.state), row.queues,
                                                 row.transition.state, row.successor_queues)
                self.protocol_rows.setdefault(domain, []).append((index, boundary))
            for key in row.releases():
                self.releases[key] = index
            for endpoint, transaction in row.presentations():
                self.presented.setdefault((endpoint, transaction), index)
            drives[index].update(row.drive.controls)
            for endpoint, payload in row.drive.presentations.items():
                drives[index].update(payload_signals(self.endpoints[endpoint], payload))
        self.ports = {field.port: name for name, field in machine.program.fields.items()
                      if field.input and field.port is not None}
        # Match the physical runner: idle payload pins are cleared every global
        # slot; inactive-domain adapter controls retain their previous drive.
        # The atomic runner initializes input-side controls to zero once.
        signals = {name: 0 for endpoint in contract.inputs()
                   for name in endpoint.adapter.environment_controls} if self.atomic else {}
        idle = {field.port: 0 for endpoint in contract.inputs() for field in endpoint.fields}
        signals.update((clock.clock, int(clock.edge == "falling")) for clock in contract.clocks)
        pins = []
        for drive in drives:
            signals.update(idle)
            signals.update(drive)
            pins.append(self._pins(signals))
        self.pins = tuple(pins)
        self._prefix = None
        self._monitors = None
        self._monitor_prefix = None

    def protocol_monitors(self):
        """The same finite obligations as replay, without a circuit frontend."""
        from ...rtl.atomic_monitors import AtomicMonitorProduct, protocol_monitor_components
        from ...rtl.protocol_relation import ProtocolIntervalRelation

        if self._monitors is None:
            parts = ProtocolIntervalRelation(self.contract).parts
            bound = max((len(rows) for _, rows in self.seed.queues), default=0)
            _, components = protocol_monitor_components(self.contract,
                tuple((domain, *part) for domain, part in sorted(parts.items())), bound)
            self._monitors = AtomicMonitorProduct(components, (), {})
        return self._monitors

    def monitor_boundary(self, start, *, check):
        """Continue shared monitors on recorded samples, not new RTL runs.

        Keep one completed prefix. Backward queries rebuild from reset;
        cancellation cannot publish a half-advanced boundary. A held output
        from before this query must not be initialized away.
        """
        from ...core.physical_value import pack_value
        from ...core.ready_valid_product import ConcreteAlgebra
        from ...core.stimulus import EventSlot

        product = self.protocol_monitors()
        word = lambda value, width: bool(value) if width == 1 else value
        widths = {pin: 1 for component in product.protocol
                  for pin in component.sink.adapter.artifact_controls}
        widths.update({field.port: packed_width(field.type)
                       for component in product.protocol for field in component.payload_fields})
        if self._monitor_prefix is not None and self._monitor_prefix[0] <= start:
            begin, state = self._monitor_prefix
        else:
            begin, state = 0, product.initial(ConcreteAlgebra, word)
        for index in range(begin, start):
            check()
            record, slot = self.trace.records[index], self.seed.slots[index]
            raw = dict(record.outputs)
            if any(type(raw.get(pin)) is not int or not 0 <= raw[pin] < 1 << width
                   for pin, width in widths.items()):
                _refuse('rtl-source-monitor-prefix-unreadable',
                    'a monitor boundary needs complete physical control and payload samples', slot=index)
            accepted = dict(record.accepted)
            event = slot if self.atomic else EventSlot(
                ticks=tuple(c.name for c in self.contract.clocks),
                resets=tuple(c.name for c in self.contract.clocks) if slot.reset else ())
            faults, assumptions = {}, {}
            state, _ = product.step(state, index, event,
                {name: payload is not None for name, payload in accepted.items()},
                raw, {}, algebra=ConcreteAlgebra, boolean=bool, word=word,
                controls={pin: bool(self.pins[index][name]) for pin, name in self.ports.items()},
                input_keys=product.request_keys(accepted, pack_value), faults=faults,
                assumptions=assumptions)
            if any(faults.values()) or any(assumptions.values()):
                _refuse('rtl-source-monitor-prefix-undefined',
                    'the selected native prefix has an undefined monitor or illegal environment',
                    slot=index)
        check()
        self._monitor_prefix = start, state
        return state

    def transaction_boundary(self, relation, start, *, check):
        """Advance the query's own symbolic transaction product over the recorded prefix.

        Constant samples give the boundary the query's sorts and context."""
        from functools import partial

        import z3

        from ...core.observation_symbolic import (
            condition_terms,
            logical_payload,
            logical_scalar,
            logical_zero,
            predicate_term,
        )
        from ...core.physical_value import pack_value
        from ...core.stimulus import EventSlot

        product, algebra, context = relation.transactions, relation.protocol.algebra, relation.context
        endpoints = {endpoint.name: endpoint for endpoint in product.groups}
        clocks = tuple(clock.name for clock in self.contract.clocks)
        schedule = self.seed.clock_schedule
        state = product.initial(algebra, zero=partial(logical_zero, context=context))
        for index in range(start):
            check()
            record, slot = self.trace.records[index], self.seed.slots[index]
            event = slot if self.atomic else EventSlot(ticks=clocks, resets=clocks if slot.reset else ())
            samples = {}
            for name, rows in product.record_samples(record).items():
                fields = endpoints[name].fields
                try:
                    samples[name] = tuple((z3.BoolVal(sampled, ctx=context), logical_payload(endpoints[name], tuple(
                        z3.BitVecVal(pack_value(field.type, value), packed_width(field.type), ctx=context)
                        for field, value in zip(fields, payload, strict=True)))) for sampled, payload in rows)
                except (TypeError, ValueError):
                    _refuse('rtl-source-transaction-prefix-unreadable',
                        'a transaction boundary needs readable recorded transfers', slot=index, endpoint=name)
            state, _ = product.advance(state, samples, algebra,
                predicate=partial(predicate_term, context=context),
                condition=partial(condition_terms, context=context),
                logical=partial(logical_scalar, context=context), ticks=event.ticks,
                resets={name: z3.BoolVal(name in event.resets, ctx=context) for name in clocks},
                time=None if schedule is None else schedule.slots[index].time)
        check()
        return state

    def product_boundary(self, start, *, check=None):
        """Recover only the selected prefix; never rerun the RTL or save every memory image."""
        from ...core.atomic import atomic_adapter_programs
        from ...core.frozen import FrozenMap
        from ...core.input_framing import InputDomainCursor
        from ...core.input_queue import InputQueueState
        from ...core.memory import MemoryEnvironmentState
        from ...core.memory_trace import MemoryTraceReader
        from ...rtl.protocol_relation import ProtocolBoundary

        if self._prefix is not None and self._prefix[0] == start:
            return self._prefix[1:]
        adapters, accepted, released = {}, {}, {}
        for domain, (view, program) in atomic_adapter_programs(self.contract).items():
            if check is not None:
                check()
            rows = self.protocol_rows.get(domain, ())
            position = bisect_left(rows, start, key=lambda row: row[0])
            if position < len(rows) and rows[position][0] == start:
                boundary = rows[position][1]
                state, queues = boundary.state, boundary.queues
            elif position:
                index, boundary = rows[position-1]
                state = program.elapse(view, boundary.successor, start-index-1)
                queues = boundary.successor_queues
            else:
                state = program.elapse(view, program.initial_state(view), start)
                queues = InputQueueState.initial(view.inputs())
            adapters[domain] = state
            accepted.update(queues.accepted)
            released.update(queues.released)
        framing = InputDomainCursor(self.contract)
        if self._prefix is not None and self._prefix[0] < start:
            begin, previous, memory_state = self._prefix
            framing.states = dict(previous.framing)
        else:
            begin = 0
            memory_state = MemoryEnvironmentState.initial(
                self.contract.memories, self.seed.memory_environment)
        # Resume only a completed prefix of this exact trace. Readers own fresh
        # mutable state, so cancellation cannot corrupt the published boundary.
        memory = MemoryTraceReader(memory_state)
        if self.contract.memories or framing.endpoints:
            for index in range(begin, start):
                if check is not None:
                    check()
                record = self.trace.records[index]
                if self.contract.memories:
                    memory.consume(dict(record.environment)['external-memory'])
                framing.advance(self.seed.slots[index], dict(record.accepted), index=index)
        controls = {pin: (self.pins[start-1][self.ports[pin]] if start else 0)
                    for e in self.contract.inputs() for pin in e.adapter.environment_controls}
        boundary = ProtocolBoundary(adapters, InputQueueState(FrozenMap(accepted), FrozenMap(released)),
                                    framing.states, controls)
        self._prefix = start, boundary, memory.snapshot()
        return self._prefix[1:]

    def _value(self, name, bits):
        kind = self.machine.program.kinds[name]
        try:
            return kind.decode(bits)
        except ValueError:
            _refuse("rtl-source-trace-value-unavailable",
                "a native source boundary needs every declared element and bit", storage=name)

    def _pins(self, signals):
        required = {name for name, _ in self.machine.drive_inputs}
        pins = {name: self._value(name, signals[port]) for port, name in self.ports.items()
                if port in signals}
        if set(pins) != required:
            _refuse("rtl-source-trace-input-unbound",
                "every compiler input must have an actual Driver binding", missing=sorted(required - set(pins)))
        return pins

    def at(self, fields, *, state_names=None, stop=None):
        """Bind one presentation, or a finite joint window, to its native entry.

        Joint windows use distinct queue-coordinate names, since several input
        transactions drive the same physical port. Only the entry is concrete;
        the task product recomputes subsequent transfers and state transitions.
        """
        fields = tuple(fields)
        starts = {self.presented.get((field.endpoint, field.transaction)) for field in fields}
        if (not starts or None in starts or (stop is None and len(starts) != 1)
                or (stop is not None and (type(stop) is not int
                    or not max(starts) < stop <= len(self.seed.slots)))):
            _refuse("rtl-source-trace-presentation-unbound",
                "a source request needs actual first presentations inside its selected interval")
        start = min(starts)
        mutable = {}
        for coordinate in fields:
            key = coordinate.endpoint, coordinate.transaction
            if self.releases[key] != coordinate.source_position:
                _refuse("rtl-source-trace-release-mismatch",
                    "the mutable field must retain its recorded queue release coordinate")
            field = self.endpoints[coordinate.endpoint].fields[coordinate.field]
            name = (self.ports[field.port] if stop is None else
                    f'queue:{coordinate.endpoint}:{coordinate.transaction}:{coordinate.field}')
            if name in mutable:
                raise ValueError('a source query cannot alias mutable queue coordinates')
            mutable[name] = coordinate, field

        # Native initial/successor observations are authoritative. Clocked
        # `outputs` are pre-commit samples and must never fill this state image.
        observed = dict(self.trace.initial_state if start == 0 else self.trace.records[start - 1].state)
        observed.update(self.boundaries.get(start, {}))
        samples = (dict(self.trace.records[start - 1].outputs)
                   if start and not self.contract.clocks else {})
        values = dict(self.pins[start - 1]) if start else {}
        missing = []
        required = self.machine.boundary_fields(state_names)
        for name, storage in self.machine.program.fields.items():
            if name not in required or name in values:
                continue
            if storage.observation is not None and storage.observation in observed:
                value = observed[storage.observation]
            elif storage.observation is not None and storage.observation in samples:
                value = samples[storage.observation]
            elif storage.port is not None and storage.port in samples:
                value = samples[storage.port]
            else:
                missing.append(name)
                continue
            values[name] = self._value(name, value)
        if missing:
            _refuse("rtl-source-trace-boundary-unavailable",
                "the native record does not contain the selected quiescent source boundary",
                slot=start, storage=missing)
        phases = [self.pins[start]]
        active = dict(phases[0])
        for clock in self.contract.clocks:
            if not self.atomic or clock.name in self.seed.slots[start].ticks:
                active[self.ports[clock.clock]] = int(clock.edge == "rising")
        if active != phases[0]:
            # A coincident event is one input image and one NBA commit, never
            # a serialized sequence of individual clock-domain transitions.
            phases.extend((active, phases[0]))
        return SourceTraceQuery(self.seed, start,
            self.machine.boundary(values, state_names=state_names), tuple(phases), mutable)
