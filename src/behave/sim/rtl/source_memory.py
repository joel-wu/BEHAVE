"""Compose source-event intervals with the shared external-memory transition.

The owner supplies protocol drives, active domains and reset events. Memory
ready/responses are *not* replay inputs: they follow requests sampled after
control settling, including changed addresses/writes and the recorded prefix.
This is a relation, not a new Driver, query policy or replay authority.
"""

from dataclasses import dataclass

import z3

from ..symbolic.encoder import validated_width
from ..core.construction_checkpoint import finish_construction
from ..core.memory import MemoryEnvironmentState, memory_artifact_signal_set, memory_port_signal_set
from .memory_exact import MemoryPinBindings, SymbolicMemoryProduct
from .source_relation import _union_images
from .symbolic_state import physical_bits


@dataclass(frozen=True, slots=True)
class SourceMemorySlot:
    """One control/sample, optional clock and clock-return sequence.

    Phases name every non-memory physical input with its typed term (or an
    explicit fresh None). The first settled phase is the request sample, not
    the clock successor. Memory drives hold across every phase of this slot.
    """

    phases: tuple
    event_bounds: tuple
    active_domains: tuple
    reset_domains: tuple = ()
    held_inputs: tuple = ()


@dataclass(frozen=True, slots=True)
class SourceMemoryFrame:
    """A completed physical slot and only its new memory obligations.

    This is a construction boundary, not a complete task/query result. The
    task owner still supplies protocol, monitor and terminal obligations.
    The first frame also retains the original memory initialization roots.
    """

    index: int
    source: object
    memory_constraints: tuple
    memory_conflicts: tuple


@dataclass(frozen=True, slots=True)
class SourceMemoryEncoding:
    slots: tuple
    memory: SymbolicMemoryProduct
    constraints: tuple
    hits: dict
    occurrences: dict

    @property
    def state(self):
        return self.slots[-1].state

    @property
    def alive(self):
        return self.slots[-1].alive

    @property
    def error(self):
        return self.slots[-1].error

    @property
    def domain_faults(self):
        return _union_images(slot.domain_faults for slot in self.slots)

    @property
    def unfinished(self):
        return z3.Or(*(slot.unfinished for slot in self.slots))

    @property
    def continuable(self):
        return self.slots[-1].continuable


