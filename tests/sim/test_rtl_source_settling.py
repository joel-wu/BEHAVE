"""Completeness checks must not assume the very quiescence they are proving."""

from dataclasses import replace
from itertools import product

import pytest
import z3

from behave.sim import api
from behave.sim.symbolic import interp
from behave.sim.symbolic.step_template import _leaves
from behave.sim.core.memory import MemoryEnvironment, MemoryEnvironmentState, MemoryInterface, MemoryPort, MemoryRun
from behave.sim.rtl.protocol_relation import ProtocolBoundary
from behave.sim.rtl.source_memory import SourceMemoryRelation
from behave.sim.rtl.source_task import SourceTaskRelation

from .test_rtl_source_history import HISTORY_SOURCE, _encode, _initial, _simple_contract
from .test_rtl_source_memory_relation import _encoded_state, _prepare, _slots
from .test_rtl_source_relation import _pins, _relation
from .test_rtl_source_scheduler import _lower, _names, _state
from .test_rtl_source_task_relation import SOURCE, _contract

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


def _unfinished_prefixes(machine, initial, pins, bound):
    """Independent interpreter: retain every accepted order, even if not quiet."""
    frontier = [(initial, False, False)]
    for offset in range(bound):
        following = []
        for state, failed, _quiet in frontier:
            if failed:
                following.append((state, True, False))
                continue
            choices = (0,) if offset == 0 and pins is not None else range(machine.commit_action + 1)
            for action in choices:
                drive = {driven: pins[name] if offset == 0 and pins is not None else state[name]
                         for name, driven in machine.drive_inputs}
                post, outputs, _marks, error = interp.run_step(
                    machine.procedure, {**drive, 'action': action}, state)
                if error:
                    following.append((state, True, False))
                elif outputs.transactions[0]['accepted'] == 1:
                    following.append((post, False, outputs.transactions[0]['quiescent'] == 1))
        frontier = following
    return {not failed and not quiet for _state, failed, quiet in frontier}


@pytest.mark.parametrize('body', [
    'always @(posedge clk) q<=a; always @(q) r=q;',
    'always_comb q=a; always @(q) r=q;',
    'always @(posedge clk) begin if(a) $fatal(1,"stop"); q<=a; end always @(q) r=q;',
])
def test_prefix_bound_agrees_with_every_small_domain_interpreter_order(tmp_path, body):
    source = f'module M(input clk,a,output reg q,r); {body} endmodule'
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    goals = sorted({goal for _, _, goal in machine.occurrences})
    state = _state(machine, {'clk': 0, 'a': 0, 'q': 0, 'r': 0})
    relation, pre, env = _relation(machine, state, goals)
    pins = _pins(machine, env, {names['clk']: 1, names['a']: 0})
    pins[names['a']] = None
    # The input and both stored bits are free, not copied from a successful run.
    for pin in ('q', 'r'):
        pre[names[pin]] = z3.FreshConst(pre[names[pin]].sort(), f'initial_{pin}')
    for bound in (1, machine.event_bound):
        encoded = relation.encode(pre, pins, event_bound=bound, require_quiescence=False)
        solver = z3.Solver()
        solver.add(*encoded.constraints)
        for a, q, r in product(range(2), repeat=3):
            values = {names['clk']: 1, names['a']: a}
            expected = _unfinished_prefixes(
                machine, _state(machine, {'clk': 0, 'a': 0, 'q': q, 'r': r}), values, bound)
            solver.push()
            solver.add(encoded.inputs[names['a']] == a, pre[names['q']] == q, pre[names['r']] == r)
            assert solver.check() == z3.sat  # Never count vacuous UNSAT as completeness.
            for unfinished in (False, True):
                solver.push()
                solver.add(encoded.unfinished == unfinished)
                assert solver.check() == (z3.sat if unfinished in expected else z3.unsat)
                solver.pop()
            if bound == machine.event_bound:
                assert expected == {False}
            solver.pop()


