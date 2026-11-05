"""Changed source requests must change later memory responses and native hits."""

from dataclasses import replace

import pytest
import z3

from behave.sim import api
from behave.sim.symbolic import ranges
from behave.sim.symbolic.encoder import encode_concrete_value
from behave.sim.core.memory import (
    MemoryEnvironment,
    MemoryEnvironmentState,
    MemoryInterface,
    MemoryPort,
    MemoryRun,
    advance_memory_environment,
    drive_memory_environment,
)
from behave.sim.engines.rtl.source_boundary import SourceTraceBindings
from behave.sim.generation import StimulusField
from behave.sim.rtl.source_memory import SourceMemoryFrame, SourceMemoryRelation, SourceMemorySlot
from behave.sim.rtl.source_relation import SourceIntervalTemplate

from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _settle, _step

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]

SOURCE = '''module M(input clk,rst,iv,input [1:0] a,input we,input [7:0] d,
 output ram_req_valid,output [1:0] ram_req_addr,output ram_req_we,
 output [7:0] ram_req_wdata,input ram_req_ready,ram_resp_valid,
 input [7:0] ram_resp_rdata,output reg [7:0] q);
 assign ram_req_valid=iv && !rst;
 assign ram_req_addr=a;
 assign ram_req_we=we;
 assign ram_req_wdata=d;
 always @(posedge clk or posedge rst) begin
   if(rst) q<=0;
   else if(ram_resp_valid) begin
     if(ram_resp_rdata==8'hc9) q<=ram_resp_rdata;
     else q<=ram_resp_rdata;
   end
 end
endmodule'''


def _prepare(tmp_path, source=SOURCE, *, initialization=False):
    machine, plan = _lower(tmp_path, source, initialization=initialization)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    return machine, plan, env, SourceIntervalTemplate(machine.procedure, env)


def _encoded_state(machine, env, state):
    kinds = dict(machine.procedure.semantics.state_types)
    return {name: encode_concrete_value(value, env[name], name, kind=kinds[name])
            for name, value in state.items()}


def _slots(machine, env, rows, relation, *, active=None, resets=None):
    names, result = _names(machine), []
    kinds, driven = dict(machine.procedure.semantics.input_types), dict(machine.drive_inputs)
    for index, row in enumerate(rows):
        active_now = True if active is None else active[index]
        reset_now = False if resets is None else resets[index]
        phases = []
        for clock in (0, int(active_now), 0):
            image = {**row, 'clk': clock, 'rst': int(reset_now)}
            pins = {}
            for port, name in relation.ports.items():
                if name not in driven or port in relation.inputs:
                    continue
                value = image[port]
                pins[name] = (None if value is None else encode_concrete_value(value,
                    env[driven[name]], driven[name], kind=kinds[driven[name]]))
            phases.append(pins)
        # Held non-clock pins can change on the first drive only. Use the
        # scheduler's proof of quiet padding, not the seed's observed events.
        all_pins = dict.fromkeys(driven, 0)
        images = tuple({**all_pins, names['clk']: clock} for clock in (0, int(active_now), 0))
        bounds = machine.phase_event_bounds(all_pins, images, set(driven)-{names['clk']},
                                            machine.event_bound)
        result.append(SourceMemorySlot(tuple(phases), bounds,
            ('c',) if active_now else (), ('c',) if reset_now else (),
            tuple(names[name] for name, value in row.items() if value is None)))
    return tuple(result)


def _raw(model, term, width):
    return model.eval(term, model_completion=True).as_long() & ((1 << width)-1)


