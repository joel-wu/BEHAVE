"""Explicit startup and whole-history source/protocol/memory composition."""

import pytest
import z3

from behave.sim import api
from behave.sim.core.memory import MemoryEnvironment, MemoryEnvironmentState, MemoryInterface, MemoryPort, MemoryRun
from behave.sim.rtl.protocol_relation import ProtocolBoundary
from behave.sim.rtl.source_task import SourceTaskRelation

from .test_protocol_interval_relation import _packed
from .test_rtl_source_memory_relation import _encoded_state, _prepare
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _names
from .test_rtl_source_task_relation import SOURCE, _contract

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]

HISTORY_SOURCE = '''module M(input clk,rst,iv,input [1:0] a,output ir,
  output reg ov,output reg [1:0] y,input ordy);
  reg [1:0] saved;
  assign ir=!rst && (!ov || ordy);
  always @(posedge clk) begin
    if(rst) begin saved<=0; ov<=0; y<=0; end
    else if(iv && ir) begin
      ov<=1;
      if(saved==1 && a==2) y<=3; else y<=a;
      saved<=a;
    end else if(ordy) ov<=0;
  end
endmodule'''


def _initial(machine, env, **pins):
    values = dict.fromkeys(machine.program.fields, 0)
    names = _names(machine)
    values.update({names[name]: value for name, value in pins.items()})
    return _encoded_state(machine, env, machine.boundary(values, startup=True))


def _simple_contract(edge='rising', *, reset_policy=None):
    return api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', api.FiniteType(2), 'a')],
                     api.ready_valid('in', 'iv', 'ir'), 'c', kind='transaction', reset_policy=reset_policy),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(2), 'y')],
                     api.ready_valid('out', 'ov', 'ordy'), 'c', kind='transaction',
                     answers_per_transaction=1)],
        [api.ClockDomain('c', 'clk', 'rst', edge=edge, reset_restores=True)])


def _encode(relation, state, contract, slots, values, environment=None, **kwargs):
    return relation.encode(state, ProtocolBoundary.initial(contract),
        _packed(contract, {'in': values}, relation.context), slots, environment,
        memory_state=MemoryEnvironmentState.initial(contract.memories, environment), **kwargs)


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('require_quiescence', [False, True])
@pytest.mark.parametrize('release_controls', [False, True])
def test_startup_failure_cannot_accept_input_write_memory_or_restart_on_reset(
        tmp_path, private_context, require_quiescence, release_controls):
    source = (SOURCE.replace('input ram_req_ready',
        'output ram_req_we,output [7:0] ram_req_wdata,input ram_req_ready')
        .replace('assign ram_req_addr=a;',
        'assign ram_req_addr=a; assign ram_req_we=1; assign ram_req_wdata=201;')
        .replace('endmodule', 'always_comb if(a==0) $fatal(1,"startup"); endmodule'))
    machine, plan, env, template = _prepare(tmp_path, source)
    startup_goal = _goal(source, plan, 'a==0')
    later_goal = _goal(source, plan, 'ram_resp_rdata==201')
    goals, base = (startup_goal, later_goal), _contract()
    contract = api.TaskContract(base.endpoints, base.clocks,
        memories=(MemoryInterface('ram', 2, 8, 'c', [MemoryPort('rw')]),))
    context = z3.Context() if private_context else None
    relation = SourceTaskRelation(template, machine, contract, goals,
                                  context=context, complete_state=True, occurrences=True)
    environment = MemoryEnvironment([MemoryRun('ram', initial={2:201},
                                               read_latency=1, ready={None:[1]*3})])
    slots = [api.EventSlot(offers=('in',), reset=i==1,
                          controls={'rst':int(i==1), 'ordy':1}) for i in range(3)]
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(2,)],
                      environment, startup=True, release_controls=release_controls,
                      require_quiescence=require_quiescence)
    solver = z3.Solver(ctx=context)
    solver.add(*encoded.constraints, *encoded.source.memory.validity_constraints)
    assert solver.check() == z3.sat
    model = solver.model()
    assert set(encoded.state) == set(machine.procedure.state)
    assert z3.is_true(model.eval(encoded.startup.hits[startup_goal]))
    assert z3.is_false(model.eval(encoded.startup.alive))
    initial_memory = MemoryEnvironmentState.initial(contract.memories, environment).as_dict()
    memory_changes = []
    for i in range(len(slots)+1):
        assert encoded.source.memory.state_image(model, i) == initial_memory
        memory_changes.extend(z3.Not(term) for term in
                              encoded.source.memory.state_constraints(i, initial_memory))
    solver.add(z3.Or(encoded.alive, encoded.unfinished, z3.Not(encoded.hits[startup_goal]),
        encoded.error != encoded.startup.error, encoded.hits[later_goal],
        *encoded.source.hits.values(), *encoded.source.occurrences.values(),
        *(term for row in encoded.protocol.accepted for term in row.values()),
        *(pointer != 0 for row in encoded.protocol.released for pointer in row.values()),
        *memory_changes))
    assert solver.check() == z3.unsat


