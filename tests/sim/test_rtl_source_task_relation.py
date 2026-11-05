"""Mutated payloads drive new handshakes, queue heads and delayed native hits."""

import pytest
import z3

from behave.sim import api
from behave.sim.core.memory import MemoryEnvironment, MemoryEnvironmentState, MemoryInterface, MemoryPort, MemoryRun
from behave.sim.engines import probing
from behave.sim.engines.rtl.generation import RTLGenerationProvider
from behave.sim.rtl.protocol_relation import ProtocolBoundary
from behave.sim.rtl.source_task import SourceTaskRelation
from behave.sim.stimulus_set import GenerationSpec

from .test_protocol_interval_relation import _packed
from .test_rtl_source_memory_relation import _encoded_state, _prepare
from .test_rtl_source_query import _goal

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]

SOURCE = '''module M(input clk,rst,iv,input [1:0] a,output ir,
  output reg ov,output reg [7:0] y,input ordy,
  output ram_req_valid,output [1:0] ram_req_addr,
  input ram_req_ready,ram_resp_valid,input [7:0] ram_resp_rdata);
  reg busy; reg [1:0] delay_count;
  assign ir=!rst && !busy && (!ov || ordy) && ram_req_ready && (a!=2 || delay_count==3);
  assign ram_req_valid=iv && ir;
  assign ram_req_addr=a;
  always @(posedge clk) begin
    if(rst) begin busy<=0; ov<=0; y<=0; delay_count<=0; end else begin
      if(iv && !ir) delay_count<=delay_count+1; else delay_count<=0;
      if(ram_req_valid) busy<=1;
      if(ov && ordy) ov<=0;
      if(ram_resp_valid) begin
        busy<=0; ov<=1;
        if(ram_resp_rdata==201) y<=ram_resp_rdata; else y<=ram_resp_rdata;
      end
    end
  end
endmodule'''


def _contract():
    return api.TaskContract([
        api.Endpoint('in','in',[api.Field('a',api.FiniteType(2),'a')],
                     api.ready_valid('in','iv','ir'),'c',kind='transaction'),
        api.Endpoint('out','out',[api.Field('y',api.FiniteType(8),'y')],
                     api.ready_valid('out','ov','ordy'),'c',kind='transaction',answers_per_transaction=1)],
        [api.ClockDomain('c','clk','rst',reset_restores=True)],
        memories=(MemoryInterface('ram',2,8,'c',[MemoryPort('r')]),))


@pytest.mark.cosim
@pytest.mark.parametrize('latency,transaction,scheduled',[
    (1,0,False), (3,0,True), (1,1,True), (3,1,False)])
def test_changed_queue_cell_changes_later_acceptance_and_native_hit(tmp_path, latency, transaction, scheduled):
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule

    _machine, plan, _env, _template = _prepare(tmp_path, SOURCE)
    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER, {'behavior.py':b'''class M:
 def reset(self): pass
 def process(self,a): return {"y":[7,17,201,3][a]}
'''},config={'class':'M'})
    task = api.admit(contract,api.realization_of_rtl_source(SOURCE,top='M',contract=contract),reference=reference)
    handle = api.prepare_rtl(task,api.RTLConfig(tmp_path/'design.sv','M',tmp_path/'build'))
    slots = [api.EventSlot(reset=i==0 and not scheduled,offers=('in',) if i in (1,10) else (),
        ticks=('c',) if scheduled else (), resets=('c',) if scheduled and i==0 else (),
        controls={'rst':int(i==0),'ordy':int(i>=10 or i%4 != 3)}) for i in range(24)]
    schedule = (GlobalSchedule([ClockTiming('c',2,clock='clk',reset='rst')],
        [GlobalEventSlot(2*i,event=slot) for i,slot in enumerate(slots)]) if scheduled else None)
    seed = api.Stimulus({'in':[(1,),(1,)]}, slots, clock_schedule=schedule,
        memory_environment=MemoryEnvironment([MemoryRun('ram',initial={0:7,1:17,2:201,3:3},
            read_latency=latency,ready={None:[1]*24})]))
    baseline = handle.replay((seed,),reference=reference).evaluation
    assert baseline.complete and baseline.passed, baseline.as_dict()
    trace = baseline.results[0].trace
    goal = _goal(SOURCE,plan,'ram_resp_rdata==201')
    assert goal in trace.records[0].hits.catalogue
    assert not any(dict(row.hits).get(goal,False) for row in trace.records)
    provider = RTLGenerationProvider.from_artifact(handle._artifact, contract,
        GenerationSpec(24,2,1,3),handle._frontend_catalogue, targetable_goals=(goal,),
        workdir=tmp_path/'provider',timeout_ms=60000)
    selection = provider.select(trace.stimulus)
    assert isinstance(selection,api.ProbeSelection), selection
    request = next(request for request in selection.requests
                   if request.mutable_fields[0].transaction == transaction)
    # The ordinary public provider must retain [first presentation, next
    # presentation), not silently clip source queries to the first clock slot.
    segment = provider._selected[next(iter(provider._selected))][request.occurrence][0]
    assert (segment.start,segment.stop) == ((1,10) if transaction==0 else (10,24))
    result = provider.probe(trace.stimulus,request)
    assert result.outcome is probing.Outcome.SAT, result.detail
    # Force repeated suspension inside the real source/protocol/memory product,
    # then solve and natively confirm the same changed-handshake question.
    from behave.sim.engines.rtl.source_boundary import SourceTraceBindings
    from behave.sim.engines.rtl.source_task_query import SourceTaskQuery, query_source_task
    binding = SourceTraceBindings(_machine, contract, trace.stimulus, trace)
    owner = SourceTaskQuery(_machine, _template, binding, request, segment)
    pauses, checkpoints = 0, 0
    def checkpoint():
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints % 97 == 0:
            raise TimeoutError('test quantum')
    while owner.product is None:
        try:
            owner.advance(checkpoint)
        except TimeoutError:
            pauses += 1
            assert not owner.broken and owner.product is None
    assert pauses > 1
    result = query_source_task(_machine, _template, binding, request, segment,
                              timeout_ms=60000, _construction=owner)
    assert result.outcome is probing.Outcome.SAT, result.detail
    expected = [(1,),(1,)]
    expected[transaction] = (2,)
    assert result.goal == goal and dict(result.candidate.queues)['in'] == tuple(expected)
    replay = handle.replay((result.candidate,),reference=reference).evaluation
    assert replay.complete and replay.passed, replay.as_dict()
    witness = replay.results[0].trace
    assert any(dict(row.hits).get(goal,False) for row in witness.records)
    old_accepts = [i for i,row in enumerate(trace.records) if dict(row.accepted).get('in') is not None]
    new_accepts = [i for i,row in enumerate(witness.records) if dict(row.accepted).get('in') is not None]
    assert new_accepts != old_accepts
    assert any(dict(row.hits).get(goal,False) for row in witness.records[segment.start:segment.stop])