@pytest.mark.parametrize('latency', [1, 3])
def test_source_publishes_one_complete_memory_frame_before_building_the_next(tmp_path, monkeypatch, latency):
    machine, plan, env, template = _prepare(tmp_path)
    goal = _goal(SOURCE, plan, "ram_resp_rdata==8'hc9")
    memory = MemoryInterface('ram', 2, 8, 'c', [MemoryPort('rw')])
    environment = MemoryEnvironment([MemoryRun('ram', initial={1: 201}, read_latency=latency,
                                               ready={None: [1] * 4})])
    relation = SourceMemoryRelation(template, machine, (memory,), (goal,))
    initial = machine.boundary({name: 0 for name in machine.program.fields})
    slots = _slots(machine, env, [{'iv': 1, 'a': 1, 'we': 0, 'd': 0}] * 4, relation)
    calls, original = [], type(relation.relation).encode_phases_steps

    def counted(owner, *args, **kwargs):
        calls.append(kwargs['tag'])
        return (yield from original(owner, *args, **kwargs))

    monkeypatch.setattr(type(relation.relation), 'encode_phases_steps', counted)
    construction = relation.encode_steps(_encoded_state(machine, env, initial), slots, environment,
        initial_state=MemoryEnvironmentState.initial((memory,), environment))
    frames = []
    while True:
        try:
            frame = next(construction)
        except StopIteration as done:
            encoded = done.value
            break
        if type(frame) is SourceMemoryFrame:
            frames.append(frame)
            assert frame.index == len(frames) - 1
            assert len(calls) == len(frames)  # No future source slot constructed.
    assert len(frames) == encoded.memory.built_slots == len(slots)
    assert all(frame.source is slot for frame, slot in zip(frames, encoded.slots, strict=True))
    assert [term.get_id() for frame in frames for term in frame.memory_constraints] == [
        term.get_id() for term in encoded.memory.constraints]
    assert [term.get_id() for frame in frames for term in frame.memory_conflicts] == [
        term.get_id() for term in encoded.memory.conflicts]
    solver = z3.Solver()
    solver.add(*encoded.constraints, *encoded.memory.validity_constraints)
    assert solver.check() == z3.sat
    assert z3.is_true(solver.model().eval(encoded.hits[goal]))


