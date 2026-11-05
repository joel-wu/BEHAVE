"""Queue/adapter continuation independent of the RTL representation.

Registered adapters own protocol semantics. This relation supplies changing
queue heads and actual sampled outputs to those adapters, rather than freezing
the seed's future handshakes. It does not execute or score a candidate.
"""

from dataclasses import dataclass

import z3

from ..core.adapter_exact import adapter_exact_product
from ..core.atomic import atomic_adapter_programs
from ..core.construction_checkpoint import finish_construction
from ..core.input_queue import InputQueueState, input_is_present, input_release_step
from ..core.physical_value import pack_value, packed_width
from ..core.refusal import Refusal, Refused
from .physical_symbolic import valid_packed_pattern
from .monitor_template import define_monitor_state
from .task_symbolic import Z3Algebra


@dataclass(frozen=True, slots=True)
class ProtocolBoundary:
    adapters: dict
    queues: InputQueueState
    framing: dict
    controls: dict

    @classmethod
    def initial(cls, contract):
        return cls(
            {domain: program.initial_state(view)
             for domain, (view, program) in atomic_adapter_programs(contract).items()},
            InputQueueState.initial(contract.inputs()),
            {e.name: e.framing.initial for e in contract.inputs() if e.framing is not None},
            {pin: 0 for e in contract.inputs() for pin in e.adapter.environment_controls})


@dataclass(frozen=True, slots=True)
class ProtocolActions:
    """One slot's queue releases and adapter controls, excluding clock/reset.

    Terms may be fixed or symbolic. Clock edges and resets belong to the
    declared slot schedule; neither can be smuggled in as a free ready signal.
    """

    offers: dict
    controls: dict


@dataclass(frozen=True, slots=True)
class ProtocolIntervalEncoding:
    constraints: tuple
    drives: tuple
    accepted: tuple
    emitted: tuple
    hits: tuple
    adapters: tuple
    pointers: tuple
    released: tuple
    framing: tuple
    # Root ownership for sufficient prefix proofs; -1 denotes initial/domain
    # constraints. The complete relation always retains every original root.
    constraint_slots: tuple = ()
    # Total successor equations, retained in constraints, in execution order.
    # They cut repeated prefix expansion but add no state restriction.
    step_definitions: tuple = ()
    # Optional private-table completions for concrete member construction.
    table_definitions: tuple = ()


