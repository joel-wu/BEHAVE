"""Whole-domain reset choices must agree with public requests and native traces."""

from dataclasses import replace
from itertools import product

import pytest
import z3

from behave.sim import api
from behave.sim.core.memory import MemoryEnvironment, MemoryEnvironmentState, MemoryRun
from behave.sim.core.observation_product import TransactionProduct
from behave.sim.core.record_value import thaw_record_value
from behave.sim.core.reset_monitor import ResetRestorationProvider
from behave.sim.rtl.initial_request import InitialStateCoordinates
from behave.sim.rtl.protocol_relation import ProtocolBoundary, ProtocolIntervalRelation
from behave.sim.rtl.source_confluence import compare_histories
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_task import SourceTaskRelation
from behave.sim.rtl.task_request import ReadyValidRequestCodec

from .test_protocol_interval_relation import _packed
from .test_rtl_source_history import HISTORY_SOURCE, _initial, _simple_contract
from .test_rtl_source_memory_relation import _prepare
from .test_rtl_source_monitors import _monitors
from .test_rtl_source_scheduler import _names
from .test_rtl_source_task_relation import SOURCE as MEMORY_SOURCE
from .test_rtl_source_task_relation import _contract as memory_contract
from .test_rtl_source_transactions import _contract as transaction_contract

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('bad', ['missing', 'extra', 'short', 'integer', 'bitvector', 'context'])
def test_reset_image_is_complete_and_context_typed(bad):
    context = z3.Context()
    relation = ProtocolIntervalRelation(_simple_contract(), context=context)
    terms = {'c': (z3.Bool('reset', ctx=context),)}
    if bad == 'missing':
        terms = {}
    elif bad == 'extra':
        terms['other'] = terms['c']
    elif bad == 'short':
        terms['c'] = ()
    else:
        terms['c'] = ({'integer': 1, 'bitvector': z3.BitVecVal(1, 1, ctx=context),
                       'context': z3.Bool('other_context')}[bad],)
    with pytest.raises((TypeError, ValueError)):
        relation.reset_choices([api.EventSlot()], terms=terms)


@pytest.mark.parametrize('opposite_polarity', [False, True])
def test_domains_sharing_a_reset_pin_cannot_choose_incompatible_levels(opposite_polarity):
    endpoints = []
    for domain in ('A', 'B'):
        endpoints.extend((
            api.Endpoint(f'in_{domain}', 'in', [api.Field('x', api.FiniteType(2), f'x_{domain}')],
                         api.ready_valid('in', f'iv_{domain}', f'ir_{domain}'), domain, kind='transaction'),
            api.Endpoint(f'out_{domain}', 'out', [api.Field('y', api.FiniteType(2), f'y_{domain}')],
                         api.ready_valid('out', f'ov_{domain}', f'ordy_{domain}'), domain, kind='transaction')))
    contract = api.TaskContract(endpoints, [api.ClockDomain('A', 'ca', 'rst'),
        api.ClockDomain('B', 'cb', 'rst', reset_active_low=opposite_polarity)])
    relation = ProtocolIntervalRelation(contract)
    resets = {name: (z3.Bool(f'reset_{name}'),) for name in ('A', 'B')}
    encoded = relation.encode(ProtocolBoundary.initial(contract), {'in_A': (), 'in_B': ()},
        [api.EventSlot(ticks=('A', 'B'), controls={'ordy_A': 1, 'ordy_B': 1, 'rst': 0})],
        [{name: z3.BoolVal(False) for name in relation.observed_widths}], atomic=True, reset_terms=resets)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    for a, b in product((False, True), repeat=2):
        solver.push()
        solver.add(resets['A'][0] == a, resets['B'][0] == b)
        assert solver.check() == (z3.sat if a == (b != opposite_polarity) else z3.unsat)
        solver.pop()