@pytest.mark.parametrize('latency', [1, 3])
@pytest.mark.parametrize('scenario', ['read', 'write_read', 'paused', 'reset', 'stalled'])
def test_feedback_matches_every_concrete_drive_sample_and_successor(tmp_path, latency, scenario):
    machine, plan, env, template = _prepare(tmp_path)
    goal = _goal(SOURCE, plan, "ram_resp_rdata==8'hc9")
    memory = MemoryInterface('ram', 2, 8, 'c', [MemoryPort('rw')])
    total = latency+5
    environment = MemoryEnvironment([MemoryRun('ram', initial={1: 7, 2: 201},
        read_latency=latency, ready={None: [int(not(scenario == 'stalled' and i == 2))
                                          for i in range(total+2)]})])
    prefix = MemoryEnvironmentState.initial((memory,), environment)
    prefix = advance_memory_environment((memory,), environment, prefix, 0,
        {'ram_req_valid': 1, 'ram_req_addr': 1, 'ram_req_we': 1, 'ram_req_wdata': 17})
    prefix = advance_memory_environment((memory,), environment, prefix, 1,
        {'ram_req_valid': 1, 'ram_req_addr': 1, 'ram_req_we': 0, 'ram_req_wdata': 0})
    rows = [{'iv': int(i == 0 or scenario == 'write_read' and i == 1),
             'we': int(scenario == 'write_read' and i == 0),
             'a': None if i < (2 if scenario == 'write_read' else 1) else 0,
             'd': 201} for i in range(total)]
    active = [not(scenario in ('paused', 'reset') and i == 1) for i in range(total)]
    resets = [scenario == 'reset' and i == 1 for i in range(total)]
    relation = SourceMemoryRelation(template, machine, (memory,), (goal,), context=z3.Context(),
        state_reads=tuple(machine.program.fields), occurrences=True)
    initial = machine.boundary({name: 0 for name in machine.program.fields})
    slots = _slots(machine, env, rows, relation, active=active, resets=resets)
    encoded = relation.encode(_encoded_state(machine, env, initial), slots, environment,
                              initial_state=prefix, slot_offset=2)
    solver = z3.Solver(ctx=relation.relation.source.context)
    solver.add(*encoded.constraints, *encoded.memory.validity_constraints)
    names = _names(machine)
    for address in (1, 2):
        solver.push()
        for index, row in enumerate(rows):
            if row['a'] is None:
                solver.add(encoded.slots[index].phases[0].inputs[names['a']] == address)
        assert solver.check() == z3.sat
        model = solver.model()
        state, memory_state, hits, differences = initial, prefix, set(), []
        for index, (row, slot) in enumerate(zip(rows, slots, strict=True)):
            mem_pins = drive_memory_environment((memory,), environment, memory_state, 2+index,
                                                reset_domains=slot.reset_domains)
            for port, bits in mem_pins.items():
                for phase in encoded.slots[index].phases:
                    term = phase.inputs[names[port]]
                    assert _raw(model, term, relation.inputs[port]) == bits
                    differences.append(z3.Extract(relation.inputs[port]-1, 0, term) != bits)
            for phase_index, clock in enumerate((0, int(active[index]), 0)):
                pins = {**row, 'a': address if row['a'] is None else row['a'],
                        'clk': clock, 'rst': int(resets[index]), **mem_pins}
                state, _out, driven_hits, _pins, _marks = _step(machine, state, 0, **pins)
                state, reached = _settle(machine, state)
                hits.update(driven_hits | reached)
                phase = encoded.slots[index].phases[phase_index]
                differences.append(phase.state[names['q']] != state[names['q']])
                differences.append(phase.hits[goal] != (goal in driven_hits | reached))
                if phase_index == 0:
                    sampled = {port: state[names[port]] for port in relation.outputs}
            memory_state = advance_memory_environment((memory,), environment, memory_state, 2+index,
                sampled, active_domains=slot.active_domains, reset_domains=slot.reset_domains)
            assert encoded.memory.state_image(model, index+1) == memory_state.as_dict()
            differences.extend(z3.Not(term) for term in encoded.memory.state_constraints(index+1,
                                                                                       memory_state.as_dict()))
        assert z3.is_true(model.eval(encoded.hits[goal])) == (goal in hits)
        # Check every enabled source order, not just a convenient SAT model.
        solver.add(z3.Or(*differences, z3.Not(encoded.alive)))
        assert solver.check() == z3.unsat
        solver.pop()


@pytest.mark.parametrize('when', ['control', 'clock'])
def test_terminal_prefix_does_not_shift_pending_reads_or_write_later_slots(tmp_path, when):
    failing = ('always @* if(iv) $fatal(1,"stop");' if when == 'control' else
               'always @(posedge clk) if(iv) $fatal(1,"stop");')
    source = SOURCE.replace('endmodule', failing+'\nendmodule')
    machine, plan, env, template = _prepare(tmp_path, source)
    goal = _goal(source, plan, "ram_resp_rdata==8'hc9")
    memory = MemoryInterface('ram', 2, 8, 'c', [MemoryPort('rw')])
    environment = MemoryEnvironment([MemoryRun('ram', initial={1: 7}, read_latency=2,
                                               ready={None: [1]*5})])
    initial_memory = MemoryEnvironmentState.initial((memory,), environment)
    initial_memory = advance_memory_environment((memory,), environment, initial_memory, 0,
        {'ram_req_valid': 1, 'ram_req_addr': 1, 'ram_req_we': 0, 'ram_req_wdata': 0})
    relation = SourceMemoryRelation(template, machine, (memory,), (goal,))
    initial = machine.boundary({name: 0 for name in machine.program.fields})
    rows = [{'iv': 1, 'a': 2, 'we': 1, 'd': 201}]*3
    slots = _slots(machine, env, rows, relation)
    encoded = relation.encode(_encoded_state(machine, env, initial), slots, environment,
                              initial_state=initial_memory, slot_offset=1)
    solver = z3.Solver()
    solver.add(*encoded.constraints, *encoded.memory.validity_constraints)
    assert solver.check() == z3.sat
    model = solver.model()
    assert z3.is_false(model.eval(encoded.alive))
    boundary = encoded.memory.state_image(model, 1)
    assert all(encoded.memory.state_image(model, i) == boundary for i in (2, 3))
    if when == 'control':
        assert boundary == initial_memory.as_dict()
    else:
        assert dict(MemoryEnvironmentState.from_dict(boundary).state('ram').writes) == {2: 201}
    solver.add(z3.Or(*(encoded.slots[i].hits[goal] for i in (1, 2))))
    assert solver.check() == z3.unsat