def test_failed_prefix_stays_failed_without_a_startup_operation(tmp_path):
    machine, plan, env, template = _prepare(tmp_path, SOURCE)
    goal, contract = _goal(SOURCE, plan, 'ram_resp_rdata==201'), _contract()
    relation = SourceTaskRelation(template, machine, contract, (goal,))
    environment = MemoryEnvironment([MemoryRun('ram', ready={None:[1,1]})])
    state = _initial(machine, env)
    startup = relation.source.relation.settle(state, event_bound=machine.event_bound)
    error = z3.BitVecVal(1, startup.error.size())
    encoded = _encode(relation, startup.state, contract,
        [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})]*2,
        [(2,)], environment, alive=z3.BoolVal(False), error=error)
    solver = z3.Solver()
    solver.add(*startup.constraints, *encoded.constraints,
               *encoded.source.memory.validity_constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Or(encoded.alive, encoded.error != error, encoded.hits[goal],
                     *(row['in'] for row in encoded.protocol.accepted)))
    assert solver.check() == z3.unsat


def test_first_control_drive_accounts_for_the_declared_initial_clock_level(tmp_path):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output ov,output reg [1:0] y,input ordy);
      assign ir=!rst && ordy; assign ov=iv;
      always @(negedge clk) if(a==0) y<=3; else y<=a;
    endmodule'''
    machine, plan, env, template = _prepare(tmp_path, source)
    goal, contract = _goal(source, plan, 'a==0'), _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (goal,), complete_state=True)
    slots = [api.EventSlot(controls={'rst':0, 'ordy':1})]
    encoded = _encode(relation, _initial(machine, env, clk=1), contract, slots, [], startup=True)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Or(z3.Not(encoded.source.slots[0].phases[0].hits[goal]),
                     z3.Not(encoded.alive), encoded.state[_names(machine)['y']] != 3))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('require_quiescence', [False, True])
def test_complete_history_uses_explicit_initial_support_and_both_queue_cells(tmp_path, require_quiescence):
    source = HISTORY_SOURCE
    machine, plan, env, template = _prepare(tmp_path, source)
    goal, contract = _goal(source, plan, 'saved==1'), _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (goal,),
                                  complete_state=True, occurrences=True)
    initial = _initial(machine, env)
    saved = _names(machine)['saved']
    initial[saved] = z3.BitVec('initial_saved', initial[saved].size())
    queues = {'in': ((z3.BitVec('first', 2),), (z3.BitVec('second', 2),))}
    encoded = relation.encode(initial, ProtocolBoundary.initial(contract), queues,
        [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})]*2, None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True,
        require_quiescence=require_quiescence)
    solver = z3.Solver()
    solver.add(*encoded.constraints, z3.UGE(initial[saved], 0), z3.ULE(initial[saved], 3))
    first, second = queues['in'][0][0], queues['in'][1][0]
    expected_first = z3.And(initial[saved] == 1, first == 2)
    expected_second = z3.And(first == 1, second == 2)
    assert solver.check() == z3.sat
    solver.push()
    solver.add(z3.Or(encoded.startup.hits[goal],
        encoded.source.slots[0].hits[goal] != expected_first,
        encoded.source.slots[1].hits[goal] != expected_second,
        encoded.hits[goal] != z3.Or(expected_first, expected_second),
        z3.Not(encoded.alive), encoded.unfinished,
        encoded.state[saved] != z3.ZeroExt(encoded.state[saved].size()-2, second),
        *(z3.Not(row['in']) for row in encoded.protocol.accepted)))
    assert solver.check() == z3.unsat  # Every initial value, input and scheduler choice.
    solver.pop()
    solver.add(initial[saved] == 0, encoded.hits[goal])
    assert solver.check() == z3.sat
    solver.add(z3.Or(first != 1, second != 2))
    assert solver.check() == z3.unsat  # The goal needs the two-step history, not one input.


def test_equal_final_outputs_do_not_establish_native_hit_correspondence(tmp_path):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output reg ov,output reg [1:0] y,input ordy);
      reg [1:0] saved;
      assign ir=!rst && ordy;
      always @(posedge clk) saved=a;
      always @(posedge clk) begin
        if(saved==1) y=2; else y=2;
        ov=iv;
      end
    endmodule'''
    machine, plan, env, template = _prepare(tmp_path, source)
    goal, contract = _goal(source, plan, 'saved==1'), _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (goal,), complete_state=True)
    encoded = _encode(relation, _initial(machine, env), contract,
        [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})], [(1,)], startup=True)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    names = _names(machine)
    for hit in (False, True):
        solver.push()
        solver.add(encoded.hits[goal] == hit)
        assert solver.check() == z3.sat
        solver.add(z3.Or(encoded.state[names['saved']] != 1, encoded.state[names['y']] != 2,
                         encoded.state[names['ov']] != 1, z3.Not(encoded.alive)))
        assert solver.check() == z3.unsat
        solver.pop()
    # Both source orders have the same output/state but different hit sets.
    # The local relation must not quietly choose one and claim global exactness.