class SymbolicRequest:
    """The existing codec's complete queue, action and initialization domain.

    This owns environment choices, not RTL transitions, a solver or evidence.
    Releasing every queued input does not assume the artifact accepts it.
    Concrete codecs remain independent of this symbolic realization.
    """

    def __init__(self, codec, steps, *, context=None, check=None, tag='rtl_request'):
        import z3
        from ..core.protocol import registered_adapter
        from ..core.stimulus import EventSlot
        from .atomic_request import AtomicRequestCodec
        from .physical_symbolic import valid_packed_pattern
        from .task_request import ReadyValidRequestCodec
        from .settled_request import SettledRequestCodec
        from .task_symbolic import Z3Algebra

        if type(codec) not in (ReadyValidRequestCodec, AtomicRequestCodec, SettledRequestCodec):
            raise TypeError('a symbolic request uses an existing closed RTL request codec')
        if type(steps) is not int or steps < 1:
            raise ValueError('a symbolic request needs a positive exact horizon')
        self.codec, self.contract, self.context = codec, codec.contract, context
        self.atomic = type(codec) is AtomicRequestCodec
        self.settled = type(codec) is SettledRequestCodec
        if self.atomic and len(codec.schedule.slots) != steps:
            raise ValueError('the symbolic horizon must equal the bound global schedule')
        if self.atomic:
            from .exact_product import stimulus_for_global_schedule

            shape = stimulus_for_global_schedule(codec.schedule,
                {endpoint.name: () for endpoint in self.contract.inputs()},
                source='symbolic request domain', memory_environment=codec.memory_environment)
            faults = shape.faults_against(self.contract)
            if faults:
                raise Refused(Refusal('rtl-atomic-request-outside-contract',
                    'the fixed source request schedule is outside its contract', first=faults[0]))
        a = Z3Algebra(context)
        self.slots = (tuple(slot.event for slot in codec.schedule.slots) if self.atomic
                      else codec.slots(steps) if self.settled else (EventSlot(),) * steps)
        self.times = (tuple(slot.time for slot in codec.schedule.slots) if self.atomic else None)
        if self.atomic or self.settled:
            lengths, cells, offers = codec.lengths, codec.cells, codec.offers
            controls = self.contract.scheduled_controls()
        else:
            lengths, cells = {codec.source.name: codec.length}, {codec.source.name: codec.cells}
            offers, controls = {codec.source.name: codec.offer}, (codec.ready, codec.reset)
        variable = {*offers.values(), *controls}
        self._variable_coordinates = tuple(sorted(variable))
        constraints, common = [], {}
        for name, (low, high) in codec.bounds.items():
            if check is not None:
                check()
            if name in variable:
                continue
            symbol = f'{len(tag)}:{tag}:{name}'
            if name in lengths.values():
                term = z3.Int(symbol, ctx=context)
                constraints.extend((term >= low, term <= high))
            else:
                width = high.bit_length()
                if low != 0 or high != (1 << width) - 1:
                    raise ValueError('a packed request coordinate needs its complete bit support')
                term = z3.Bool(symbol, ctx=context) if width == 1 else z3.BitVec(symbol, width, ctx=context)
            common[name] = term
        self.lengths = {name: common[coordinate] for name, coordinate in lengths.items()}
        self.queues = {name: tuple(tuple(common[n] for n in row) for row in rows)
                       for name, rows in cells.items()}
        for endpoint in self.contract.inputs():
            for index, row in enumerate(self.queues[endpoint.name]):
                if check is not None:
                    check()
                # Unused cells are bit patterns, not transactions; the codec
                # permits even unused enumeration tag patterns.
                constraints.extend(z3.Implies(self.lengths[endpoint.name] > index,
                    valid_packed_pattern(term, field.type))
                    for term, field in zip(row, endpoint.fields, strict=True))
        self.valuations = tuple({**common, **{
            name: z3.Bool(f'{len(tag)}:{tag}:{slot}:{name}', ctx=context)
            for name in sorted(variable)}} for slot in range(steps))
        # Keep every public coordinate and its domain equation. The transition
        # builder can nevertheless use controls already fixed by that domain,
        # instead of expanding both sides and rediscovering them after unrolling.
        action_rows = [{name: row[name] for name in variable} for row in self.valuations]
        if self.atomic or self.settled:
            for row, action, slot in zip(self.valuations, action_rows, self.slots, strict=True):
                fixed = {coordinate: name in slot.offers for name, coordinate in offers.items()}
                scheduled = dict(slot.controls)
                fixed.update({pin: bool(scheduled[pin]) for pin in controls})
                constraints.extend(row[name] == value for name, value in fixed.items())
                action.update({name: a.bool(value) for name, value in fixed.items()})
            if self.settled:
                constraints.extend(length == steps for length in self.lengths.values())
        else:
            clock, = self.contract.clocks
            for index, (row, action) in enumerate(zip(self.valuations, action_rows, strict=True)):
                reset = a.lnot(row[clock.reset]) if clock.reset_active_low else row[clock.reset]
                if index <= codec.reset_hold:
                    constraints.append(reset == (index < codec.reset_hold))
                    action[clock.reset] = a.bool((index < codec.reset_hold) != clock.reset_active_low)
                constraints.append(z3.Implies(reset, a.lnot(row[codec.offer])))
                if index < codec.reset_hold:
                    action[codec.offer] = a.bool(False)
        self.resets = {clock.name: tuple(z3.simplify(a.lnot(row[clock.reset])) if clock.reset_active_low
                      else row[clock.reset] for row in action_rows) for clock in self.contract.clocks}
        reset_pins = {clock.reset for clock in self.contract.clocks}
        self.actions = tuple(ProtocolActions(
            {name: row[coordinate] for name, coordinate in offers.items()},
            {pin: row[pin] for pin in controls if pin not in reset_pins}) for row in action_rows)

        def active(endpoint, index):
            if self.settled:
                return a.bool(True)
            return a.land(not self.atomic or endpoint.clock in self.slots[index].ticks,
                          a.lnot(self.resets[endpoint.clock][index]))

        # Terminal environment membership depends on the schedule alone, not
        # DUT liveness, acceptance, event-order completeness or error flags.
        relevant_offers, active_inputs = [], []
        self.release_capacity = {}
        for endpoint in self.contract.inputs():
            releases = tuple(a.land(active(endpoint, i), action.offers[endpoint.name])
                             for i, action in enumerate(self.actions))
            capacity = a.count(releases)
            self.release_capacity[endpoint.name] = capacity
            constraints.append(capacity >= self.lengths[endpoint.name])
            nonempty = self.lengths[endpoint.name] > 0
            relevant_offers.append(tuple(a.land(nonempty, term) for term in releases))
            active_inputs.append(tuple(a.land(nonempty, active(endpoint, i)) for i in range(steps)))
        offered = tuple(a.lor(*(row[i] for row in relevant_offers)) for i in range(steps))
        suffix, later = [], a.bool(False)
        for term in reversed(offered):
            later = a.lor(term, later)
            suffix.append(later)
        seen, drains = a.bool(False), []
        for i, (action, later) in enumerate(zip(self.actions, reversed(suffix), strict=True)):
            if check is not None:
                check()
            drain = a.land(seen, a.lnot(later), a.lor(*(row[i] for row in active_inputs)))
            drains.append(drain)
            for endpoint in self.contract.outputs():
                receptive = registered_adapter(endpoint.adapter.name, endpoint.adapter.version).receptive_controls(endpoint)
                constraints.extend(z3.Implies(a.land(drain, active(endpoint, i)), action.controls[pin])
                                   for pin in receptive)
            seen = a.lor(seen, offered[i])
        self.drain_slots = tuple(drains)
        self.constraints = tuple(constraints)

    def full_queue_hint(self):
        """Propose available input for this calendar, never restrict the domain.

        Different clocks can release different numbers of inputs. A physical
        queue's capacity is not the number a fixed calendar can deliver. The
        caller must still validate the complete source/protocol execution.
        """
        import z3
        return tuple(length == z3.If(self.release_capacity[name] < len(self.queues[name]),
            self.release_capacity[name], len(self.queues[name])) for name, length in self.lengths.items())

    def bind_initial(self, terms):
        """Bind every public initialization coordinate to physical source bits.

        The source owner supplies HDL initialization and private storage support.
        No seed or zero fallback is inferred. This mapping alone does not prove
        unrepresented private initial choices are observationally inert.
        """
        import z3

        initial = self.codec.initial
        if initial is None or initial.startup_pins is None:
            raise ValueError('a whole source history needs pre-HDL construction coordinates')
        if terms.keys() != initial.bounds.keys():
            raise ValueError('source initialization must bind every request coordinate exactly once')
        constraints = []
        for coordinate, _, width in (*initial.coordinates, *initial.pin_coordinates):
            term, choice = terms[coordinate], self.valuations[0][coordinate]
            if not z3.is_bv(term) or term.size() != width or term.ctx != choice.ctx:
                raise TypeError('initial source bits must have the physical width and request context')
            constraints.append(choice == (term == 1) if z3.is_bool(choice) else choice == term)
        return tuple(constraints)

    def fix(self, rows):
        """Bind a validated public request without discarding unused cells."""
        import z3

        if len(rows) != len(self.valuations):
            raise ValueError('a fixed request must retain the symbolic horizon')
        self.codec.decode(rows)
        # Queue cells and initial coordinates share the same symbol in every
        # slot. Keep their equality once, after validating the complete rows;
        # unused cells remain bound and inconsistent rows are still rejected.
        seen, constraints = set(), []
        for index, (terms, row) in enumerate(zip(self.valuations, rows, strict=True)):
            # decode above checks every repeated queue/initial coordinate.
            # Their symbolic owner is shared too: after the first slot, only
            # action coordinates can add a new equality. Avoid one solver-API
            # visit per queue cell per slot merely to rediscover that sharing.
            for name in terms if index == 0 else self._variable_coordinates:
                term = terms[name]
                key = term.get_id(), row[name]
                if key not in seen:
                    seen.add(key)
                    constraints.append(term == (bool(row[name]) if z3.is_bool(term) else row[name]))
        return tuple(constraints)

    def _map_valuations(self, transform):
        """Transform shared queue/initial terms once, preserving the public rows."""
        variable = frozenset(self._variable_coordinates)
        common = {name: transform(term) for name, term in self.valuations[0].items()
                  if name not in variable}
        return tuple({**common, **{name: transform(row[name])
                                  for name in self._variable_coordinates}}
                     for row in self.valuations)

    def fixed(self, rows):
        """Partially evaluate this request, without changing the owning domain.

        This private view is for membership checks of one validated request.
        The universal owner and its coordinates remain untouched.
        """
        from copy import copy
        from .symbolic_state import fold_fixed_terms

        bindings = tuple(tuple(eq.children()) for eq in self.fix(rows))
        folded = {term.get_id(): value for term, value in bindings}
        # Queue, action and initialization coordinates are already literals.
        # Fold the derived request expressions as ONE shared DAG. Passing the
        # complete (potentially thousands-wide) binding table separately for
        # every reset, drain and constraint otherwise dominates native audit.
        derived = (*self.release_capacity.values(),
                   *(term for action in self.actions
                     for term in (*action.offers.values(), *action.controls.values())),
                   *(term for terms in self.resets.values() for term in terms),
                   *self.drain_slots, *self.constraints)
        roots = {term.get_id(): term for term in derived if term.get_id() not in folded}
        if roots:
            folded.update(zip(roots, fold_fixed_terms(roots.values(), bindings), strict=True))

        def fold(term):
            return folded[term.get_id()]

        view = copy(self)
        view.valuations = self._map_valuations(fold)
        view.lengths = {name: fold(term) for name, term in self.lengths.items()}
        view.release_capacity = {name: fold(term) for name, term in self.release_capacity.items()}
        view.queues = {name: tuple(tuple(fold(term) for term in row) for row in cells)
                       for name, cells in self.queues.items()}
        view.actions = tuple(ProtocolActions(
            {name: fold(term) for name, term in action.offers.items()},
            {name: fold(term) for name, term in action.controls.items()}) for action in self.actions)
        view.resets = {name: tuple(map(fold, terms)) for name, terms in self.resets.items()}
        view.drain_slots = tuple(map(fold, self.drain_slots))
        view.constraints = tuple(map(fold, self.constraints))
        return view

    def rows(self, model):
        import z3

        values = {}

        def read(term):
            key = term.get_id()
            if key not in values:
                value = model.eval(term, model_completion=True)
                values[key] = int(z3.is_true(value)) if z3.is_bool(value) else value.as_long()
            return values[key]

        return list(self._map_valuations(read))