def test_separate_read_and_write_ports_keep_read_first_order(tmp_path):
    source = '''module M(input clk,rst,iv,we,input [1:0] a,input [7:0] d,
      output ram__r_req_valid,ram__w_req_valid,
      output [1:0] ram__r_req_addr,ram__w_req_addr,output [7:0] ram__w_req_wdata,
      input ram__r_req_ready,ram__w_req_ready,ram__r_resp_valid,
      input [7:0] ram__r_resp_rdata,output reg [7:0] q);
      assign ram__r_req_valid=iv && !rst;
      assign ram__w_req_valid=we && !rst;
      assign ram__r_req_addr=a;
      assign ram__w_req_addr=a;
      assign ram__w_req_wdata=d;
      always @(posedge clk) if(rst) q<=0; else if(ram__r_resp_valid) begin
        if(ram__r_resp_rdata==201) q<=201; else q<=ram__r_resp_rdata;
      end
    endmodule'''
    machine, plan, env, template = _prepare(tmp_path, source)
    goal = _goal(source, plan, 'ram__r_resp_rdata==201')
    memory = MemoryInterface('ram', 2, 8, 'c', [MemoryPort('r','r'), MemoryPort('w','w')])
    environment = MemoryEnvironment([MemoryRun('ram', initial={2:17}, read_latency=1,
                                               ready={'r':[1]*4, 'w':[1]*4})])
    relation = SourceMemoryRelation(template, machine, (memory,), (goal,),
                                     state_reads=tuple(machine.program.fields))
    initial = machine.boundary({name:0 for name in machine.program.fields})
    rows = [{'iv':int(i<2), 'we':int(i==0), 'a':2, 'd':201} for i in range(4)]
    encoded = relation.encode(_encoded_state(machine, env, initial), _slots(machine, env, rows, relation),
        environment, initial_state=MemoryEnvironmentState.initial((memory,), environment))
    solver = z3.Solver()
    solver.add(*encoded.constraints, *encoded.memory.validity_constraints)
    assert solver.check() == z3.sat
    names = _names(machine)
    # Simultaneous write cannot change the value snapshotted by that read.
    solver.add(z3.Or(encoded.slots[1].phases[0].inputs[names['ram__r_resp_rdata']] != 17,
                     encoded.slots[2].phases[0].inputs[names['ram__r_resp_rdata']] != 201,
                     encoded.slots[1].hits[goal], z3.Not(encoded.slots[2].hits[goal])))
    assert solver.check() == z3.unsat


