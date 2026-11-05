"""Clockless requests preserve plain-pin samples, without clock/reset choices."""

from itertools import product

import pytest
import z3

from behave.sim import api
from behave.sim.rtl.protocol_relation import SymbolicRequest
from behave.sim.rtl.settled_request import SettledRequestCodec

pytestmark = pytest.mark.solver


def _contract(value_type=None, *, inputs=1):
    return api.TaskContract([
        *(api.Endpoint(f'in{i}', 'in', [api.Field('x', value_type or api.FiniteType(1), f'x{i}')],
                       api.settled('in'), None, kind='transaction') for i in range(inputs)),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(1), 'y')],
                     api.settled('out'), None, kind='transaction', answers_per_transaction=1)], [])


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('inputs', [1, 2])
def test_settled_domain_is_exactly_the_sampled_queues(private, inputs):
    codec = SettledRequestCodec(_contract(inputs=inputs), 2)
    context = z3.Context() if private else None
    request = SymbolicRequest(codec, 2, context=context)
    solver = z3.Solver(ctx=context)
    solver.add(*request.constraints)
    assert request.settled and not request.atomic
    assert request.resets == {} and request.times is None
    assert all(z3.is_true(term) for action in request.actions for term in action.offers.values())
    for values in product((0, 1), repeat=2 * inputs):
        queues = {f'in{i}': [(value,) for value in values[2*i:2*i+2]] for i in range(inputs)}
        stimulus = api.Stimulus(queues=queues, slots=codec.slots(2))
        rows = codec.encode(stimulus)
        decoded = codec.decode(rows)
        assert dict(decoded.queues) == dict(stimulus.queues)
        assert decoded.clock_schedule is None
        assert all(not s.ticks and not s.reset and not s.resets and not s.controls for s in decoded.slots)
        solver.push()
        solver.add(*request.fix(rows))
        assert solver.check() == z3.sat
        assert request.rows(solver.model()) == rows
        assert request.fixed(rows).rows(solver.model()) == rows
        assert all(z3.is_false(solver.model().eval(term)) for term in request.drain_slots)
        solver.pop()
    # Exhaust the finite domain: no extra schedules, queue lengths or actions.
    found = 0
    while solver.check() == z3.sat:
        rows = request.rows(solver.model())
        codec.decode(rows)
        solver.add(z3.Not(z3.And(*request.fix(rows))))
        found += 1
    assert found == 2 ** (2 * inputs)


@pytest.mark.parametrize('recursive', [False, True])
def test_used_enumerations_are_valid_but_unused_cells_keep_all_bits(recursive):
    kind = api.ValueType.enumeration(('a', 'b', 'c'))
    if recursive:
        kind = api.ValueType.record({'items': api.ValueType.array(2, kind)})
    codec = SettledRequestCodec(_contract(kind), 2)
    request = SymbolicRequest(codec, 1)
    solver = z3.Solver()
    solver.add(*request.constraints)
    name = codec.cells['in0'][1][0]
    solver.add(request.valuations[0][name] == codec.bounds[name][1])
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    codec.decode(rows)
    used = codec.cells['in0'][0][0]
    solver.add(request.valuations[0][used] == codec.bounds[used][1])
    assert solver.check() == z3.unsat
    with pytest.raises(api.Refused):
        codec.decode([{**rows[0], used: codec.bounds[used][1]}])


@pytest.mark.parametrize('bad', ['reset', 'idle', 'short', 'changed', 'bool', 'shape', 'range'])
def test_clockless_codec_rejects_changed_topology_and_invalid_rows(bad):
    codec = SettledRequestCodec(_contract(), 2)
    stimulus = api.Stimulus(queues={'in0': [(0,), (1,)]}, slots=codec.slots(2))
    rows = codec.encode(stimulus)
    if bad in ('reset', 'idle'):
        slots = (api.EventSlot(offers=('in0',), reset=True) if bad == 'reset' else api.EventSlot(),) * 2
        with pytest.raises(api.Refused):
            codec.encode(api.Stimulus(queues=dict(stimulus.queues), slots=slots))
        return
    if bad == 'short':
        rows = rows[:1]
    elif bad == 'changed':
        rows[-1][codec.cells['in0'][0][0]] = 1
    elif bad == 'bool':
        rows[0][codec.offers['in0']] = True
    elif bad == 'shape':
        rows[0]['invented.clock'] = 1
    elif bad == 'range':
        rows[0][codec.cells['in0'][0][0]] = 2
    with pytest.raises(api.Refused):
        codec.decode(rows)


def test_more_samples_than_queue_capacity_is_not_a_weaker_request():
    request = SymbolicRequest(SettledRequestCodec(_contract(), 1), 2)
    solver = z3.Solver()
    solver.add(*request.constraints)
    assert solver.check() == z3.unsat