def test_too_short_bound_is_a_witness_not_an_empty_domain_proof(tmp_path):
    machine, _ = _lower(tmp_path, 'module M(input clk,a,output reg q); always @(posedge clk) q<=a; endmodule')
    names = _names(machine)
    relation, pre, env = _relation(machine, _state(machine, {'clk': 0, 'a': 0, 'q': 0}), ())
    pins = _pins(machine, env, {names['clk']: 1, names['a']: 1})
    complete = relation.encode(pre, pins, event_bound=1)
    prefix = relation.encode(pre, pins, event_bound=1, require_quiescence=False)
    solver = z3.Solver()
    solver.add(*complete.constraints)
    assert solver.check() == z3.unsat  # Old completion premise removes this prefix.
    solver.reset()
    solver.add(*prefix.constraints, prefix.unfinished)
    assert solver.check() == z3.sat


@pytest.mark.parametrize('private_context', [False, True])
def test_startup_oscillation_is_not_silently_excluded(tmp_path, private_context):
    machine, _ = _lower(tmp_path, '''module M(input a,output reg q,r);
      always_comb q=~r;
      always_comb r=q;
    endmodule''')
    assert machine.event_bound is None
    names = _names(machine)
    initial = machine.boundary({name: 0 for name in machine.program.fields}, startup=True)
    relation, pre, _env = _relation(machine, initial, ())
    context = z3.Context() if private_context else None
    if context is not None:
        relation = relation.translate(context)
        pre = {key: value.translate(context) for key, value in pre.items()}
    # A diagnostic finite prefix is not being advertised as a proved bound.
    prefix = relation.settle(pre, event_bound=6, require_quiescence=False)
    solver = z3.Solver(ctx=context)
    solver.add(*prefix.constraints, prefix.unfinished)
    assert solver.check() == z3.sat
    oracle = _unfinished_prefixes(machine, initial, None, 6)
    assert oracle == {True}
    assert names['q'] in prefix.state