class SourceMemoryRelation:
    """A source observation cone closed over actual memory request samples."""

    def __init__(self, template, machine, interfaces, goals, *, context=None,
                 complete_state=False, occurrences=False, state_reads=(), project_phases=False):
        self.machine, self.interfaces = machine, tuple(interfaces)
        self.ports = {field.port: name for name, field in machine.program.fields.items()
                      if field.port is not None}
        self.outputs, self.inputs = {}, {}
        for memory in self.interfaces:
            outputs = memory_artifact_signal_set(memory)
            for port in memory.ports:
                for pin in memory_port_signal_set(memory.name, port.id, port.kind):
                    width = (memory.addr_width if pin.endswith('_addr') else
                             memory.data_width if pin.endswith(('_rdata', '_wdata')) else 1)
                    name = self.ports[pin]
                    if machine.program.kinds[name].width != width:
                        raise ValueError(f'memory pin {pin!r} has a different source width')
                    target = self.outputs if pin in outputs else self.inputs
                    target[pin] = width
        reads = set(state_reads) | {self.ports[pin] for pin in self.outputs}
        self._phase_roots = frozenset(reads) if project_phases else None
        self.relation = template.relation(machine, goals, context=context,
            complete_state=complete_state, occurrences=occurrences, state_reads=reads)

    def encode(self, state, slots, environment, *, initial_state, slot_offset=0,
               alive=None, error=None, enabled=None, require_quiescence=True,
               reset_terms=None, check=None, tag='source_memory'):
        return finish_construction(self.encode_steps(state, slots, environment,
            initial_state=initial_state, slot_offset=slot_offset, alive=alive, error=error,
            enabled=enabled, require_quiescence=require_quiescence, reset_terms=reset_terms, tag=tag), check)

    def encode_steps(self, state, slots, environment, *, initial_state, slot_offset=0,
                     alive=None, error=None, enabled=None, require_quiescence=True,
                     reset_terms=None, tag='source_memory', previous_pins=None, native=None):
        """Link requests to later responses, preserving terminal source prefixes.

        Validity (e.g. simultaneous conflicting writes) remains in the shared
        memory product's separate named predicate. Consumers must choose its
        normal or conflict branch explicitly, as existing circuit queries do.
        """
        slots = tuple(slots)
        if not slots:
            raise ValueError('a source/memory interval needs at least one slot')
        if not isinstance(initial_state, MemoryEnvironmentState):
            raise TypeError('a source/memory interval binds an explicit complete memory boundary')
        source = self.relation.source
        context, env = source.context, source.environment
        driven = dict(self.machine.drive_inputs)
        memory_names = {self.ports[pin] for pin in self.inputs}
        physical = set(driven) - memory_names
        relation = self.relation
        if (self._phase_roots is not None and not self.interfaces and native is None
                and not any(slot.held_inputs for slot in slots)):
            from .source_direct import NativeCoverSourceCompiler
            from ..core.refusal import Refused

            if type(relation.direct) is NativeCoverSourceCompiler:
                try:
                    selected = yield from relation.direct.project_phases_steps(relation, state,
                        ((pins, bound) for slot in slots
                         for pins, bound in zip(slot.phases, slot.event_bounds, strict=True)),
                        state_reads=self._phase_roots, previous_pins=previous_pins)
                except Refused:
                    selected = None
                if selected is not None:
                    relation = selected
        from .source_relation import _HistoryControlFacts

        facts = _HistoryControlFacts()
        encoded, memory_drives = [], []

        def sample(pin, index):
            term = encoded[index].phases[0].state[self.ports[pin]]
            return physical_bits(term, self.outputs[pin])

        memory = yield from SymbolicMemoryProduct.begin_steps(self.interfaces, environment,
            MemoryPinBindings(sample, lambda pin, index: (memory_drives[index][pin],)),
            len(slots), tag=tag, active_domains=tuple(slot.active_domains for slot in slots),
            reset_terms=({memory.clock: tuple(memory.clock in slot.reset_domains for slot in slots)
                          for memory in self.interfaces} if reset_terms is None else reset_terms),
            initial_state=initial_state, slot_offset=slot_offset, context=context)
        memory_start = conflict_start = 0
        for index, slot in enumerate(slots):
            yield
            if any(set(pins) != physical for pins in slot.phases):
                raise ValueError('source/memory phases supply exactly the non-memory inputs')
            if not set(slot.held_inputs) <= physical:
                raise ValueError('memory responses belong to the product, not a held seed input')
            values, raw = {}, {}
            for pin, width in self.inputs.items():
                name = self.ports[pin]
                bits = z3.BitVec(f'{tag}_{index}_{pin}', width, ctx=context)
                raw[pin] = bits
                # Physical unsigned bits become the source's mathematical
                # signed/unsigned value at its declared IR carrier width.
                extended = validated_width(env[driven[name]], driven[name]) - width
                extend = z3.SignExt if self.machine.program.kinds[name].signed else z3.ZeroExt
                values[name] = extend(extended, bits) if extended else bits
            memory_drives.append(raw)
            phase = yield from relation.encode_phases_steps(state,
                tuple({**pins, **values} for pins in slot.phases),
                event_bounds=(slot.event_bounds if native is None else tuple(
                    native.phase_event_bound(bound, index, position)
                    for position, bound in enumerate(slot.event_bounds))), held_inputs=slot.held_inputs,
                alive=alive, error=error, enabled=enabled, require_quiescence=require_quiescence,
                tag=f'{tag}_slot{index}_phase', previous_pins=previous_pins, _facts=facts,
                phase_relations=None if native is None else tuple(
                    native.phase_relation(relation, index, position) for position in range(len(slot.phases))),
                bind_phase=None if native is None else
                    lambda phase, position: native.bind_phase(phase, index, position))
            encoded.append(phase)
            state, alive, error = phase.state, phase.alive, phase.error
            previous_pins = phase.phases[-1].inputs
            if not require_quiescence:
                enabled = phase.continuable
            yield from memory.advance_steps(phase.phases[0].continuable)
            yield SourceMemoryFrame(index, phase, tuple(memory.constraints[memory_start:]),
                                    tuple(memory.conflicts[conflict_start:]))
            memory_start, conflict_start = len(memory.constraints), len(memory.conflicts)
        return SourceMemoryEncoding(tuple(encoded), memory,
            (*tuple(term for slot in encoded for term in slot.constraints), *memory.constraints),
            _union_images(slot.hits for slot in encoded),
            _union_images(slot.occurrences for slot in encoded))