def test_request_is_sampled_before_clock_commit_not_from_its_successor(tmp_path):
    source = SOURCE.replace('assign ram_req_addr=a;',
        'reg [1:0] request_a;\n assign ram_req_addr=request_a;\n'
        'always @(posedge clk) if(rst) request_a<=0; else request_a<=a;')
    machine, plan, env, template = _prepare(tmp_path, source)
    goal = _goal(source, plan, "ram_resp_rdata==8'hc9")
    memory = MemoryInterface('ram', 2, 8, 'c', [MemoryPort('rw')])
    environment = MemoryEnvironment([MemoryRun('ram', initial={0:7,1:17,2:201}, read_latency=1,
                                               ready={None:[1]*4})])
    relation = SourceMemoryRelation(template, machine, (memory,), (goal,),
                                     state_reads=tuple(machine.program.fields))
    initial = machine.boundary({name:0 for name in machine.program.fields})
    rows = [{'iv':int(i<2), 'we':0, 'a':2 if i==0 else 1, 'd':0} for i in range(4)]
    encoded = relation.encode(_encoded_state(machine, env, initial), _slots(machine, env, rows, relation),
        environment, initial_state=MemoryEnvironmentState.initial((memory,), environment))
    solver = z3.Solver()
    solver.add(*encoded.constraints, *encoded.memory.validity_constraints)
    assert solver.check() == z3.sat
    names = _names(machine)
    solver.add(z3.Or(encoded.slots[0].phases[0].state[names['ram_req_addr']] != 0,
                     encoded.slots[0].state[names['ram_req_addr']] != 2,
                     encoded.slots[1].phases[0].inputs[names['ram_resp_rdata']] != 7,
                     encoded.slots[2].phases[0].inputs[names['ram_resp_rdata']] != 201,
                     encoded.slots[1].hits[goal], z3.Not(encoded.slots[2].hits[goal])))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('symbolic_resets', [False, True])
def test_independent_memory_domains_signed_pins_and_off_edge_reset(tmp_path, symbolic_resets):
    source = '''module M(input ca,cb,ra,rb,iv,input [1:0] a,
      output left_req_valid,right_req_valid,output [1:0] left_req_addr,right_req_addr,
      input left_req_ready,right_req_ready,left_resp_valid,right_resp_valid,
      input signed [7:0] left_resp_rdata,input [7:0] right_resp_rdata,
      output reg [7:0] qa,qb);
      assign left_req_valid=iv && !ra;
      assign right_req_valid=iv && !rb;
      assign left_req_addr=a;
      assign right_req_addr=a;
      always @(posedge ca or posedge ra) if(ra) qa<=0; else if(left_resp_valid) begin
        if(left_resp_rdata==-55) qa<=left_resp_rdata; else qa<=left_resp_rdata;
      end
      always @(posedge cb or posedge rb) if(rb) qb<=0; else if(right_resp_valid) begin
        if(right_resp_rdata==201) qb<=right_resp_rdata; else qb<=right_resp_rdata;
      end
    endmodule'''
    machine, plan, env, template = _prepare(tmp_path, source)
    goals = tuple(_goal(source, plan, text) for text in ('left_resp_rdata==-55','right_resp_rdata==201'))
    memories = tuple(MemoryInterface(name, 2, 8, domain, [MemoryPort('r')])
                     for name, domain in (('left','A'),('right','B')))
    environment = MemoryEnvironment([MemoryRun(memory.name, initial={1:201}, read_latency=2,
                                               ready={None:[1]*5}) for memory in memories])
    relation = SourceMemoryRelation(template, machine, memories, goals, context=z3.Context(),
                                     state_reads=tuple(machine.program.fields))
    names, kinds = _names(machine), dict(machine.procedure.semantics.input_types)
    driven = dict(machine.drive_inputs)
    initial = machine.boundary({name:0 for name in machine.program.fields})
    active = [('A','B'), ('A',), (), ('B',), ('A','B')]
    reset = [(), (), ('B',), (), ()]
    reset_terms = ({domain: tuple(z3.Bool(f'{domain}_reset_{i}', ctx=relation.relation.source.context)
                                 for i in range(5)) for domain in ('A', 'B')}
                   if symbolic_resets else None)
    slots = []
    for index, domains in enumerate(active):
        images = tuple({'ca':int(edge and 'A' in domains), 'cb':int(edge and 'B' in domains),
                        'ra':0, 'rb':int('B' in reset[index]), 'iv':int(index==0), 'a':1}
                       for edge in (False,True,False))
        phases = tuple({names[port]:encode_concrete_value(value, env[driven[names[port]]],
                    driven[names[port]], kind=kinds[driven[names[port]]])
                    for port,value in image.items()} for image in images)
        if reset_terms:
            for phase in phases:
                for domain, pin in (('A', 'ra'), ('B', 'rb')):
                    width = phase[names[pin]].size()
                    context = relation.relation.source.context
                    phase[names[pin]] = z3.If(reset_terms[domain][index],
                        z3.BitVecVal(1, width, ctx=context), z3.BitVecVal(0, width, ctx=context))
        all_pins = dict.fromkeys(driven, 0)
        bounds = machine.phase_event_bounds(all_pins,
            tuple({**all_pins,names['ca']:image['ca'],names['cb']:image['cb']} for image in images),
            set(driven)-{names['ca'],names['cb']}, machine.event_bound)
        slots.append(SourceMemorySlot(phases,bounds,domains,() if reset_terms else reset[index]))
    memory_state = MemoryEnvironmentState.initial(memories, environment)
    encoded = relation.encode(_encoded_state(machine, env, initial), slots, environment,
                              initial_state=memory_state, reset_terms=reset_terms)
    solver = z3.Solver(ctx=relation.relation.source.context)
    solver.add(*encoded.constraints, *encoded.memory.validity_constraints)
    if reset_terms:
        solver.add(*(term == (domain in reset[i]) for domain, row in reset_terms.items()
                     for i, term in enumerate(row)))
    assert solver.check() == z3.sat
    model = solver.model()
    for index in range(5):
        samples = {pin:_raw(model, encoded.slots[index].phases[0].state[names[pin]], width)
                   for pin,width in relation.outputs.items()}
        memory_state = advance_memory_environment(memories, environment, memory_state, index, samples,
            active_domains=active[index], reset_domains=reset[index])
        assert encoded.memory.state_image(model,index+1) == memory_state.as_dict()
    solver.add(z3.Or(z3.Not(encoded.hits[goals[0]]), encoded.hits[goals[1]],
                     encoded.state[names['qa']] != 201, encoded.state[names['qb']] != 0))
    assert solver.check() == z3.unsat