def test_terminal_failure_does_not_become_an_unfinished_prefix(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input clk,a,output reg q);
      always @(posedge clk) if(a) $fatal(1,"stop"); else q<=a;
    endmodule''')
    names = _names(machine)
    goals = sorted({goal for _, _, goal in machine.occurrences})
    relation, pre, env = _relation(machine, _state(machine, {'clk': 0, 'a': 0, 'q': 0}), goals)
    pins = _pins(machine, env, {names['clk']: 1, names['a']: 1})
    prefix = relation.encode(pre, pins, event_bound=2, require_quiescence=False)
    solver = z3.Solver()
    solver.add(*prefix.constraints)
    assert solver.check() == z3.sat
    assert z3.is_false(solver.model().eval(prefix.alive))
    assert solver.model().eval(prefix.error).as_long() != 0
    assert any(z3.is_true(solver.model().eval(hit)) for hit in prefix.hits.values())
    solver.add(prefix.unfinished)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
def test_disabled_interval_holds_recursive_state_without_faking_a_failure(tmp_path, private_context):
    source = '''module M(input clk,a,output reg q);
      reg mem[1:0][0:1];
      always @(posedge clk) begin
        mem[0][0]<=a; mem[1][1]<=mem[0][0]; q<=mem[1][1];
        if(a) $fatal(1,"not reached");
      end
    endmodule'''
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    goals = sorted({goal for _, _, goal in machine.occurrences})
    initial = _state(machine, {'clk': 0, 'a': 0, 'q': 1, 'mem': [[1, 0], [0, 1]]})
    relation, pre, env = _relation(machine, initial, goals)
    context = z3.Context() if private_context else None
    if context is not None:
        relation = relation.translate(context)
    enabled = z3.Bool('enter_interval', ctx=context)
    encoded = relation.encode(pre, _pins(machine, env, {names['clk']: 1, names['a']: 1}),
        event_bound=machine.event_bound, enabled=enabled, require_quiescence=False)
    solver = z3.Solver(ctx=context)
    solver.add(*encoded.constraints, z3.Not(enabled))
    assert solver.check() == z3.sat
    solver.add(z3.Or(z3.Not(encoded.alive), encoded.error != 0, encoded.unfinished,
        encoded.continuable, *encoded.hits.values(),
        *(left != (right if right.ctx == left.ctx else right.translate(left.ctx))
          for name, value in encoded.state.items()
          for left, right in zip(_leaves(value), _leaves(pre[name]), strict=True))))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
def test_history_startup_cutoff_keeps_state_memory_queues_and_error_distinct(tmp_path, private_context):
    source = (SOURCE.replace('input ram_req_ready',
        'output ram_req_we,output [7:0] ram_req_wdata,input ram_req_ready')
        .replace('assign ram_req_addr=a;',
        'assign ram_req_addr=a; assign ram_req_we=1; assign ram_req_wdata=201;'))
    machine, _, env, template = _prepare(tmp_path, source)
    # Deliberately inadequate diagnostic bound, not a runtime policy/default.
    machine = replace(machine, event_bound=1)
    base = _contract()
    contract = api.TaskContract(base.endpoints, base.clocks,
        memories=(MemoryInterface('ram', 2, 8, 'c', [MemoryPort('rw')]),))
    relation = SourceTaskRelation(template, machine, contract, (),
        context=z3.Context() if private_context else None, complete_state=True)
    environment = MemoryEnvironment([MemoryRun('ram', initial={2:17}, ready={None:[1]*3})])
    slots = [api.EventSlot(offers=('in',), reset=i==1,
                          controls={'rst':int(i==1), 'ordy':1}) for i in range(3)]
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(2,)],
                      environment, startup=True, require_quiescence=False)
    solver = z3.Solver(ctx=relation.context)
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    before = MemoryEnvironmentState.initial(contract.memories, environment).as_dict()
    solver.add(z3.Or(z3.Not(encoded.unfinished), z3.Not(encoded.startup.unfinished),
        z3.Not(encoded.alive), encoded.error != 0, encoded.continuable,
        encoded.source.unfinished,
        *(term for row in encoded.protocol.accepted for term in row.values()),
        *(pointer != 0 for row in encoded.protocol.released for pointer in row.values()),
        *(z3.Not(term) for i in range(len(slots)+1)
          for term in encoded.source.memory.state_constraints(i, before)),
        *(left != right for name, value in encoded.state.items()
          for left, right in zip(_leaves(value), _leaves(encoded.startup.state[name]), strict=True))))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('phase_index', [0, 1, 2])
def test_first_unfinished_phase_stops_later_drives_and_reset(tmp_path, monkeypatch, phase_index):
    # Exercise return-edge work too. Each edge/control interval has real work.
    source = HISTORY_SOURCE.replace('endmodule',
        'wire [1:0] mirror; assign mirror=a; '
        'reg [1:0] trailing; always @(negedge clk) trailing<=a; endmodule')
    machine, _, env, template = _prepare(tmp_path, source)
    original = type(machine).phase_event_bounds
    # The first control drive conservatively uses the full bound when the
    # startup successor's clock is symbolic. Test control trimming on slot 1.
    stop_slot = int(phase_index == 0)
    slot_number = 0

    def short_bound(self, *args):
        nonlocal slot_number
        bounds = list(original(self, *args))
        if slot_number == stop_slot:
            bounds[phase_index] = 1
        slot_number += 1
        return tuple(bounds)

    monkeypatch.setattr(type(machine), 'phase_event_bounds', short_bound)
    contract = _simple_contract()
    goals = sorted({goal for _, _, goal in machine.occurrences})
    relation = SourceTaskRelation(template, machine, contract, goals, complete_state=True)
    slots = [api.EventSlot(offers=('in',), reset=i==2,
                          controls={'rst':int(i==2), 'ordy':1}) for i in range(4)]
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(1,), (2,)],
                      startup=True, require_quiescence=False)
    stop = encoded.source.slots[stop_slot].phases[phase_index]
    solver = z3.Solver()
    solver.add(*encoded.constraints, stop.unfinished)
    assert solver.check() == z3.sat
    later = (*encoded.source.slots[stop_slot].phases[phase_index+1:],
             *(phase for slot in encoded.source.slots[stop_slot+1:] for phase in slot.phases))
    solver.add(z3.Or(z3.Not(encoded.alive), encoded.error != 0, encoded.continuable,
        z3.Not(encoded.unfinished),
        z3.Not(encoded.protocol.accepted[0]['in']),
        *(row['in'] for row in encoded.protocol.accepted[1:]),
        *(row['in'] != 1 for row in encoded.protocol.pointers[1:]),
        *(phase.entered for phase in later),
        *(hit for phase in later for hit in phase.hits.values()),
        *(left != right for name, value in encoded.state.items()
          for left, right in zip(_leaves(value), _leaves(stop.state[name]), strict=True))))
    assert solver.check() == z3.unsat


def test_history_bound_keeps_both_finished_and_unfinished_input_subdomains(tmp_path, monkeypatch):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output ov,output reg [1:0] y,input ordy);
      assign ir=1; assign ov=0;
      always @(a) begin if(a==1) y=3; else y=a; end
    endmodule'''
    machine, _, env, template = _prepare(tmp_path, source)
    original = type(machine).phase_event_bounds
    slot_number = 0

    def short_second_control(self, *args):
        nonlocal slot_number
        bounds = original(self, *args)
        slot_number += 1
        return (1, *bounds[1:]) if slot_number == 2 else bounds

    monkeypatch.setattr(type(machine), 'phase_event_bounds', short_second_control)
    contract = _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (), complete_state=True)
    second = z3.BitVec('second_payload', 2)
    encoded = relation.encode(_initial(machine, env), ProtocolBoundary.initial(contract),
        {'in': ((z3.BitVecVal(0, 2),), (second,))},
        [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})]*3, None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True,
        require_quiescence=False)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    for value in range(4):
        solver.push()
        solver.add(second == value)
        assert solver.check() == z3.sat  # No input is removed by the cutoff.
        solver.add(z3.Or(encoded.unfinished != (value != 0), z3.Not(encoded.alive),
                         encoded.protocol.pointers[-1]['in'] != (1 if value else 2)))
        assert solver.check() == z3.unsat
        solver.pop()