@pytest.mark.parametrize('phase',['control','clock'])
def test_terminal_event_does_not_disable_its_own_input_or_restart_after_reset(tmp_path, phase):
    trigger = 'always @*' if phase == 'control' else 'always @(posedge clk)'
    source = SOURCE.replace('endmodule',f'{trigger} if(iv) $fatal(1,"stop"); endmodule')
    machine, plan, env, template = _prepare(tmp_path,source)
    goal = _goal(source,plan,'ram_resp_rdata==201')
    contract, context = _contract(), z3.Context()
    relation = SourceTaskRelation(template,machine,contract,(goal,),context=context)
    slots = [api.EventSlot(offers=('in',) if i==0 else (),reset=i==1,
                          controls={'rst':int(i==1),'ordy':1}) for i in range(3)]
    environment = MemoryEnvironment([MemoryRun('ram',read_latency=1,ready={None:[1]*3})])
    state = machine.boundary({name:0 for name in machine.program.fields})
    enc = relation.encode(_encoded_state(machine,env,state),ProtocolBoundary.initial(contract),
        _packed(contract,{'in':[(1,)]},context),slots,environment,
        memory_state=MemoryEnvironmentState.initial(contract.memories,environment))
    solver = z3.Solver(ctx=context)
    solver.add(*enc.constraints,*enc.source.memory.validity_constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Or(enc.source.alive,enc.source.hits[goal],
                     *(row['in'] for row in enc.protocol.accepted[1:])))
    assert solver.check() == z3.unsat


@pytest.mark.cosim
def test_memory_free_multislot_query_confirms_the_delayed_goal_natively(tmp_path):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output reg ov,output reg [1:0] y,input ordy);
      reg [1:0] saved, count;
      assign ir=!rst && count==0 && (!ov || ordy);
      always @(posedge clk) begin
        if(rst) begin ov<=0; y<=0; saved<=0; count<=0; end else begin
          if(ov && ordy) ov<=0;
          if(iv && ir) begin saved<=a; count<=2; end
          else if(count!=0) begin
            count<=count-1;
            if(count==1) begin
              ov<=1;
              if(saved==3) y<=3; else y<=saved;
            end
          end
        end
      end
    endmodule'''
    contract = api.TaskContract([
        api.Endpoint('in','in',[api.Field('a',api.FiniteType(2),'a')],
            api.ready_valid('in','iv','ir'),'c',kind='transaction'),
        api.Endpoint('out','out',[api.Field('y',api.FiniteType(2),'y')],
            api.ready_valid('out','ov','ordy'),'c',kind='transaction',answers_per_transaction=1)],
        [api.ClockDomain('c','clk','rst',reset_restores=True)])
    _machine, plan, _env, _template = _prepare(tmp_path, source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER, {'behavior.py':b'''class M:
 def reset(self): pass
 def process(self,a): return {"y":a}
'''}, config={'class':'M'})
    task = api.admit(contract,api.realization_of_rtl_source(source,top='M',contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task,api.RTLConfig(tmp_path/'design.sv','M',tmp_path/'build'))
    seed = api.Stimulus({'in':[(1,)]}, [api.EventSlot(reset=i==0,
        offers=('in',) if i==1 else (), controls={'rst':int(i==0),'ordy':1})
        for i in range(9)])
    replay = handle.replay((seed,),reference=reference).evaluation
    assert replay.complete and replay.passed, replay.as_dict()
    trace, goal = replay.results[0].trace, _goal(source,plan,'saved==3')
    assert not any(dict(row.hits).get(goal,False) for row in trace.records)
    provider = RTLGenerationProvider.from_artifact(handle._artifact,contract,
        GenerationSpec(9,1,1,3),handle._frontend_catalogue,targetable_goals=(goal,),
        workdir=tmp_path/'provider',timeout_ms=60000)
    selected = provider.select(trace.stimulus)
    assert isinstance(selected,api.ProbeSelection), selected
    request = selected.requests[0]
    segment = provider._selected[next(iter(provider._selected))][request.occurrence][0]
    assert segment.stop > segment.start+1
    try:
        answer = provider.probe(trace.stimulus,request)
    except api.Refused as exc:
        pytest.fail(str(exc.refusal.as_dict()))
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.goal == goal and dict(answer.candidate.queues)['in'] == ((3,),)
    confirmed = handle.replay((answer.candidate,),reference=reference).evaluation
    assert confirmed.complete and confirmed.passed, confirmed.as_dict()
    assert any(dict(row.hits).get(goal,False) for row in confirmed.results[0].trace.records)