class _QueuePayload:
    """One immutable queue's value function, instantiated at each actual head.

    The default substitutes the original zero-default mux. Direct Analysis
    uses a balanced finite lookup over packed payloads, not a general SMT
    array. Both retain every queue cell and the same out-of-range default.
    The placeholder is absent from every returned drive.
    """

    def __init__(self, endpoint, rows, context, *, compact=False, index_sort=None):
        self.index = z3.FreshConst(z3.IntSort(ctx=context) if index_sort is None else index_sort,
                                   prefix='queue_head')
        if z3.is_bv(self.index) and len(rows) > 1 << self.index.size():
            raise ValueError('queue index carrier must distinguish every allocated row')
        self.zero = tuple(z3.BoolVal(False, ctx=context) if packed_width(f.type) == 1 else
                          z3.BitVecVal(0, packed_width(f.type), ctx=context)
                          for f in endpoint.fields)
        self.length = len(rows)
        self.rows = tuple(rows)
        self.constraints = ()
        self.definitions = ()
        self.word = None
        if compact and self.zero:
            self.widths = tuple(packed_width(f.type) for f in endpoint.fields)
            self._word_parts = {}
            # Align finite selectors to index bits. Padding changes only the
            # value tree: no queue entries or domain assumptions are added.
            self._tree_size = (1 << (max(1, self.length) - 1).bit_length()
                               if z3.is_bv(self.index) else self.length)

            def pack(row):
                terms = tuple(z3.If(v, z3.BitVecVal(1, 1, ctx=context),
                                    z3.BitVecVal(0, 1, ctx=context)) if z3.is_bool(v) else v
                              for v in row)
                return terms[0] if len(terms) == 1 else z3.Concat(*terms)

            def lookup(lo, hi):
                if lo >= self.length:
                    value = zero
                elif hi - lo == 1:
                    value = pack(self.rows[lo])
                else:
                    mid = (lo + hi) // 2
                    value = z3.If(self._before(lo, hi), lookup(lo, mid), lookup(mid, hi))
                self._word_parts[lo, hi] = value
                return value

            zero = pack(self.zero)
            valid = (z3.ULE(self.index, self.length - 1) if z3.is_bv(self.index)
                     else z3.And(self.index >= 0, self.index < self.length))
            self.word = z3.If(valid, lookup(0, self._tree_size), zero) if self.length else zero
            self._zero_word = zero
            self._prefix_words = {0: zero, self.length: self.word}
            self.values = ()
            return
        values = list(self.zero)
        for index, row in reversed(tuple(enumerate(rows))):
            at = self.index == index
            values = [z3.If(at, term, old) for term, old in zip(row, values, strict=True)]
        self.values = tuple(values)

    def _before(self, lo, hi):
        if z3.is_bv(self.index):
            bit = (hi - lo).bit_length() - 2
            return z3.Extract(bit, bit, self.index) == 0
        return self.index < (lo + hi) // 2

    def _prefix_word(self, stop):
        """Reuse full subtrees; only the prefix boundary needs new syntax.

        This is a value function with a zero default outside the prefix, not
        permission to shorten a request. Its caller must establish that the
        actual head cannot reach a later cell. Across increasing prefixes,
        construction adds O(N log N) nodes, not N separately rebuilt tables.
        """
        cached = self._prefix_words.get(stop)
        if cached is not None:
            return cached

        def prune(lo, hi):
            if hi <= stop:
                return self._word_parts[lo, hi]
            mid = (lo + hi) // 2
            if stop <= mid:
                return prune(lo, mid)
            return z3.If(self._before(lo, hi), self._word_parts[lo, mid], prune(mid, hi))

        valid = (z3.ULE(self.index, stop - 1) if z3.is_bv(self.index)
                 else z3.And(self.index >= 0, self.index < stop))
        value = z3.If(valid, prune(0, self._tree_size), self._zero_word)
        self._prefix_words[stop] = value
        return value

    def at(self, head, present, *, head_upper=None):
        """Read every possible head, optionally using a constructor-owned bound.

        ``head_upper`` is supplied only by the interval's guarded increment
        construction below. It is not a sampled head or a workload limit.
        """
        concrete = z3.simplify(head)
        if z3.is_int_value(concrete) or z3.is_bv_value(concrete):
            index = concrete.as_long()
            values = self.rows[index] if 0 <= index < self.length else self.zero
            return tuple(z3.simplify(z3.If(present, value, zero))
                         for value, zero in zip(values, self.zero, strict=True))
        if self.word is not None:
            # Substitute the shared word once, rather than one lookup per
            # field. Extract preserves each field's exact bits and signedness.
            if head_upper is not None and type(head_upper) is not int:
                raise TypeError('a queue head bound is an exact constructor integer')
            template = (self.word if head_upper is None else
                        self._prefix_word(max(0, min(self.length, head_upper + 1))))
            word = z3.substitute(template, (self.index, head))
            high, values = sum(self.widths), []
            for width, zero in zip(self.widths, self.zero, strict=True):
                value = z3.Extract(high - 1, high - width, word)
                high -= width
                values.append(z3.If(present, value == 1 if z3.is_bool(zero) else value, zero))
            return tuple(values)
        return tuple(z3.If(present, z3.substitute(value, (self.index, head)), zero)
                     for value, zero in zip(self.values, self.zero, strict=True))


