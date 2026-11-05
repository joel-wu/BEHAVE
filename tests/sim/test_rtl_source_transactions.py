"""Declared observation/sequence/latency goals follow actual source transfers."""

from dataclasses import replace
from itertools import product
from types import SimpleNamespace

import pytest
import z3

from behave.sim import api
from behave.sim.core.observation_monitor import TransactionMonitor
from behave.sim.core.observation_product import TransactionProduct
from behave.sim.core.observation_symbolic import history_image
from behave.sim.core.ready_valid_product import ConcreteAlgebra
from behave.sim.core.transaction_monitor import DeclaredTransactionMonitor
from behave.sim.rtl.source_confluence import compare_histories
from behave.sim.rtl.source_task import SourceTaskRelation

from .test_finite_transaction_queries import _sequence
from .test_latency_goals import _rule
from .test_rtl_source_history import HISTORY_SOURCE, _encode, _initial, _simple_contract
from .test_rtl_source_memory_relation import _prepare
from .test_rtl_source_monitors import UNSTABLE, _truth

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


def _contract(*, basis=None, safety=False):
    base = _simple_contract()
    clock_monitor = api.TransactionMonitorSpec('edges', api.FiniteMonitor(
        (0, 1), ((), ('edge',)), 0, ('second',), tuple(
            api.FiniteMonitorTransition(state, event, 1-state if event else state,
                ('second',) if state and event else ())
            for state, event in product((0, 1), ((), ('edge',))))), (), order=(),
        sampling='schedule-entry', schedule_events=(api.ScheduleEvent('edge', 'clock-edge', 'c'),))
    return api.TaskContract(base.endpoints, base.clocks, observation_goals=(
        api.TransactionGoal('zero', 'out', api.GoalCondition('eq', api.GoalField('y'), 0)),
        api.TransactionGoal('pair', 'out', api.GoalCondition('and',
            api.GoalCondition('eq', api.GoalField('y', previous=True), 0),
            api.GoalCondition('eq', api.GoalField('y'), 1))),
        api.TransactionGoal('outside', 'out', api.GoalCondition('eq', api.GoalField('y'), 4))),
        transaction_monitors=(_sequence(0, 1, safety=safety), clock_monitor),
        latency_goals=(_rule(basis=basis or api.LatencyTimebase('clock', 'c'), reset_domains=('c',)),))


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('safety', [False, True])
def test_source_transfers_keep_history_across_silence_and_reset(tmp_path, private_context, safety):
    machine, _, env, template = _prepare(tmp_path, UNSTABLE)
    contract = _contract(safety=safety)
    context = z3.Context() if private_context else None
    binding = TransactionProduct(contract)
    relation = SourceTaskRelation(template, machine, contract, (), context=context, transactions=binding)
    slots = [api.EventSlot(offers=('in',) if i in (0, 3) else (), reset=i==2,
                          controls={'rst':int(i==2), 'ordy':1}) for i in range(4)]
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(0,), (1,)], startup=True)
    solver = z3.Solver(ctx=context)
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    model = solver.model()
    concrete = binding.initial(ConcreteAlgebra)
    records = []
    for i in range(4):
        samples = {name: ((i in (0, 3), (int(i==3),)),) for name in ('in', 'out')}
        concrete, hits = binding.advance(concrete, samples, ConcreteAlgebra, ticks=('c',),
                                         resets={'c':i==2})
        symbolic = history_image(encoded.transaction_states[i+1])
        assert TransactionProduct.image(concrete) == TransactionProduct.image(symbolic, lambda x: _truth(model, x))
        assert hits == {key:_truth(model, value) for key, value in encoded.transaction_hits[i].items()}
        records.append(api.SlotRecord(accepted={name: payload for name, ((fired, payload),) in samples.items()
                                               if fired}))
    stimulus = api.Stimulus(queues={'in':[(0,), (1,)]}, slots=slots)
    trace = SimpleNamespace(records=records, stimulus=stimulus)
    native_scalar = TransactionMonitor(contract, behavior=False).check(stimulus, trace)
    native_finite = DeclaredTransactionMonitor(contract, behavior=False).check(stimulus, trace)
    assert {hit.goal for hit in native_scalar.hits} == {'observation:zero', 'observation:pair'}
    assert 'monitor:chain:hit' in {hit.goal for hit in (*native_finite.hits, *native_finite.violations)}
    solver.add(z3.Or(z3.Not(encoded.hits['observation:pair']),
                     z3.Not(encoded.hits['monitor:chain:hit']), encoded.hits['observation:outside'],
                     z3.Not(encoded.hits['latency:response:zero'])))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('phase', [0, 1, 2])
