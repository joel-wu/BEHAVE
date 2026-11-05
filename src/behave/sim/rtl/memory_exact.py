"""Exact finite product for the shared external-memory protocol.

The task and stimulus records remain owned by :mod:`behave.sim.core.memory`.
This module is only their Z3 realization: one finite array per declared memory,
one fixed-latency pipeline per read port, and the exact ready bit carried by the
stimulus for every slot.  It does not define another memory protocol.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import z3

from ..core.construction_checkpoint import finish_construction

from ..symbolic.memory_terms import memory_junk_term
from ..core.memory import (
    MEMORY_SPARSE_STATE_IMAGE,
    MemoryEnvironment,
    MemoryEnvironmentState,
    MemoryInterface,
    MemoryState,
    memory_port_prefix,
)

__all__ = [
    "EXACT_MEMORY_PRODUCT",
    "MemoryPinBindings",
    "SymbolicMemoryProduct",
    "exact_memory_capability_issues",
]


EXACT_MEMORY_PRODUCT = (
    "rtl-exact-external-memory/finite-sparse-state/"
    + MEMORY_SPARSE_STATE_IMAGE + "/cancel-on-domain-reset/sparse-overrides/6")


def exact_memory_capability_issues(interfaces):
    """Representation gaps, currently empty for every finite declared memory."""

    return ()


def _bool(term):
    return term if z3.is_bool(term) else term == 1


def _bv(term, width):
    if z3.is_bool(term):
        if width != 1:
            raise TypeError("a Boolean memory pin cannot carry a wider value")
        return z3.If(term, z3.BitVecVal(1, 1, ctx=term.ctx), z3.BitVecVal(0, 1, ctx=term.ctx))
    if not z3.is_bv(term) or term.size() != width:
        raise TypeError("a memory pin has the wrong exact bit-vector width")
    return term


def _read(model, term):
    value = model.eval(term, model_completion=True)
    if z3.is_true(value):
        return 1
    if z3.is_false(value):
        return 0
    return value.as_long()


@dataclass(frozen=True, slots=True)
class MemoryPinBindings:
    """Physical sampling/drive terms, independent of the RTL representation.

    ``sample(name, slot)`` is the request pin at the memory's sampling point.
    ``drive(name, slot)`` names every phase held at that slot's response/ready
    value. Source-event and circuit products share the same memory transition.
    """

    sample: Callable[[str, int], z3.ExprRef]
    drive: Callable[[str, int], tuple[z3.ExprRef, ...]]

    @classmethod
    def from_frames(cls, frames, explorer, steps, *, frame_cycles=None):
        cycles = (tuple((slot,) for slot in range(steps)) if frame_cycles is None
                  else tuple(tuple(row) for row in frame_cycles))
        if len(cycles) != steps or any(not row or any(
                type(cycle) is not int or cycle < 0 for cycle in row) for row in cycles):
            raise ValueError("memory frame cycles are nonempty integer tuples per slot")

        def sample(name, slot):
            if name not in explorer.out_vars_current:
                raise ValueError(f"the RTL relation has no artifact memory output {name!r}")
            return frames.term(explorer.out_vars_current[name], cycles[slot][0])

        def drive(name, slot):
            if name not in explorer.mem_inp_vars:
                raise ValueError(f"the RTL relation has no environment memory input {name!r}")
            return tuple(frames.term(explorer.mem_inp_vars[name], cycle) for cycle in cycles[slot])

        return cls(sample, drive)


class SymbolicMemoryProduct:
    """One exact external-memory trajectory beside RTL or source-event frames.

    ``initial_state`` is the complete recorded boundary for a local suffix;
    otherwise the task run supplies the cold-start image. ``slot_offset`` keeps
    ready policy indexed by the original global slot, while pin bindings and
    active domain/reset lists index this suffix. ``enabled`` identifies slots
    that reach the request sample; a terminated source prefix does not advance
    memory afterwards. All terms share the caller's context.
    """

    def __init__(self, interfaces, environment, pins, steps, *, tag,
                 active_domains=None, reset_terms=None,
                 initial_state=None, slot_offset=0, context=None, enabled=None, check=None):
        finish_construction(self._initialize_steps(interfaces, environment, pins, steps, tag=tag,
            active_domains=active_domains, reset_terms=reset_terms, initial_state=initial_state,
            slot_offset=slot_offset, context=context, enabled=enabled), check)

    @classmethod
    def construct_steps(cls, *args, **kwargs):
        """Publish only the complete memory product, retaining paused slot work."""
        product = object.__new__(cls)
        yield from product._initialize_steps(*args, **kwargs)
        return product

    @classmethod
    def begin_steps(cls, *args, **kwargs):
        """Declare one request's coordinates and initial state, not its future.

        ``advance_steps`` adds each sampled slot exactly once to this owner.
        Pending read responses and writes retain their original coordinates;
        no shorter request or concrete boundary is reconstructed.
        """
        product = object.__new__(cls)
        yield from product._declare_steps(*args, **kwargs)
        return product

    def _initialize_steps(self, interfaces, environment, pins, steps, *, tag,
                          active_domains=None, reset_terms=None,
                          initial_state=None, slot_offset=0, context=None, enabled=None):
        if enabled is not None:
            enabled = tuple(enabled)
            if len(enabled) != steps or any(not z3.is_bool(sample) for sample in enabled):
                raise ValueError("a memory product needs one Boolean sample enable per slot")
        yield from self._declare_steps(interfaces, environment, pins, steps, tag=tag,
            active_domains=active_domains, reset_terms=reset_terms, initial_state=initial_state,
            slot_offset=slot_offset, context=context)
        initial_roots = len(self.constraints)
        for slot in range(steps):
            yield from self.advance_steps(None if enabled is None else enabled[slot])
        # Preserve the batch interface's transition-before-initialization
        # order. Equal conjunctions can cause very different SMT preprocessing;
        # streaming callers instead need initialization in their first frame.
        self.constraints[:] = self.constraints[initial_roots:] + self.constraints[:initial_roots]

    def _declare_steps(self, interfaces, environment, pins, steps, *, tag,
                       active_domains=None, reset_terms=None,
                       initial_state=None, slot_offset=0, context=None):
        if type(steps) is not int or steps < 0:
            raise ValueError("a memory request needs a nonnegative slot count")
        if type(interfaces) not in (tuple, list) or any(
                type(memory) is not MemoryInterface for memory in interfaces):
            raise TypeError("a symbolic memory product needs MemoryInterface records")
        self.interfaces = tuple(interfaces)
        if not self.interfaces and environment is None:
            environment = MemoryEnvironment()
        if type(environment) is not MemoryEnvironment:
            raise TypeError("a symbolic memory product needs one MemoryEnvironment")
        issues = exact_memory_capability_issues(self.interfaces)
        if issues:
            from ..core.refusal import Refusal, Refused

            raise Refused(Refusal(
                "rtl-exact-memory-depth-outside-capability",
                "the task's sparse external memory cannot be represented by this "
                "versioned finite exact product",
                product=EXACT_MEMORY_PRODUCT, first=issues[0], count=len(issues)))
        self.environment = environment
        self.context = z3.main_ctx() if context is None else context
        if type(slot_offset) is not int or slot_offset < 0:
            raise ValueError("a memory continuation starts at a nonnegative global slot")
        self.slot_offset = slot_offset
        self.initial_state = (MemoryEnvironmentState.initial(self.interfaces, environment)
                              if initial_state is None else initial_state)
        if type(self.initial_state) is not MemoryEnvironmentState:
            raise TypeError("a memory continuation needs a complete memory boundary")
        self.pins = pins
        self.steps = steps
        self.built_slots = 0
        self._advancing = False
        if active_domains is None:
            active_domains = tuple(
                frozenset(memory.clock for memory in self.interfaces)
                for _slot in range(steps))
        if type(active_domains) not in (tuple, list) \
                or len(active_domains) != steps \
                or any(type(domains) not in (tuple, list, set, frozenset)
                       or any(type(domain) is not str or not domain
                              for domain in domains)
                       for domains in active_domains):
            raise TypeError(
                "symbolic memory active domains are finite name sets per slot")
        self.active_domains = tuple(frozenset(domains)
                                    for domains in active_domains)
        self.reset_terms = {}
        for domain, terms in (reset_terms or {}).items():
            if len(terms) != steps:
                raise ValueError("memory reset terms need one Boolean per slot")
            self.reset_terms[domain] = tuple(
                z3.BoolVal(term, ctx=self.context) if type(term) is bool else term for term in terms)
            if any(not z3.is_bool(term) for term in self.reset_terms[domain]):
                raise TypeError("memory reset terms must be Boolean")
        self.constraints = []
        self.arrays = {}
        self.initial_presence = {}
        self.write_values = {}
        self.write_flags = {}
        self.responses = {}
        self.delays = {}
        self.conflicts = []
        self.write_actions = {}

        for memory in self.interfaces:
            run = environment.run(memory.name)
            if run is None:
                raise ValueError(f"memory environment omits {memory.name!r}")
            if run.ready_policy is None and any(
                    len(bits) < slot_offset + steps for _port, bits in run.ready):
                raise ValueError(
                    f"memory {memory.name!r} ready schedule is shorter than the "
                    "encoded prefix")
            yield
            self._declare_memory(memory, run, tag)
        # The same complete carrier binds cold starts and recorded continuations;
        # do not silently empty pending responses or reset earlier writes.
        self.constraints.extend(self.state_constraints(0, self.initial_state.as_dict()))

    def advance_steps(self, enabled=None):
        """Commit one complete slot; interruption resumes this same iterator.

        A discarded or failed partial advance cannot be retried on this owner:
        its in-progress marker prevents publishing duplicate/partial equations.
        The consumer must then retire the whole unfinished construction.
        """
        if self._advancing or self.built_slots >= self.steps:
            raise ValueError("memory advances require an idle, unfinished construction")
        enabled = z3.BoolVal(True, ctx=self.context) if enabled is None else enabled
        if not z3.is_bool(enabled) or enabled.ctx != self.context:
            raise ValueError("a memory sample enable must be Boolean in the product context")
        self._advancing = True
        for memory in self.interfaces:
            yield
            self._step(memory, self.environment.run(memory.name), self.built_slots, enabled)
        self.built_slots += 1
        self._advancing = False

    @staticmethod
    def _cell_array(memory, cells, *, context=None):
        """Explicit values only; absence is resolved by ``read_cell``.

        A named array equated with a Lambda of the deterministic junk function
        introduces quantified array equalities into otherwise finite queries.
        Keep sparse overrides in ordinary arrays and apply that exact function
        only at read addresses. Unwritten cells are NOT assumed to be zero.
        """
        sort = z3.BitVecSort(memory.addr_width, ctx=context)
        array = z3.K(sort, z3.BitVecVal(0, memory.data_width, ctx=context))
        for position, value in cells:
            array = z3.Store(
                array, z3.BitVecVal(position, memory.addr_width, ctx=context),
                z3.BitVecVal(value, memory.data_width, ctx=context))
        return array

    def read_cell(self, memory, boundary, address):
        """Initial explicit cells plus all writes override deterministic junk."""
        present = z3.Or(z3.Select(self.initial_presence[memory.name], address),
                        z3.Select(self.write_flags[memory.name][boundary], address))
        return z3.If(present, z3.Select(self.arrays[memory.name][boundary], address),
                     memory_junk_term(memory.name, address, memory.data_width))

    def _declare_memory(self, memory, run, tag):
        addr_sort = z3.BitVecSort(memory.addr_width, ctx=self.context)
        data_sort = z3.BitVecSort(memory.data_width, ctx=self.context)
        arrays = tuple(z3.Array(
            f"{tag}_memory_{memory.name}_cells_{slot}", addr_sort, data_sort)
                       for slot in range(self.steps + 1))
        write_values = tuple(z3.Array(
            f"{tag}_memory_{memory.name}_writes_{slot}", addr_sort, data_sort)
                             for slot in range(self.steps + 1))
        write_flags = tuple(z3.Array(
            f"{tag}_memory_{memory.name}_written_{slot}", addr_sort,
            z3.BoolSort(ctx=self.context)) for slot in range(self.steps + 1))
        self.arrays[memory.name] = arrays
        self.write_values[memory.name] = write_values
        self.write_flags[memory.name] = write_flags
        present = z3.K(addr_sort, z3.BoolVal(False, ctx=self.context))
        for address, _ in self.initial_state.state(memory.name).cells:
            present = z3.Store(present, z3.BitVecVal(address, memory.addr_width, ctx=self.context),
                               z3.BoolVal(True, ctx=self.context))
        self.initial_presence[memory.name] = present
        self.write_actions[memory.name] = [() for _slot in range(self.steps)]
        for port in memory.ports:
            if not port.reads:
                continue
            key = (memory.name, port.id)
            self.responses[key] = tuple((
                z3.Bool(f"{tag}_memory_{memory.name}_{port.id}_resp_v_{slot}", ctx=self.context),
                z3.BitVec(
                    f"{tag}_memory_{memory.name}_{port.id}_resp_a_{slot}",
                    memory.addr_width, ctx=self.context),
                z3.BitVec(
                    f"{tag}_memory_{memory.name}_{port.id}_resp_d_{slot}",
                    memory.data_width, ctx=self.context),
            ) for slot in range(self.steps + 1))
            self.delays[key] = tuple(tuple((
                z3.Bool(
                    f"{tag}_memory_{memory.name}_{port.id}_delay_{stage}_v_{slot}", ctx=self.context),
                z3.BitVec(
                    f"{tag}_memory_{memory.name}_{port.id}_delay_{stage}_a_{slot}",
                    memory.addr_width, ctx=self.context),
                z3.BitVec(
                    f"{tag}_memory_{memory.name}_{port.id}_delay_{stage}_d_{slot}",
                    memory.data_width, ctx=self.context),
            ) for stage in range(run.read_latency - 1))
                for slot in range(self.steps + 1))
    def _step(self, memory, run, slot, enabled):
        resets = self.reset_terms.get(memory.clock)
        reset = z3.BoolVal(False, ctx=self.context) if resets is None else resets[slot]
        state_reset = z3.And(enabled, reset)
        array = self.arrays[memory.name][slot]
        next_array = array
        writes = self.write_values[memory.name][slot]
        written = self.write_flags[memory.name][slot]
        next_writes, next_written = writes, written
        write_actions = []

        for port in memory.ports:
            prefix = memory_port_prefix(memory.name, port.id)
            ready = bool(run.ready_at(port.id, self.slot_offset + slot))
            for term in self.pins.drive(prefix + "_req_ready", slot):
                self.constraints.append(_bool(term) == z3.And(z3.Not(reset), ready))
            if port.reads:
                response = self.responses[(memory.name, port.id)][slot]
                for term in self.pins.drive(prefix + "_resp_valid", slot):
                    self.constraints.append(_bool(term) == z3.And(z3.Not(reset), response[0]))
                for term in self.pins.drive(prefix + "_resp_rdata", slot):
                    self.constraints.append(
                        _bv(term, memory.data_width)
                        == z3.If(z3.And(z3.Not(reset), response[0]), response[2],
                                 z3.BitVecVal(0, memory.data_width, ctx=self.context)))

        if memory.clock not in self.active_domains[slot]:
            self.constraints.extend((
                self.arrays[memory.name][slot + 1] == array,
                self.write_values[memory.name][slot + 1] == writes,
                self.write_flags[memory.name][slot + 1] == written,
            ))
            for port in memory.ports:
                if not port.reads:
                    continue
                key = (memory.name, port.id)
                self._carry_read(self.responses[key][slot + 1],
                                 self.responses[key][slot], state_reset)
                for next_stage, stage in zip(
                        self.delays[key][slot + 1], self.delays[key][slot]):
                    self._carry_read(next_stage, stage, state_reset)
            return

        for port in memory.ports:
            prefix = memory_port_prefix(memory.name, port.id)
            ready = bool(run.ready_at(port.id, self.slot_offset + slot))
            valid = _bool(self.pins.sample(prefix + "_req_valid", slot))
            accepted = z3.And(enabled, valid, ready, z3.Not(reset))
            address = _bv(
                self.pins.sample(prefix + "_req_addr", slot), memory.addr_width)
            reading = z3.BoolVal(port.kind == "r", ctx=self.context)
            if port.kind == "rw":
                reading = z3.Not(_bool(self.pins.sample(prefix + "_req_we", slot)))
            read = z3.And(accepted, reading)

            if port.reads:
                value = self.read_cell(memory, slot, address)
                key = (memory.name, port.id)
                response_next = self.responses[key][slot + 1]
                delay_now = self.delays[key][slot]
                delay_next = self.delays[key][slot + 1]
                entering = (read, address, value)
                leaving = delay_now[0] if delay_now else entering
                retained = tuple(z3.If(enabled, new, old) for new, old in
                                 zip(leaving, self.responses[key][slot], strict=True))
                self._carry_read(response_next, retained, state_reset)
                if delay_now:
                    shifted = (*delay_now[1:], entering)
                    for left, right, old in zip(delay_next, shifted, delay_now, strict=True):
                        retained = tuple(z3.If(enabled, new, held) for new, held in zip(right, old, strict=True))
                        self._carry_read(left, retained, state_reset)

            if port.writes:
                write = z3.And(accepted, z3.Not(reading))
                value = _bv(
                    self.pins.sample(prefix + "_req_wdata", slot), memory.data_width)
                write_actions.append((write, address, value))

        # The concrete protocol has no port priority: distinct simultaneous writes
        # commit atomically, while two accepted writes to one address are outside the
        # relation.  Accumulating Stores represents the former without accidentally
        # giving the last declaration iteration priority.
        for index, (left, left_address, _left_value) in enumerate(write_actions):
            for right, right_address, _right_value in write_actions[index + 1:]:
                self.conflicts.append(z3.And(
                    left, right, left_address == right_address))
        for write, address, value in write_actions:
            next_array = z3.If(
                write, z3.Store(next_array, address, value), next_array)
            next_writes = z3.If(
                write, z3.Store(next_writes, address, value), next_writes)
            next_written = z3.If(
                write,
                z3.Store(next_written, address, z3.BoolVal(True, ctx=self.context)),
                next_written)
        self.write_actions[memory.name][slot] = tuple(write_actions)

        self.constraints.extend((
            self.arrays[memory.name][slot + 1] == next_array,
            self.write_values[memory.name][slot + 1] == next_writes,
            self.write_flags[memory.name][slot + 1] == next_written,
        ))

    def _carry_read(self, successor, current, reset):
        """Invalidate queued/visible reads on reset; inactive payloads are ignored."""
        self.constraints.extend((successor[0] == z3.And(z3.Not(reset), current[0]),
                                 successor[1] == current[1],
                                 successor[2] == current[2]))

    @property
    def conflict(self):
        """Whether any slot violates the protocol's no-write-priority rule."""

        return z3.Or(*self.conflicts) if self.conflicts else z3.BoolVal(False, ctx=self.context)

    @property
    def validity_constraints(self):
        """Normal-run constraints, kept separate so conflicts can be named first."""

        return tuple(z3.Not(conflict) for conflict in self.conflicts)

    def state_image(self, model, boundary):
        """Canonical sparse core state at one slot boundary."""

        if type(boundary) is not int or not 0 <= boundary <= self.built_slots:
            raise ValueError("a memory image needs a completed state boundary")
        rows = []
        for memory in self.interfaces:
            initial = self.initial_state.state(memory.name)
            cells, writes = dict(initial.cells), dict(initial.writes)
            for actions in self.write_actions[memory.name][:boundary]:
                for active, address, value in actions:
                    if not z3.is_true(model.eval(active, model_completion=True)):
                        continue
                    position = _read(model, address)
                    stored = _read(model, value)
                    cells[position] = stored
                    writes[position] = stored
            pending = []
            responses = []
            for port in memory.ports:
                if not port.reads:
                    continue
                key = (memory.name, port.id)
                response = self.responses[key][boundary]
                responses.append((
                    port.id,
                    None if not z3.is_true(model.eval(
                        response[0], model_completion=True)) else (
                            _read(model, response[1]), _read(model, response[2]))))
                queue = []
                for index, stage in enumerate(self.delays[key][boundary]):
                    if z3.is_true(model.eval(stage[0], model_completion=True)):
                        queue.append((
                            _read(model, stage[1]), _read(model, stage[2]),
                            index + 1))
                pending.append((port.id, queue))
            rows.append((memory.name, MemoryState(
                cells=cells.items(), pending=pending, responses=responses,
                writes=writes.items())))
        return MemoryEnvironmentState(rows).as_dict()

    def semantic_terms(self):
        """Every symbolic component that gives the memory product its meaning."""

        terms = []
        for memory in self.interfaces:
            terms.extend(self.arrays[memory.name])
            terms.extend(self.write_values[memory.name])
            terms.extend(self.write_flags[memory.name])
            for port in memory.ports:
                if not port.reads:
                    continue
                key = (memory.name, port.id)
                for response in self.responses[key]:
                    terms.extend(response)
                for boundary in self.delays[key]:
                    for stage in boundary:
                        terms.extend(stage)
        return tuple(terms)

    def state_constraints(self, boundary: int, image: object) -> tuple[z3.BoolRef, ...]:
        """Equalities fixing every memory component at a local-query boundary."""
        if type(boundary) is not int or not 0 <= boundary <= self.built_slots:
            raise ValueError("a memory boundary names one completed state")
        state_record = MemoryEnvironmentState.from_record(image)
        faults = state_record.faults_against(self.interfaces, self.environment)
        if faults:
            raise ValueError(f"the memory boundary is outside its declaration: {faults[0]}")

        constraints = []
        for memory in self.interfaces:
            state = state_record.state(memory.name)
            writes = dict(state.writes)
            expected_cells = self._cell_array(memory, state.cells, context=self.context)
            expected_writes = z3.K(
                z3.BitVecSort(memory.addr_width, ctx=self.context),
                z3.BitVecVal(0, memory.data_width, ctx=self.context))
            expected_written = z3.K(
                z3.BitVecSort(memory.addr_width, ctx=self.context), z3.BoolVal(False, ctx=self.context))
            for address, value in writes.items():
                index = z3.BitVecVal(address, memory.addr_width, ctx=self.context)
                expected_writes = z3.Store(
                    expected_writes, index,
                    z3.BitVecVal(value, memory.data_width, ctx=self.context))
                expected_written = z3.Store(
                    expected_written, index, z3.BoolVal(True, ctx=self.context))
            constraints.extend((
                self.arrays[memory.name][boundary] == expected_cells,
                self.write_values[memory.name][boundary] == expected_writes,
                self.write_flags[memory.name][boundary] == expected_written,
            ))

            pending = dict(state.pending)
            responses = dict(state.responses)
            for port in memory.ports:
                if not port.reads:
                    continue
                key = (memory.name, port.id)
                response = responses[port.id]
                symbolic_response = self.responses[key][boundary]
                constraints.append(
                    symbolic_response[0] == z3.BoolVal(response is not None, ctx=self.context))
                if response is not None:
                    constraints.extend((
                        symbolic_response[1]
                        == z3.BitVecVal(response[0], memory.addr_width, ctx=self.context),
                        symbolic_response[2]
                        == z3.BitVecVal(response[1], memory.data_width, ctx=self.context),
                    ))
                by_left = {entry[2]: entry for entry in pending[port.id]}
                for index, stage in enumerate(self.delays[key][boundary]):
                    entry = by_left.get(index + 1)
                    constraints.append(stage[0] == z3.BoolVal(entry is not None, ctx=self.context))
                    if entry is not None:
                        constraints.extend((
                            stage[1] == z3.BitVecVal(
                                entry[0], memory.addr_width, ctx=self.context),
                            stage[2] == z3.BitVecVal(
                                entry[1], memory.data_width, ctx=self.context),
                        ))
        return tuple(constraints)