class _FiniteIndexAlgebra(Z3Algebra):
    """Unsigned internal indices; the exposed history remains integer-valued.

    Queue callers supply initial cursor plus EVERY slot, so increment cannot
    wrap even if an invalid sample were to accept on every step. Adapter callers
    supply the closed carrier's size, also covering mixed-radix divisors and
    validity comparisons. Neither use adds a premise to the request domain.
    """

    def __init__(self, bound, context):
        super().__init__(context)
        self.width = max(1, bound.bit_length())

    def integer(self, value):
        return z3.BitVecVal(value, self.width, ctx=self.context)

    @staticmethod
    def gt(left, right):
        return z3.UGT(left, right)

    @staticmethod
    def div(left, right):
        return z3.UDiv(left, right)

    @staticmethod
    def mod(left, right):
        return z3.URem(left, right)


class ProtocolIntervalRelation:
    """One shared transition product for explicit clocks and environment actions.

    Queues contain physical packed terms (Boolean for a one-bit field); samples
    contain the actual pre-clock artifact control terms. A source-event caller
    may bind these samples to fresh proxies, then equate them to settled source
    states. Circuit callers can pass their pre-clock frame terms directly.
    """

    def __init__(self, contract, *, context=None, compact_queues=False):
        self.contract, self.context = contract, context
        self.compact_queues = compact_queues
        self.algebra = Z3Algebra(context)
        self.parts = {
            domain: (view, program, adapter_exact_product(program, view))
            for domain, (view, program) in atomic_adapter_programs(contract).items()}
        self.observed_widths = {pin: width for _, _, table in self.parts.values()
                                for pin, width in table.observed_widths.items()}
        self.offered_names = frozenset(e.name for e in contract.inputs())
        self.scheduled_names = frozenset(pin for _, _, table in self.parts.values()
                                        for pin in table.scheduled_names)

    def symbolic_actions(self, steps, *, tag='protocol'):
        """Free offers/ready over the declared domain, not a baseline's choices.

        The caller may add restrictions to these same terms, or share them
        between executions. This allocates no solver and imposes no fairness
        or eventual-completion assumption.
        """
        return tuple(ProtocolActions(
            {name: z3.Bool(f'{tag}_offer_{i}_{name}', ctx=self.context)
             for name in sorted(self.offered_names)},
            {pin: z3.Bool(f'{tag}_control_{i}_{pin}', ctx=self.context)
            for pin in sorted(self.scheduled_names)}) for i in range(steps))

    def reset_choices(self, slots, *, atomic=False, terms=None):
        """Typed reset assertions by clock domain, separate from physical polarity.

        Omission preserves the fixed schedule. An explicit image names every
        domain/slot; clocks stay fixed, and no reset/fairness choice is inferred
        from a replay. Domain owners add prefix/action restrictions separately.
        """
        names = {clock.name for clock in self.contract.clocks}
        if not names and (atomic or any(slot.reset or slot.resets or slot.ticks for slot in slots)):
            raise ValueError('a settled sample has no clock edges or reset events')
        if terms is None:
            terms = {name: tuple(name in slot.resets if atomic else slot.reset for slot in slots)
                     for name in names}
        if terms.keys() != names or any(len(row) != len(slots) for row in terms.values()):
            raise ValueError('reset choices need every declared domain and one term per slot')
        context = self.algebra.bool(False).ctx
        rows = {name: tuple(self.algebra.bool(term) if type(term) is bool else term for term in row)
                for name, row in terms.items()}
        if any(not z3.is_bool(term) or term.ctx != context for row in rows.values() for term in row):
            raise TypeError('reset choices must be Boolean terms in the relation context')
        return rows

    def encode(self, boundary, queues, slots, samples, *, atomic=False,
               actions=None, queue_lengths=None, reset_terms=None, enabled=None, sampled=None, check=None):
        return finish_construction(self.encode_steps(boundary, queues, slots, samples,
            atomic=atomic, actions=actions, queue_lengths=queue_lengths,
            reset_terms=reset_terms, enabled=enabled, sampled=sampled), check)

    def encode_steps(self, boundary, queues, slots, samples, *, atomic=False,
                     actions=None, queue_lengths=None, reset_terms=None, enabled=None, sampled=None,
                     normalize=False):
        """Continue the explicit prefix; reset never reloads input queues.

        Inactive domains elapse without sampling/resetting their adapters, and
        retain their last driven controls. Framing follows the shared declared
        input-domain transition, including off-edge reset. Terminal source
        prefixes are disabled, not resumed by a later scheduled reset.
        Without ``actions``, preserve the slots' fixed offers/controls. With
        them, only those environment choices change; ticks stay fixed. Optional
        ``reset_terms`` supplies shared symbolic domain assertions, not pin levels.
        ``queue_lengths`` selects a prefix of each allocated payload array,
        including the empty queue. Omission preserves fixed-length replay.
        The bound does not require releasing or accepting every queue cell.
        """
        a, contract = self.algebra, self.contract
        slots, samples = tuple(slots), tuple(samples)
        resets = self.reset_choices(slots, atomic=atomic, terms=reset_terms)
        if len(slots) != len(samples):
            raise ValueError('each protocol slot needs its own artifact sample')
        actions = (None,) * len(slots) if actions is None else tuple(actions)
        if len(actions) != len(slots):
            raise ValueError('each protocol slot needs its own environment actions')
        for action in actions:
            if action is not None and (action.offers.keys() != self.offered_names
                                       or action.controls.keys() != self.scheduled_names):
                raise ValueError('protocol actions must name exactly the declared offers and non-reset controls')
        enabled = tuple(a.bool(True) for _ in slots) if enabled is None else tuple(enabled)
        if len(enabled) != len(slots):
            raise ValueError('each protocol sample needs an enable term')
        sampled = enabled if sampled is None else tuple(sampled)
        if len(sampled) != len(slots):
            raise ValueError('each protocol slot needs a sample-reached term')
        # Adapter products already declare a closed finite state carrier.
        # Include state_count itself for validity comparisons and mixed-radix
        # extraction; do not change their registered transition programs.
        state_algebras = {domain: (_FiniteIndexAlgebra(table.state_count, self.context)
                                  if self.compact_queues else a)
                          for domain, (_, _, table) in self.parts.items()}
        states = {domain: state_algebras[domain].integer(table.state_index(boundary.adapters[domain]))
                  for domain, (_, _, table) in self.parts.items()}
        lengths = ({name: len(rows) for name, rows in queues.items()}
                   if queue_lengths is None else queue_lengths)
        if lengths.keys() != self.offered_names or queues.keys() != self.offered_names:
            raise ValueError('protocol queues and lengths must name every input endpoint')
        pointers = {e.name: a.integer(boundary.queues.accepted[e.name]) for e in contract.inputs()}
        released = {e.name: a.integer(boundary.queues.released[e.name]) for e in contract.inputs()}
        framing = {e.name: tuple(a.integer(n) for n in boundary.framing[e.name])
                   for e in contract.inputs() if e.framing is not None}
        constraints = [z3.Implies(reached, started) for reached, started in zip(sampled, enabled, strict=True)]
        constraint_slots = list(range(len(slots)))
        # Distinct clock domains may share a physical reset pin. They cannot
        # choose incompatible pin levels, even when their polarities differ.
        physical_resets = {}
        for clock in contract.clocks:
            levels = tuple(a.lnot(term) if clock.reset_active_low else term for term in resets[clock.name])
            if clock.reset in physical_resets:
                constraints.extend(left == right for left, right in
                                   zip(physical_resets[clock.reset], levels, strict=True))
                constraint_slots.extend(range(len(slots)))
            physical_resets[clock.reset] = levels
        for e in contract.inputs():
            length = lengths[e.name]
            if type(length) is not int and not z3.is_int(length):
                raise TypeError('a queue length is an integer, not a bit pattern or Boolean')
            # Even a fixed queue bound belongs to the supplied solver context.
            # Python comparisons here would leak plain True/False assertions.
            length = a.integer(length) if type(length) is int else length
            constraints.extend((pointers[e.name] >= 0, released[e.name] >= pointers[e.name],
                                released[e.name] <= length,
                                length >= 0, length <= len(queues[e.name])))
            for index, row in enumerate(queues[e.name]):
                constraints.extend(z3.Implies(length > index, valid_packed_pattern(value, field.type))
                                   for value, field in zip(row, e.fields, strict=True))
        # A prefix-held request cannot be rewritten while continuing the same
        # adapter state. Only queue cells outside that prefix can change.
        for domain, (view, _, table) in self.parts.items():
            for name, payload in table.held_requests(boundary.adapters[domain]).items():
                row = queues[name][boundary.queues.accepted[name]]
                endpoint = next(e for e in view.inputs() if e.name == name)
                for term, value, field in zip(row, payload, endpoint.fields, strict=True):
                    bits = pack_value(field.type, value)
                    constraints.append(term == (bool(bits) if z3.is_bool(term) else bits))
        controls = {pin: a.bool(value) for pin, value in boundary.controls.items()}
        constraint_slots.extend([-1] * (len(constraints) - len(constraint_slots)))
        # Keep original integer boundary/length constraints above. This is a
        # change of internal carrier, not a truncation of the input domain.
        count = a
        internal_lengths = lengths
        if self.compact_queues:
            bound = max((0, *boundary.queues.accepted.values(), *boundary.queues.released.values(),
                         *(len(row) for row in queues.values()))) + len(slots)
            count = _FiniteIndexAlgebra(bound, self.context)
            pointers = {n: count.integer(boundary.queues.accepted[n]) for n in pointers}
            released = {n: count.integer(boundary.queues.released[n]) for n in released}
            internal_lengths = {n: count.integer(value) if type(value) is int else z3.Int2BV(value, count.width)
                                for n, value in lengths.items()}

        def expose(values):
            return {n: z3.BV2Int(value) if z3.is_bv(value) else value for n, value in values.items()}

        payload_lookup = {e.name: _QueuePayload(e, queues[e.name], self.context, compact=self.compact_queues,
                          index_sort=pointers[e.name].sort())
                          for e in contract.inputs()}
        table_constraints = tuple(term for lookup in payload_lookup.values() for term in lookup.constraints)
        constraints.extend(table_constraints)
        constraint_slots.extend([-1] * len(table_constraints))
        table_definitions = tuple(pair for lookup in payload_lookup.values() for pair in lookup.definitions)
        drives, accepted, emitted, hits, definitions = [], [], [], [], []
        state_rows, pointer_rows, release_rows, frame_rows = [expose(states)], [expose(pointers)], [expose(released)], [framing]
        for index, (slot, sample, action, alive, reached_sample) in enumerate(zip(
                slots, samples, actions, enabled, sampled, strict=True)):
            yield
            first_constraint = len(constraints)
            active = set(slot.ticks) if atomic else set(self.parts)
            reset = {name: row[index] for name, row in resets.items()}
            if not contract.clocks:
                reset[None] = a.bool(False)
            present, after_release, payloads = {}, {}, {}
            for e in contract.inputs():
                table = self.parts[e.clock][2]
                awake = a.land(e.clock in active, a.lnot(reset[e.clock]))
                offered = a.bool(e.name in slot.offers) if action is None else action.offers[e.name]
                after_release[e.name] = a.ite(alive, input_release_step(
                    released[e.name], internal_lengths[e.name], a.land(awake, offered), count),
                    released[e.name])
                present[e.name] = input_is_present(pointers[e.name], after_release[e.name],
                    a.land(alive, awake, table.presentation_allowed(
                        states[e.clock], e.name, state_algebras[e.clock])), count)
                # The cursor starts at this concrete boundary and each prior
                # slot adds either zero or one. The finite carrier above
                # covers even unconditional acceptance, so there is no wrap.
                # This structural bound holds for arbitrary handshakes,
                # resets, disabled slots and domain schedules. Retain the
                # entire queue/request; omit only cells unreadable this early.
                payloads[e.name] = payload_lookup[e.name].at(pointers[e.name], present[e.name],
                    head_upper=boundary.queues.accepted[e.name] + index if self.compact_queues else None)
            scheduled = {pin: a.bool(value) for pin, value in slot.controls}
            if action is not None:
                scheduled.update(action.controls)
            if reset_terms is not None:
                scheduled.update({clock.reset: a.lnot(reset[clock.name]) if clock.reset_active_low
                                  else reset[clock.name] for clock in contract.clocks})
            drive = {**controls, **scheduled}
            fired = {e.name: a.bool(False) for e in contract.inputs()}
            out = {e.name: a.bool(False) for e in contract.outputs()}
            after, reached = {}, {}
            for domain, (_, _, table) in self.parts.items():
                if domain not in active:
                    valid, successor = table.symbolic_elapse(states[domain], state_algebras[domain])
                else:
                    enc = table.symbolic(states[domain], reset[domain],
                        {n: present[n] for n in table.presented_names},
                        {n: scheduled[n] for n in table.scheduled_names},
                        {n: sample[n] for n in table.observed_names}, state_algebras[domain])
                    valid, successor = enc.valid, enc.state
                    drive.update(enc.drive)
                    fired.update({n: a.land(reached_sample, term) for n, term in enc.accepted.items()})
                    out.update({n: a.land(reached_sample, term) for n, term in enc.emitted.items()})
                    for goal, term in enc.hits.items():
                        reached[goal] = a.lor(reached.get(goal, a.bool(False)), a.land(reached_sample, term))
                constraints.append(z3.Implies(reached_sample, valid))
                after[domain] = a.ite(reached_sample, successor, states[domain])
            for e in contract.inputs():
                drive.update((f.port, value) for f, value in zip(e.fields, payloads[e.name], strict=True))
            next_frames = {}
            for e in contract.inputs():
                if e.framing is None:
                    continue
                markers = {f.name: a.ite(value, a.integer(1), a.integer(0)) if z3.is_bool(value) else value
                           for f, value in zip(e.fields, payloads[e.name], strict=True)
                           if f.name in e.framing.fields}
                next_frame, legal = e.framing.step(framing[e.name], markers, a)
                constraints.append(a.lor(reset[e.clock], a.lnot(fired[e.name]), legal))
                next_frames[e.name] = tuple(a.ite(reached_sample,
                    a.ite(reset[e.clock], a.integer(0), a.ite(fired[e.name], new, old)), old)
                    for old, new in zip(framing[e.name], next_frame, strict=True))
            pointers = {n: a.ite(fired[n], pointer+1, pointer) for n, pointer in pointers.items()}
            states, released, framing = after, after_release, next_frames
            controls = {pin: drive[pin] for pin in boundary.controls}
            values = (states, pointers, released, framing, controls)
            if normalize:
                from .monitor_template import _map_terms
                values = _map_terms(values, z3.simplify)
                drive = {pin: z3.simplify(value) for pin, value in drive.items()}
            (states, pointers, released, framing, controls), frame = define_monitor_state(
                values, prefix='protocol_state')
            definitions.append(tuple(frame))
            constraints.extend(frame)
            drives.append(drive)
            accepted.append(fired)
            emitted.append(out)
            hits.append(reached)
            state_rows.append(expose(states))
            pointer_rows.append(expose(pointers))
            release_rows.append(expose(released))
            frame_rows.append(framing)
            constraint_slots.extend([index] * (len(constraints) - first_constraint))
        return ProtocolIntervalEncoding(tuple(constraints), tuple(drives), tuple(accepted),
            tuple(emitted), tuple(hits), tuple(state_rows), tuple(pointer_rows),
            tuple(release_rows), tuple(frame_rows), tuple(constraint_slots), tuple(definitions), table_definitions)