@pytest.mark.cosim
@pytest.mark.parametrize('latency', [1, 3])
def test_changed_request_yields_future_native_goal_and_public_comparison(tmp_path, latency):
    source = '''module M(input clk,rst,iv,input [1:0] a,
      output reg ov,output reg [7:0] y,output ram_req_valid,output [1:0] ram_req_addr,
      input ram_req_ready,ram_resp_valid,input [7:0] ram_resp_rdata);
      assign ram_req_valid=iv && !rst;
      assign ram_req_addr=a;
      always @(posedge clk) begin
        if(rst) begin ov<=0; y<=0; end else begin
          ov<=ram_resp_valid;
          if(ram_resp_valid) begin
            if(ram_resp_rdata==201) y<=ram_resp_rdata; else y<=ram_resp_rdata;
          end
        end
      end
    endmodule'''
    machine, plan, env, template = _prepare(tmp_path, source)
    memory = MemoryInterface('ram', 2, 8, 'c', [MemoryPort('r')])
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', api.FiniteType(2), 'a')],
                     api.sampled_edge('in', valid='iv'), 'c', kind=api.Kind.TRANSACTION),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(8), 'y')],
                     api.sampled_edge('out', valid='ov'), 'c', kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1)],
        [api.ClockDomain('c', 'clk', 'rst', reset_restores=True)],
        protocol_instances=[api.ProtocolInstance('compute', ('in', 'out'))], memories=(memory,))
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER, {'behavior.py': b'''class M:
 def reset(self): pass
 def process(self,a): return {"y": [7,17,201,3][a]}
'''}, config={'class': 'M'})
    task = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(tmp_path/'design.sv', 'M', tmp_path/'build'))
    seed = api.Stimulus({'in': [(1,)]}, [api.EventSlot(reset=i==0, offers=('in',) if i==1 else (),
        controls={'rst': int(i==0)}) for i in range(latency+5)],
        memory_environment=MemoryEnvironment([MemoryRun('ram', initial={0:7,1:17,2:201,3:3},
            read_latency=latency, ready={None: [1]*(latency+5)})]))
    baseline = handle.replay((seed,), reference=reference).evaluation
    assert baseline.complete and baseline.passed
    trace = baseline.results[0].trace
    goal = _goal(source, plan, 'ram_resp_rdata==201')
    assert not any(hit for row in trace.records for key, hit in row.hits if key == goal)
    binding = SourceTraceBindings(machine, contract, trace.stimulus, trace)
    relation = SourceMemoryRelation(template, machine, (memory,), (goal,), context=z3.Context())
    query = binding.at((StimulusField('in', 0, 0, binding.releases['in',0]),),
                       state_names=relation.relation.source.state_names)
    names = _names(machine)
    rows = [{'iv': int(i==0), 'a': None if i==0 else 0} for i in range(latency+3)]
    slots = _slots(machine, env, rows, relation)
    memory_state = MemoryEnvironmentState.initial((memory,), seed.memory_environment)
    encoded = relation.encode(_encoded_state(machine, env, query.state), slots,
        seed.memory_environment, initial_state=memory_state, slot_offset=query.slot)
    solver = z3.Solver(ctx=relation.relation.source.context)
    solver.add(*encoded.constraints, *encoded.memory.validity_constraints, encoded.hits[goal])
    assert solver.check() == z3.sat
    model = solver.model()
    candidate = {names['a']: model.eval(encoded.slots[0].phases[0].inputs[names['a']]).as_long()}
    assert candidate == {names['a']: 2}
    assert all(z3.is_false(model.eval(slot.hits[goal])) for slot in encoded.slots[:latency])
    replay = handle.replay((query.proposal(candidate),), reference=reference).evaluation
    assert replay.complete and replay.passed
    witness = replay.results[0].trace
    assert any(hit for row in witness.records for key, hit in row.hits if key == goal)
    # A fixed seed response would produce 17, never this SAT/native result.
    solver.add(encoded.slots[latency].phases[0].inputs[names['ram_resp_rdata']] == 17)
    assert solver.check() == z3.unsat