@pytest.mark.cosim
@pytest.mark.parametrize('require_quiescence', [False, True])
@pytest.mark.parametrize('edge,initial_saved,declared', [
    ('rising', 0, False), ('falling', 1, False), ('rising', 1, 'declaration'),
    ('rising', 1, 'process'), ('rising', 1, 'process-race'), ('rising', 1, 'closed-helper'),
])
def test_full_history_audits_native_startup_slots_state_and_every_goal(
        tmp_path, edge, initial_saved, declared, require_quiescence):
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.initial_state import RTLInitialState
    from behave.sim.rtl.native_observation import _read_intervals
    from behave.sim.rtl.source_goals import manifest_of

    source = HISTORY_SOURCE.replace('posedge clk', 'negedge clk' if edge=='falling' else 'posedge clk')
    if declared in {'declaration', 'closed-helper'}:
        source = source.replace('output reg ov,', 'output reg ov=0,').replace(
            'output reg [1:0] y,', 'output reg [1:0] y=0,').replace(
            'reg [1:0] saved;', f'reg [1:0] saved={initial_saved};')
        if declared == 'closed-helper':
            source = source.replace(f'reg [1:0] saved={initial_saved};', '''
              function automatic [1:0] initial_value(input [1:0] v); return v+1; endfunction
              reg [1:0] saved=initial_value(0);''')
    elif declared == 'process':
        source = source.replace('output reg ov,', 'output reg ov=0,').replace(
            'output reg [1:0] y,', 'output reg [1:0] y=0,').replace(
            'reg [1:0] saved;', f'reg [1:0] saved; initial saved={initial_saved};')
    elif declared == 'process-race':
        source = source.replace('reg [1:0] saved;',
            f'reg [1:0] saved; initial begin saved={initial_saved}; ov=0; y=0; end')
    machine, plan, env, template = _prepare(tmp_path, source, initialization=declared in {'process', 'process-race'})
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    contract, names = _simple_contract(edge), _names(machine)
    slots = [api.EventSlot(offers=('in',) if i < 3 else (),
                          controls={'rst':0, 'ordy':int(i != 1)}) for i in range(5)]
    values = [(1,), (2,), (3,)]
    stimulus = api.Stimulus({'in': values}, slots)
    widths = {'saved':2, 'ov':1, 'y':2}
    initial_values = {'saved':initial_saved, 'ov':0, 'y':0}
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    trace = artifact.exact_trace(contract, stimulus,
        observe_names=('saved', 'ov', 'y', 'ir'), state_names=tuple(widths), state_widths=widths,
        initial_state=(None if declared else
                       RTLInitialState.from_values(artifact.identity, widths, initial_values)))
    if declared:
        # No injected initial state: these values come from the actual HDL.
        assert dict(trace.initial_state) == initial_values
    native = list(_read_intervals(thaw_record_value(trace.source_evidence), stimulus,
                                  catalogue, expected_plan=None, prefix=False))
    assert len(trace.records) == len(slots)
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
                                  complete_state=True, occurrences=True)
    if declared:
        raw = dict.fromkeys(machine.program.fields, 0)
        raw.update({names[name]: None for name in initial_values})
        state = relation.initial_state(raw).state
    else:
        state = _initial(machine, env, clk=int(edge=='falling'), **initial_values)
    encoded = _encode(relation, state, contract, slots, values, startup=True,
                      require_quiescence=require_quiescence)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    phases = (encoded.startup, *(phase for slot in encoded.source.slots for phase in slot.phases))
    differences = {'unfinished': encoded.unfinished}
    for index, (phase, (_label, observed)) in enumerate(zip(phases, native, strict=True)):
        differences.update({f'phase[{index}].occurrence:{key}': term != (key in observed.occurrences)
                            for key, term in phase.occurrences.items()})
        differences.update({f'phase[{index}].goal:{goal}': term != (goal in observed.goals)
                            for goal, term in phase.hits.items()})
        differences[f'phase[{index}].alive'] = z3.Not(phase.alive)
    for i, record in enumerate(trace.records):
        sample, successor = encoded.source.slots[i].phases[0], encoded.source.slots[i]
        differences.update({f'slot[{i}].sample:{name}': sample.state[names[name]] != dict(record.outputs)[name]
                            for name in widths})
        differences.update({f'slot[{i}].successor:{name}': successor.state[names[name]] != dict(record.state)[name]
                            for name in widths})
        differences[f'slot[{i}].accepted'] = encoded.protocol.accepted[i]['in'] != (dict(record.accepted).get('in') is not None)
        differences[f'slot[{i}].emitted'] = encoded.protocol.emitted[i]['out'] != (dict(record.accepted).get('out') is not None)
    mismatch = z3.Or(*differences.values())
    if declared == 'process-race':
        # A matching source order exists, but another startup order observes ov
        # before the initial block assigns it. Equal outputs cannot certify hits.
        solver.push()
        solver.add(z3.Not(mismatch))
        assert solver.check() == z3.sat
        solver.pop()
    solver.add(mismatch)
    outcome = solver.check()
    if declared == 'process-race':
        assert outcome == z3.sat
        changed = [name for name, term in differences.items() if z3.is_true(solver.model().eval(term))]
        assert changed and all(name.startswith('phase[0].') for name in changed)
        assert any('.goal:' in name for name in changed)
        return
    assert outcome == z3.unsat, [name for name, term in differences.items()
                                if z3.is_true(solver.model().eval(term))]