@pytest.mark.parametrize('require_quiescence', [False, True])
def test_disabled_memory_interval_never_samples_a_live_held_request(tmp_path, require_quiescence):
    machine, _, env, template = _prepare(tmp_path)
    memory = MemoryInterface('ram', 2, 8, 'c', [MemoryPort('rw')])
    environment = MemoryEnvironment([MemoryRun('ram', initial={2:17}, read_latency=3,
                                               ready={None:[1,1]})])
    relation = SourceMemoryRelation(template, machine, (memory,), (), complete_state=True)
    values = {'iv':1, 'a':2, 'we':1, 'd':201, 'ram_req_valid':1,
              'ram_req_addr':2, 'ram_req_we':1, 'ram_req_wdata':201, 'ram_req_ready':1}
    state = _encoded_state(machine, env,
        _state(machine, {**dict.fromkeys(_names(machine), 0), **values}))
    slots = _slots(machine, env, [{'iv':1, 'we':1, 'a':2, 'd':201}]*2,
                   relation, resets=(False, True))
    before = MemoryEnvironmentState.initial((memory,), environment)
    encoded = relation.encode(state, slots, environment, initial_state=before,
        enabled=z3.BoolVal(False), require_quiescence=require_quiescence)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Or(z3.Not(encoded.alive), encoded.unfinished, encoded.continuable,
        *(z3.Not(term) for i in range(3)
          for term in encoded.memory.state_constraints(i, before.as_dict()))))
    assert solver.check() == z3.unsat