@pytest.mark.parametrize('memory', [False, True])
@pytest.mark.parametrize('private', [False, True])
def test_every_reset_pattern_matches_fixed_whole_history(tmp_path, memory, private):
    source = MEMORY_SOURCE if memory else HISTORY_SOURCE
    contract = memory_contract() if memory else transaction_contract()
    machine, plan, env, template = _prepare(tmp_path, source)
    names = _names(machine)
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    context = z3.Context() if private else None
    state_widths = {names['busy']: 1} if memory else {names['saved']: 2}
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue), context=context,
        complete_state=True, occurrences=True, monitors=_monitors(contract, fields=state_widths),
        transactions=TransactionProduct(contract))
    steps = 3
    # Reset while a delayed read is pending must flush the response, not memory.
    environment = MemoryEnvironment([MemoryRun('ram', initial={1: 201}, read_latency=2,
        ready={None: [1]*steps})]) if memory else None
    initial_memory = MemoryEnvironmentState.initial(contract.memories, environment)
    state = _initial(machine, env)
    slots = [api.EventSlot(offers=('in',), controls={'rst': 0, 'ordy': 1})]*steps
    queues = _packed(contract, {'in': [(1,), (0,), (3,)]}, context)
    terms = {'c': tuple(z3.Bool(f'reset_{i}', ctx=context) for i in range(steps))}
    symbolic = relation.encode(state, ProtocolBoundary.initial(contract), queues, slots, environment,
        memory_state=initial_memory, startup=True, reset_terms=terms, require_quiescence=False,
        tag='symbolic_reset')
    for pattern in product((False, True), repeat=steps):
        fixed_slots = [api.EventSlot(offers=slot.offers, reset=reset, controls={'rst': int(reset), 'ordy': 1})
                       for slot, reset in zip(slots, pattern, strict=True)]
        fixed = relation.encode(state, ProtocolBoundary.initial(contract), queues, fixed_slots, environment,
            memory_state=initial_memory, startup=True, require_quiescence=False, tag=f'fixed_{pattern}')
        pair = compare_histories(symbolic, fixed, tuple(machine.program.fields))
        solver = z3.Solver(ctx=context)
        solver.set(timeout=30_000)
        solver.add(*pair.constraints, *(term == bit for term, bit in zip(terms['c'], pattern, strict=True)))
        assert solver.check() == z3.sat, pattern
        if memory and not any(pattern):
            # This fixture has order-sensitive transient source hits. Keep
            # that counterexample; equality under unrelated orders is false.
            solver.push()
            solver.add(pair.different)
            assert solver.check() == z3.sat
            solver.pop()
        # Compare the two reset representations under the SAME arbitrary
        # internal choices, not a false claim that all process orders agree.
        left_phases = (symbolic.startup, *(phase for slot in symbolic.source.slots for phase in slot.phases))
        right_phases = (fixed.startup, *(phase for slot in fixed.source.slots for phase in slot.phases))
        solver.add(*(left == right for a, b in zip(left_phases, right_phases, strict=True)
                     for left, right in zip(a.actions, b.actions, strict=True)))
        solver.add(z3.Or(pair.different, pair.unfinished))
        result = solver.check()
        assert result == z3.unsat, (pattern, result, solver.reason_unknown(),
            [name for name, term in pair.differences.items() if z3.is_true(solver.model().eval(term))]
            if result == z3.sat else [])


@pytest.mark.parametrize('correct', [False, True])
def test_free_later_reset_finds_reset_restoration_violation(tmp_path, correct):
    source = HISTORY_SOURCE if correct else HISTORY_SOURCE.replace('saved<=0;', 'saved<=saved;')
    machine, _, env, template = _prepare(tmp_path, source)
    contract, names = _simple_contract(), _names(machine)
    relation = SourceTaskRelation(template, machine, contract, (), complete_state=True,
        monitors=_monitors(contract, fields={names['saved']: 2}))
    resets = {'c': (True, False, z3.Bool('later_reset'))}
    encoded = relation.encode(_initial(machine, env), ProtocolBoundary.initial(contract),
        _packed(contract, {'in': [(1,)]}, None),
        [api.EventSlot(offers=('in',), controls={'rst': 0, 'ordy': 1})]*3, None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True, reset_terms=resets,
        require_quiescence=False)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    solver.push()
    solver.add(encoded.hits[ResetRestorationProvider.violation_goal('c')])
    assert solver.check() == (z3.unsat if correct else z3.sat)
    if not correct:
        assert z3.is_true(solver.model().eval(resets['c'][2]))
    solver.pop()
    solver.add(encoded.hits[ResetRestorationProvider.scenario_goal('c')] != resets['c'][2])
    assert solver.check() == z3.unsat