def test_unfinished_history_cannot_tick_or_reset_declared_monitors(tmp_path, monkeypatch, phase):
    source = HISTORY_SOURCE.replace('endmodule',
        'reg [1:0] tail; always @(negedge clk) tail<=saved; endmodule')
    machine, _, env, template = _prepare(tmp_path, source)
    original, index = type(machine).phase_event_bounds, 0

    def bound(self, *args):
        nonlocal index
        index += 1
        bounds = list(original(self, *args))
        if index == 3:
            bounds[phase] = 1
        return tuple(bounds)

    monkeypatch.setattr(type(machine), 'phase_event_bounds', bound)
    contract = _contract()
    relation = SourceTaskRelation(template, machine, contract, (), transactions=TransactionProduct(contract))
    slots = [api.EventSlot(offers=('in',), reset=i>=2,
                          controls={'rst':int(i>=2), 'ordy':1}) for i in range(5)]
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(0,), (1,), (2,)],
                      startup=True, require_quiescence=False)
    solver = z3.Solver()
    solver.add(*encoded.constraints, encoded.unfinished)
    assert solver.check() == z3.sat
    count = 2 + int(phase > 0)
    for state in encoded.transaction_states[3:]:
        solver.push()
        solver.add(state['latency']['response']['counter'] != count)
        assert solver.check() == z3.unsat
        solver.pop()
    solver.add(z3.Or(*(value for row in encoded.transaction_hits[count:] for value in row.values())))
    assert solver.check() == z3.unsat


def test_unaccepted_output_does_not_become_a_previous_transaction(tmp_path):
    machine, _, env, template = _prepare(tmp_path, UNSTABLE)
    contract = _contract()
    relation = SourceTaskRelation(template, machine, contract, (), transactions=TransactionProduct(contract))
    slots = [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':i}) for i in range(2)]
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(0,), (1,)], startup=True)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Or(encoded.hits['observation:zero'], encoded.hits['observation:pair'],
                     encoded.hits['monitor:chain:hit']))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
def test_signed_transaction_fields_keep_their_logical_value(tmp_path, private_context):
    machine, _, env, template = _prepare(tmp_path, UNSTABLE)
    base = _simple_contract()
    endpoints = [api.Endpoint(e.name, e.direction, [api.Field(e.fields[0].name,
        api.FiniteType(2, signed=True), e.fields[0].port)], e.adapter, e.clock,
        kind=e.kind, answers_per_transaction=e.answers_per_transaction) for e in base.endpoints]
    contract = api.TaskContract(endpoints, base.clocks, observation_goals=(
        api.TransactionGoal('negative', 'out', api.GoalCondition('and',
            api.GoalCondition('eq', api.GoalField('y', previous=True), -1),
            api.GoalCondition('eq', api.GoalField('y'), -2))),))
    context = z3.Context() if private_context else None
    relation = SourceTaskRelation(template, machine, contract, (), context=context,
                                  transactions=TransactionProduct(contract))
    slots = [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})]*2
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(-1,), (-2,)], startup=True)
    solver = z3.Solver(ctx=context)
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Not(encoded.hits['observation:negative']))
    assert solver.check() == z3.unsat


def test_global_latency_uses_declared_time_not_slot_count(tmp_path):
    machine, _, env, template = _prepare(tmp_path, HISTORY_SOURCE)
    contract = _contract(basis=api.LatencyTimebase('global-time'))
    relation = SourceTaskRelation(template, machine, contract, (), transactions=TransactionProduct(contract))
    slots = [api.EventSlot(offers=('in',) if i==0 else (), controls={'rst':0, 'ordy':1}) for i in range(2)]
    with pytest.raises(ValueError, match='timestamped schedule'):
        _encode(relation, _initial(machine, env), contract, slots, [(0,)], startup=True)
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(0,)], startup=True, times=(7, 11))
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Or(encoded.hits['latency:response:one'],
                     z3.Not(encoded.hits['latency:response:later'])))
    assert solver.check() == z3.unsat


def test_confluence_compares_latent_transaction_history_and_continuation_requires_it(tmp_path):
    machine, _, env, template = _prepare(tmp_path, UNSTABLE)
    contract = _contract()
    relation = SourceTaskRelation(template, machine, contract, (), complete_state=True,
                                  transactions=TransactionProduct(contract))
    slots = [api.EventSlot(controls={'rst':0, 'ordy':1})]
    initial = _initial(machine, env)
    encoded = _encode(relation, initial, contract, slots, [], startup=True)
    final = encoded.transaction_states[-1]
    history = {**final, 'observations': {'out': {**final['observations']['out'], 'seen': z3.BoolVal(True)}}}
    changed = replace(encoded, transaction_states=(*encoded.transaction_states[:-1], history))
    audit = compare_histories(encoded, changed, tuple(machine.program.fields))
    solver = z3.Solver()
    solver.add(*audit.constraints, audit.different)
    assert solver.check() == z3.sat
    assert any(name.startswith('transactions.states') and _truth(solver.model(), value)
               for name, value in audit.differences.items())
    with pytest.raises(ValueError, match='transaction boundary'):
        _encode(relation, initial, contract, slots, [])