def test_settled_monitor_goals_have_a_shared_exact_product():
    from behave.sim.core.monitor import Monitor, _protocol_goals_have_exact_product

    assert _protocol_goals_have_exact_product(Monitor(_contract()))


@pytest.mark.parametrize('compact', [False, True])
@pytest.mark.parametrize('private', [False, True])
def test_settled_protocol_consumes_each_sample_without_a_clock(compact, private):
    from behave.sim.rtl.protocol_relation import ProtocolBoundary, ProtocolIntervalRelation

    contract = _contract(inputs=2)
    context = z3.Context() if private else None
    request = SymbolicRequest(SettledRequestCodec(contract, 3), 3, context=context)
    relation = ProtocolIntervalRelation(contract, context=context, compact_queues=compact)
    assert set(relation.parts) == {None}
    _, program, table = relation.parts[None]
    assert table.drive_names == () and table.scheduled_names == ()
    encoding = relation.encode(ProtocolBoundary.initial(contract), request.queues,
        request.slots, ({},) * 3, actions=request.actions, queue_lengths=request.lengths,
        reset_terms=request.resets)
    solver = z3.Solver(ctx=context)
    solver.add(*request.constraints, *encoding.constraints)
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    stimulus = request.codec.decode(rows)
    queues = dict(stimulus.queues)
    # The symbolic relation must agree with the original concrete adapter.
    state = program.initial_state(contract)
    differences = []
    for i, slot in enumerate(stimulus.slots):
        payloads = {name: queue[i] for name, queue in queues.items()}
        drive = program.drive(contract, state, slot, payloads, {})
        transition = program.step(contract, state, slot, drive, {'y': i % 2})
        state = transition.state
        for endpoint in contract.inputs():
            name = endpoint.name
            differences.extend((encoding.accepted[i][name] != True,
                                encoding.pointers[i+1][name] != i+1,
                                encoding.drives[i][endpoint.fields[0].port] != request.queues[name][i][0]))
            assert transition.accepted_inputs[name] == payloads[name]
        assert transition.emitted_outputs['out'] == (i % 2,)
        differences.append(encoding.emitted[i]['out'] != True)
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat
    # A reset is not a latent clockless degree of freedom in the exact table.
    reset = table.symbolic(z3.IntVal(0, ctx=context), z3.BoolVal(True, ctx=context),
        dict.fromkeys(table.presented_names, z3.BoolVal(True, ctx=context)), {}, {}, relation.algebra)
    assert z3.is_false(z3.simplify(reset.valid))


@pytest.mark.parametrize('bad', ['clock', 'reset', 'reset_term', 'global'])
def test_settled_protocol_cannot_smuggle_clock_or_reset_choices(bad):
    from behave.sim.rtl.protocol_relation import ProtocolIntervalRelation

    relation = ProtocolIntervalRelation(_contract())
    slot = api.EventSlot(offers=('in0',), ticks=('clk',) if bad == 'clock' else (), reset=bad == 'reset')
    with pytest.raises(ValueError):
        relation.reset_choices((slot,), atomic=bad == 'global',
                               terms={None: (True,)} if bad == 'reset_term' else {})


def test_settled_monitor_advances_on_samples_and_preserves_terminal_guards():
    from behave.sim.rtl.atomic_monitors import AtomicMonitorProduct, protocol_monitor_components
    from behave.sim.rtl.protocol_relation import ProtocolIntervalRelation

    relation = ProtocolIntervalRelation(_contract())
    _, components = protocol_monitor_components(relation.contract,
        [(domain, *part) for domain, part in relation.parts.items()], 2)
    assert len(components) == 1 and components[0].domain is None
    product = AtomicMonitorProduct(components, (), {})
    algebra = relation.algebra
    word = lambda value, width: z3.BitVecVal(value, width)
    state = product.initial(algebra, word)
    assert state.image()['monitor.ready-valid'] == {}
    assert set(state.image()['monitor.settled']) == {'out'}
    # No request but a plain output: the shared monitor must flag it, even
    # though there is no physical tick. This catches silently skipped samples.
    _, hits = product.step(state, 0, api.EventSlot(), {'in0': algebra.bool(False)}, {'y': 1}, {},
        algebra=algebra, boolean=algebra.bool, word=word, reset_terms={}, faults={})
    assert z3.is_true(z3.simplify(hits['monitor.output-without-transaction']))
    _, suppressed = product.step(state, 0, api.EventSlot(), {'in0': algebra.bool(False)}, {'y': 1}, {},
        algebra=algebra, boolean=algebra.bool, word=word, sampled=algebra.bool(False), faults={})
    assert all(z3.is_false(z3.simplify(term)) for term in suppressed.values())