@pytest.mark.cosim
@pytest.mark.parametrize('active_low,edge', [(False, 'rising'), (True, 'falling')])
@pytest.mark.parametrize('later_reset', [False, True])
def test_source_reset_witness_uses_existing_public_request_and_native_replay(
        tmp_path, active_low, edge, later_reset):
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.native_observation import _read_intervals
    from behave.sim.rtl.startup_state import RTLStartupState

    source = HISTORY_SOURCE.replace('posedge clk', 'negedge clk' if edge == 'falling' else 'posedge clk')
    if active_low:
        source = source.replace('!rst &&', 'rst &&').replace('if(rst)', 'if(!rst)')
    machine, plan, env, template = _prepare(tmp_path, source)
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    base = _simple_contract(edge)
    contract = api.TaskContract(base.endpoints, [api.ClockDomain.from_dict(
        {**base.clocks[0].as_dict(), 'reset_active_low': active_low})])
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    widths = {'saved': 2, 'ov': 1, 'y': 2}
    pins = {'clk': int(edge == 'falling'), 'rst': int(active_low)}
    pin_fields = sorted((field.port, machine.program.kinds[name].width, pins.get(field.port, 0))
                        for name, field in machine.program.fields.items() if field.input)
    initial = InitialStateCoordinates(artifact.identity, widths,
        startup_pins={name: width for name, width, _value in pin_fields})
    from behave.sim.rtl.initial_state import RTLInitialState

    startup = RTLStartupState(RTLInitialState.from_values(
        artifact.identity, widths, dict.fromkeys(widths, 0)), pin_fields)
    codec = ReadyValidRequestCodec(contract, 2, 1, initial=initial)
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
                                  complete_state=True, occurrences=True)
    resets = {'c': (z3.BoolVal(True), z3.BoolVal(False), z3.Bool('later'), z3.BoolVal(False))}
    actions = tuple(replace(action, offers={'in': z3.Not(reset)}, controls={'ordy': z3.BoolVal(True)})
        for action, reset in zip(relation.protocol.symbolic_actions(4), resets['c'], strict=True))
    queues = {'in': ((z3.BitVec('first', 2),), (z3.BitVec('second', 2),))}
    # Both representations explicitly choose the same pre-evaluation pins.
    # A settled register deposit is not a construction witness.
    encoded = relation.encode(_initial(machine, env, clk=int(edge == 'falling'), rst=int(active_low)),
        ProtocolBoundary.initial(contract), queues, [api.EventSlot()]*4, None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True, actions=actions,
        reset_terms=resets, require_quiescence=False)
    solver = z3.Solver()
    solver.add(*encoded.constraints, resets['c'][2] == later_reset,
               queues['in'][0][0] == 1, queues['in'][1][0] == 2)
    assert solver.check() == z3.sat
    model = solver.model()
    common = {codec.length: 2, **initial.encode(startup)}
    common.update({name: model.eval(value).as_long() for cells, values in
                   zip(codec.cells, queues['in'], strict=True) for name, value in zip(cells, values, strict=True)})
    rows = [{**common, codec.offer: int(z3.is_true(model.eval(action.offers['in']))), codec.ready: 1,
             codec.reset: int(z3.is_true(model.eval(reset))) ^ int(active_low)}
            for action, reset in zip(actions, resets['c'], strict=True)]
    stimulus = codec.decode(rows)
    assert codec.encode(stimulus, initial_state=initial.decode(rows)) == rows
    # Exercise the parent dispatch with this source formula. The source
    # worker's prepare-replay answer is the request's own stimulus and startup
    # choice; a real worker decodes it in test_rtl_source_analysis_worker.
    from types import SimpleNamespace
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance

    value = {'stimulus': stimulus.as_dict(), 'initialization': startup.as_dict()}

    class Proxy(RTLIsolatedAnalysisInstance):
        def _raw_call(self, operation, **payload):
            # Preparing replay inputs uses the decode payload without its
            # standalone membership proof; the source audit follows.
            assert operation == 'prepare-replay' and payload == {'rows': rows}
            return value

    proxy = object.__new__(Proxy)
    def fresh_source_trace(actual, *, reuse, **choice):
        assert reuse is False
        return artifact.exact_trace(
            contract, actual, observe_names=(*widths, 'ir'), state_names=tuple(widths),
            state_widths=widths, **choice)
    proxy._context = SimpleNamespace(source_trace=fresh_source_trace)
    actual_stimulus, trace = proxy._source_payload(rows)
    assert actual_stimulus == stimulus
    startup.check_trace(trace)
    native = list(_read_intervals(thaw_record_value(trace.source_evidence), stimulus,
                                  catalogue, expected_plan=None, prefix=False))
    phases = (encoded.startup, *(phase for slot in encoded.source.slots for phase in slot.phases))
    differences = {'unfinished': encoded.unfinished}
    for i, (phase, (_, observed)) in enumerate(zip(phases, native, strict=True)):
        differences.update({f'phase[{i}].hit:{goal}': term != (goal in observed.goals)
                            for goal, term in phase.hits.items()})
        differences.update({f'phase[{i}].occurrence:{key}': term != (key in observed.occurrences)
                            for key, term in phase.occurrences.items()})
    names = _names(machine)
    assert len(trace.records) == 4
    for i, record in enumerate(trace.records):
        differences.update({f'slot[{i}].state:{name}': encoded.source.slots[i].state[names[name]] != dict(record.state)[name]
                            for name in widths})
        differences[f'slot[{i}].accepted'] = encoded.protocol.accepted[i]['in'] != (dict(record.accepted).get('in') is not None)
        differences[f'slot[{i}].emitted'] = encoded.protocol.emitted[i]['out'] != (dict(record.accepted).get('out') is not None)
    # Both paths consume the same original queue, including after the later reset.
    assert model.eval(encoded.protocol.pointers[-1]['in']).as_long() == 2
    solver.add(z3.Or(*differences.values()))
    result = solver.check()
    assert result == z3.unsat, [name for name, term in differences.items()
                              if result == z3.sat and z3.is_true(solver.model().eval(term))]