def test_response_pins_are_owned_by_the_product_and_construction_is_cancellable(tmp_path):
    machine, plan, env, template = _prepare(tmp_path)
    memory = MemoryInterface('ram', 2, 8, 'c', [MemoryPort('rw')])
    environment = MemoryEnvironment([MemoryRun('ram', ready={None: [1]})])
    relation = SourceMemoryRelation(template, machine, (memory,),
        (_goal(SOURCE, plan, "ram_resp_rdata==8'hc9"),))
    state = _encoded_state(machine, env, machine.boundary({name: 0 for name in machine.program.fields}))
    slots = _slots(machine, env, [{'iv':1, 'a':0, 'we':0, 'd':0}], relation)
    memory_state = MemoryEnvironmentState.initial((memory,), environment)
    bad = replace(slots[0], phases=tuple({**phase, relation.ports['ram_resp_valid']: None}
                                       for phase in slots[0].phases))
    with pytest.raises(ValueError, match='exactly the non-memory'):
        relation.encode(state, (bad,), environment, initial_state=memory_state)
    with pytest.raises(TypeError, match='explicit complete memory boundary'):
        relation.encode(state, slots, environment, initial_state=None)
    def cancelled():
        raise TimeoutError('owner deadline')
    with pytest.raises(TimeoutError, match='owner deadline'):
        relation.encode(state, slots, environment, initial_state=memory_state, check=cancelled)
