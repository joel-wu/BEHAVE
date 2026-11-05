"""A source request ranges over exactly the existing codec's finite domain."""

from itertools import product

import pytest
import z3

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.core.input_queue import input_completion_environment
from behave.sim.rtl.atomic_request import AtomicRequestCodec
from behave.sim.rtl.initial_request import InitialStateCoordinates
from behave.sim.rtl.task_request import ReadyValidRequestCodec

pytestmark = pytest.mark.solver


def _contract(active_low=False, value_type=None):
    return api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', value_type or api.FiniteType(1), 'a')],
                     api.ready_valid('in', 'iv', 'ir'), 'c', kind='transaction'),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(1), 'y')],
                     api.ready_valid('out', 'ov', 'ordy'), 'c', kind='transaction')],
        [api.ClockDomain('c', 'clk', 'rst', reset_active_low=active_low)])


def _domain(codec, steps, context=None):
    from behave.sim.rtl.protocol_relation import SymbolicRequest

    return SymbolicRequest(codec, steps, context=context)


@pytest.mark.parametrize('reset_hold', [0, 2, 7])
@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('active_low', [False, True])
def test_only_domain_fixed_ready_valid_controls_are_construction_literals(
        reset_hold, private, active_low):
    codec = ReadyValidRequestCodec(_contract(active_low), 2, reset_hold)
    request = _domain(codec, 6, z3.Context() if private else None)
    solver = z3.Solver(ctx=request.context)
    solver.add(*request.constraints)
    differences = []
    for index, (row, action) in enumerate(zip(request.valuations, request.actions, strict=True)):
        assert set(row) == set(codec.bounds)
        # Retain the original physical coordinates even when their use is folded.
        assert all(z3.is_const(row[name]) and not (z3.is_true(row[name]) or z3.is_false(row[name]))
                   for name in (codec.reset, codec.ready, codec.offer))
        reset = request.resets['c'][index]
        differences.extend((reset != (z3.Not(row[codec.reset]) if active_low else row[codec.reset]),
                            action.offers['in'] != row[codec.offer],
                            action.controls[codec.ready] != row[codec.ready]))
        if index <= reset_hold:
            assert z3.is_true(reset) == (index < reset_hold)
            assert z3.is_false(reset) == (index == reset_hold)
        else:
            assert not (z3.is_true(reset) or z3.is_false(reset))
        if index < reset_hold:
            assert z3.is_false(action.offers['in'])
        else:
            assert action.offers['in'].eq(row[codec.offer])
        assert action.controls[codec.ready].eq(row[codec.ready])
    solver.push()
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat
    solver.pop()
    # No inferred fairness or one-shot reset: later resets, offers and ready
    # retain both legal values. Empty queues isolate these environment choices.
    solver.add(request.lengths['in'] == 0)
    for index in range(reset_hold + 1, len(request.actions)):
        for reset, offer, ready in product((False, True), repeat=3):
            solver.push()
            solver.add(request.resets['c'][index] == reset,
                       request.actions[index].offers['in'] == offer,
                       request.actions[index].controls[codec.ready] == ready)
            assert solver.check() == (z3.unsat if reset and offer else z3.sat)
            solver.pop()


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('active_low', [False, True])
def test_atomic_calendar_controls_are_literals_without_losing_public_coordinates(private, active_low):
    contract = _contract(active_low)
    slots = [api.EventSlot(ticks=('c',), offers=('in',) if index == 1 else (),
                          resets=('c',) if index == 0 else (),
                          controls={'rst': int((index == 0) != active_low), 'ordy': int(index > 0)})
             for index in range(4)]
    codec = AtomicRequestCodec(contract, 2, _schedule(contract, slots))
    request = _domain(codec, len(slots), z3.Context() if private else None)
    differences = []
    for index, (row, action) in enumerate(zip(request.valuations, request.actions, strict=True)):
        for name, value in ((codec.offers['in'], action.offers['in']),
                            ('ordy', action.controls['ordy'])):
            assert z3.is_true(value) or z3.is_false(value)
            differences.append(value != row[name])
        reset = request.resets['c'][index]
        assert z3.is_true(reset) == (index == 0)
        assert z3.is_false(reset) == (index != 0)
        differences.append(reset != (z3.Not(row['rst']) if active_low else row['rst']))
    solver = z3.Solver(ctx=request.context)
    solver.add(*request.constraints, z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('offers', [1, 3])
@pytest.mark.parametrize('private', [False, True])
def test_full_queue_hint_uses_calendar_capacity_not_physical_queue_size(offers, private):
    slots = [GlobalEventSlot(0, event=api.EventSlot(
        controls={'rst': 1, 'ordy': 0}, ticks=('c',), resets=('c',)))]
    slots.extend(GlobalEventSlot(i + 1, event=api.EventSlot(
        controls={'rst': 0, 'ordy': 1}, ticks=('c',), offers=('in',))) for i in range(offers))
    codec = AtomicRequestCodec(_contract(), 2, GlobalSchedule(
        (ClockTiming('c', 1, clock='clk', reset='rst'),), tuple(slots)))
    request = _domain(codec, len(slots), z3.Context() if private else None)
    original = request.constraints
    solver = z3.Solver(ctx=request.context)
    solver.add(*original, *request.full_queue_hint())
    assert solver.check() == z3.sat
    assert solver.model().eval(request.lengths['in']).as_long() == min(offers, 2)
    fixed = request.fixed(request.rows(solver.model()))
    assert all(z3.is_true(z3.simplify(term)) for term in fixed.full_queue_hint())
    assert request.constraints is original
    # The existential hint never excludes the original empty request.
    solver = z3.Solver(ctx=request.context)
    solver.add(*original, request.lengths['in'] == 0)
    assert solver.check() == z3.sat


@pytest.mark.parametrize('private', [False, True])
def test_shared_coordinates_are_bound_and_read_once_without_losing_unused_cells(private):
    from collections import Counter
    from types import SimpleNamespace

    codec = ReadyValidRequestCodec(_contract(), 4, 1)
    request = _domain(codec, 7, z3.Context() if private else None)
    solver = z3.Solver(ctx=request.context)
    solver.add(*request.constraints, request.lengths['in'] == 0)
    assert solver.check() == z3.sat
    model, calls = solver.model(), Counter()

    def evaluate(term, **kwargs):
        calls[term.get_id()] += 1
        return model.eval(term, **kwargs)

    rows = request.rows(SimpleNamespace(eval=evaluate))
    assert set(calls.values()) == {1}
    full = [term == (bool(row[name]) if z3.is_bool(term) else row[name])
            for terms, row in zip(request.valuations, rows, strict=True) for name, term in terms.items()]
    fixed = request.fix(rows)
    assert len(fixed) == len(calls) < len(full)
    solver.push()
    solver.add(z3.Xor(z3.And(*full), z3.And(*fixed)))
    assert solver.check() == z3.unsat
    solver.pop()
    # No queued transactions, but even the last unused cell is still fixed.
    coordinate = codec.cells[-1][0]
    changed = [{**row, coordinate: 1 - row[coordinate]} for row in rows]
    solver.push()
    solver.add(*fixed, *request.fix(changed))
    assert solver.check() == z3.unsat
    solver.pop()
    inconsistent = [dict(row) for row in rows]
    inconsistent[-1][coordinate] = 1 - rows[-1][coordinate]
    with pytest.raises(api.Refused):
        request.fix(inconsistent)


@pytest.mark.parametrize('queue_size,slots', [(2, 4), (16, 4), (16, 40)])
@pytest.mark.parametrize('private', [False, True])
def test_shared_coordinate_solver_visits_do_not_multiply_by_slots(
        monkeypatch, queue_size, slots, private):
    from collections import Counter

    codec = ReadyValidRequestCodec(_contract(), queue_size, 1)
    request = _domain(codec, slots, z3.Context() if private else None)
    solver = z3.Solver(ctx=request.context)
    solver.add(*request.constraints, request.lengths['in'] == 0)
    assert solver.check() == z3.sat
    model = solver.model()
    rows = request.rows(model)
    shared = {id(term) for name, term in request.valuations[0].items()
              if name not in (codec.offer, codec.ready, codec.reset)}
    get_id, calls = z3.ExprRef.get_id, Counter()

    def counted(term):
        if id(term) in shared:
            calls[id(term)] += 1
        return get_id(term)

    monkeypatch.setattr(z3.ExprRef, 'get_id', counted)
    request.fix(rows)
    assert set(calls) == shared and set(calls.values()) == {1}
    calls.clear()
    assert request.rows(model) == rows
    assert set(calls) == shared and set(calls.values()) == {1}
    calls.clear()
    assert request.fixed(rows).rows(model) == rows
    # fix, shared row folding, and queue/length folding each visit the shared
    # coordinate once. No slot-dependent repeated solver-ID traversal remains.
    assert set(calls) == shared and set(calls.values()) == {3}


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('active_low', [False, True])
@pytest.mark.parametrize('atomic', [False, True])
def test_fixed_request_batches_all_derived_roots_without_changing_values(
        private, active_low, atomic, monkeypatch):
    contract = _contract(active_low, api.FiniteType(17))
    initial = InitialStateCoordinates('a'*64, {'q': 17}, startup_pins={'clk': 1, 'a': 17})
    if atomic:
        slots = [api.EventSlot(ticks=('c',), offers=('in',) if i == 1 else (),
                              resets=('c',) if i == 0 else (),
                              controls={'rst': int((i == 0) != active_low), 'ordy': 1})
                 for i in range(6)]
        codec = AtomicRequestCodec(contract, 3, _schedule(contract, slots), initial=initial)
    else:
        codec = ReadyValidRequestCodec(contract, 3, 1, initial=initial)
    request = _domain(codec, 6, z3.Context() if private else None)
    solver = z3.Solver(ctx=request.context)
    solver.add(*request.constraints, request.lengths['in'] == 1)
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    # A false constraint and duplicates must survive the temporary AST pack.
    request.constraints += (z3.BoolVal(False, ctx=request.context), request.constraints[0])
    original = request.constraints
    pairs = tuple(tuple(eq.children()) for eq in request.fix(rows))
    substitute, calls = z3.substitute, []

    def counted(term, *bindings):
        calls.append(len(bindings))
        return substitute(term, *bindings)

    monkeypatch.setattr(z3, 'substitute', counted)
    fixed = request.fixed(rows)
    assert calls == [len(pairs)]
    assert request.constraints is original
    assert z3.is_false(fixed.constraints[-2])
    assert len(fixed.constraints) == len(original)

    def terms(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from terms(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                yield from terms(item)
        elif z3.is_expr(value):
            yield value
        else:  # ProtocolActions retains both offers and controls.
            yield from terms(value.offers)
            yield from terms(value.controls)

    for name in ('valuations', 'lengths', 'release_capacity', 'queues',
                 'actions', 'resets', 'drain_slots', 'constraints'):
        for before, after in zip(terms(getattr(request, name)), terms(getattr(fixed, name)), strict=True):
            expected = z3.simplify(substitute(before, *pairs))
            assert z3.is_true(z3.simplify(after == expected)), name


@pytest.mark.parametrize('active_low', [False, True])
@pytest.mark.parametrize('private', [False, True])
def test_free_request_language_equals_codec_language_exhaustively(active_low, private):
    codec = ReadyValidRequestCodec(_contract(active_low), 1, 1)
    context = z3.Context() if private else None
    request = _domain(codec, 3, context)
    solver = z3.Solver(ctx=context)
    solver.add(*request.constraints)
    expected = set()
    for length, payload, *actions in product((0, 1), repeat=11):
        rows = [{codec.length: length, codec.cells[0][0]: payload,
                 codec.offer: actions[3*i], codec.ready: actions[3*i+1],
                 codec.reset: actions[3*i+2]} for i in range(3)]
        try:
            codec.decode(rows)
        except api.Refused:
            continue
        expected.add(tuple(tuple(sorted(row.items())) for row in rows))
    found = set()
    while solver.check() == z3.sat:
        rows = request.rows(solver.model())
        stimulus = codec.decode(rows)
        drains = tuple(i for i, term in enumerate(request.drain_slots)
                       if z3.is_true(solver.model().eval(term)))
        assert drains == input_completion_environment(stimulus, codec.contract).drain_slots
        key = tuple(tuple(sorted(row.items())) for row in rows)
        assert key not in found
        found.add(key)
        solver.add(z3.Not(z3.And(*request.fix(rows))))
    assert found == expected
    assert any(rows[-1][codec.reset] == int(not active_low)
               for key in found for rows in [[dict(row) for row in key]])


def _schedule(contract, slots):
    return GlobalSchedule([ClockTiming(c.name, 2, clock=c.clock, reset=c.reset,
        reset_active_low=c.reset_active_low, edge=c.edge) for c in contract.clocks],
        [GlobalEventSlot(i*2, event=slot) for i, slot in enumerate(slots)])


def test_fixed_schedule_drain_depends_on_nonempty_queues_not_all_offers():
    endpoints = []
    for name in ('a', 'b'):
        endpoints.extend((
            api.Endpoint('in_'+name, 'in', [api.Field('x', api.FiniteType(1), 'x_'+name)],
                         api.ready_valid('in', 'iv_'+name, 'ir_'+name), name, kind='transaction'),
            api.Endpoint('out_'+name, 'out', [api.Field('y', api.FiniteType(1), 'y_'+name)],
                         api.ready_valid('out', 'ov_'+name, 'r_'+name), name, kind='transaction')))
    contract = api.TaskContract(endpoints, [api.ClockDomain(n, 'clk_'+n, 'rst_'+n) for n in ('a', 'b')])
    # A later offer to an EMPTY b queue cannot conceal a non-receptive drain
    # after a's last offer. Making b nonempty genuinely moves the last offer.
    slots = [api.EventSlot(ticks=('a', 'b'), offers=offers,
                          controls={'rst_a': 0, 'rst_b': 0, 'r_a': ready, 'r_b': ready})
             for offers, ready in [(('in_a',), 1), ((), 0), (('in_b',), 1), ((), 1)]]
    codec = AtomicRequestCodec(contract, 1, _schedule(contract, slots))
    request = _domain(codec, 4, z3.Context())
    solver = z3.Solver(ctx=request.context)
    solver.add(*request.constraints)
    for a, b in product((0, 1), repeat=2):
        solver.push()
        solver.add(request.lengths['in_a'] == a, request.lengths['in_b'] == b)
        answer = solver.check()
        assert answer == (z3.unsat if (a, b) == (1, 0) else z3.sat)
        if answer == z3.sat:
            codec.decode(request.rows(solver.model()))
        solver.pop()


@pytest.mark.parametrize('builder_index', range(9))
def test_registered_protocols_share_fixed_request_domain(builder_index):
    from .test_protocol_interval_relation import BUILDERS

    contract = BUILDERS[builder_index]()
    names = tuple(c.name for c in contract.clocks)
    slots = [api.EventSlot(ticks=names, resets=names if i == 0 else (),
                          offers=tuple(e.name for e in contract.inputs()) if i == 1 else (),
                          controls={**dict.fromkeys(contract.scheduled_controls(), 1),
                                    **{c.reset: int((i == 0) != c.reset_active_low) for c in contract.clocks}})
             for i in range(4)]
    codec = AtomicRequestCodec(contract, 2, _schedule(contract, slots))
    request = _domain(codec, 4)
    solver = z3.Solver()
    solver.add(*request.constraints, *(length == 1 for length in request.lengths.values()))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    stimulus = codec.decode(rows)
    assert tuple(i for i, term in enumerate(request.drain_slots)
                 if z3.is_true(solver.model().eval(term))) == input_completion_environment(
                     stimulus, contract).drain_slots
    assert dict(stimulus.queues).keys() == request.queues.keys()
    assert all(dict(slot.controls) == dict(original.controls)
               for slot, original in zip(stimulus.slots, slots, strict=True))


@pytest.mark.parametrize('private', [False, True])
def test_initial_coordinates_are_fixed_physical_terms_and_bind_whole_image(private):
    context = z3.Context() if private else None
    initial = InitialStateCoordinates('a'*64, {'q': 3}, startup_pins={'clk': 1, 'a': 1})
    codec = ReadyValidRequestCodec(_contract(), 1, 0, initial=initial)
    request = _domain(codec, 2, context)
    terms = {coordinate: z3.BitVec(coordinate, width, ctx=context)
             for coordinate, _, width in (*initial.coordinates, *initial.pin_coordinates)}
    solver = z3.Solver(ctx=context)
    solver.add(*request.constraints, *request.bind_initial(terms), terms['initial.rtl.q'] == 7)
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    assert all(row['initial.rtl.q'] == 7 for row in rows)
    assert initial.decode(rows).registers.values()['q'] == 7
    for bad in ({}, {**terms, 'extra': terms['initial.rtl.q']},
                {**terms, 'initial.rtl.q': z3.BitVec('wrong_width', 4, ctx=context)},
                {**terms, 'initial.rtl.q': z3.BitVec('wrong_context', 3, ctx=z3.Context())}):
        with pytest.raises((TypeError, ValueError)):
            request.bind_initial(bad)


@pytest.mark.parametrize('steps', [0, -1, True, 1.5])
def test_horizon_is_an_exact_positive_integer(steps):
    with pytest.raises((TypeError, ValueError)):
        _domain(ReadyValidRequestCodec(_contract(), 1, 0), steps)


def test_fixed_schedule_horizon_cannot_be_silently_truncated():
    contract = _contract()
    codec = AtomicRequestCodec(contract, 1, _schedule(contract, [api.EventSlot(
        ticks=('c',), controls={'rst': 0, 'ordy': 1})]))
    with pytest.raises(ValueError):
        _domain(codec, 2)


@pytest.mark.parametrize('recursive', [False, True])
@pytest.mark.parametrize('used', [False, True])
def test_unused_enum_bits_do_not_narrow_the_request_or_protocol_domain(recursive, used):
    from behave.sim.core.physical_value import packed_width
    from behave.sim.rtl.protocol_relation import ProtocolBoundary, ProtocolIntervalRelation

    kind = api.ValueType.enumeration(('a', 'b', 'c'))
    if recursive:
        kind = api.ValueType.record({'items': api.ValueType.array(2, kind)})
    contract = _contract(value_type=kind)
    codec = ReadyValidRequestCodec(contract, 1, 0)
    request = _domain(codec, 1)
    invalid = (1 << packed_width(kind)) - 1
    raw = [{codec.length: int(used), codec.cells[0][0]: invalid,
            codec.offer: int(used), codec.reset: 0, codec.ready: 1}]
    if used:
        with pytest.raises(api.Refused):
            codec.decode(raw)
    else:
        assert codec.decode(raw).depth('in') == 0
    protocol = ProtocolIntervalRelation(contract)
    encoded = protocol.encode(ProtocolBoundary.initial(contract), request.queues,
        request.slots, [{name: z3.BoolVal(False) for name in protocol.observed_widths}],
        actions=request.actions, reset_terms=request.resets, queue_lengths=request.lengths)
    solver = z3.Solver()
    solver.add(*request.constraints, *encoded.constraints,
               request.lengths['in'] == int(used), request.queues['in'][0][0] == invalid)
    assert solver.check() == (z3.unsat if used else z3.sat)


def test_request_construction_uses_callers_cancellation():
    from behave.sim.rtl.protocol_relation import SymbolicRequest

    def stop():
        raise InterruptedError('caller deadline')

    with pytest.raises(InterruptedError, match='caller deadline'):
        SymbolicRequest(ReadyValidRequestCodec(_contract(), 2, 1), 4, check=stop)


def test_concrete_request_codecs_do_not_import_a_solver():
    import subprocess
    import sys

    subprocess.run([sys.executable, '-c', '''import sys
from behave.sim.rtl.task_request import ReadyValidRequestCodec
from behave.sim.rtl.atomic_request import AtomicRequestCodec
assert 'z3' not in sys.modules
'''], check=True, capture_output=True, timeout=30)
