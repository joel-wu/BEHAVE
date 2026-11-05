"""Recorded memory prefixes reach source queries and native witness replay."""

import json
from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.symbolic import ranges
from behave.sim.core.memory import (
    MemoryEnvironment,
    MemoryInterface,
    MemoryPort,
    MemoryRun,
    drive_memory_environment,
)
from behave.sim.core.memory_trace import final_memory_state
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_boundary import SourceTraceBindings
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.engines.rtl.source_session import PreparedSourceGeneration
from behave.sim.generation import ProbeRequest, StimulusField
from behave.sim.rtl.source_generation import SourceGenerationSession
from behave.sim.rtl.source_relation import SourceIntervalTemplate

from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower

pytestmark = [pytest.mark.cosim, pytest.mark.solver]

SOURCE = """module M(input clk,rst,iv,input [7:0] a,output reg ov,output reg [7:0] y,
  output wire ram_req_valid,output wire [1:0] ram_req_addr,
  output wire ram_req_we,output wire [7:0] ram_req_wdata,
  input ram_req_ready,ram_resp_valid,input [7:0] ram_resp_rdata);
  reg [7:0] previous;
  assign ram_req_valid=iv && !rst;
  assign ram_req_addr=a[5:4];
  assign ram_req_we=a[0];
  assign ram_req_wdata=a;
  always @(posedge clk) begin
    if(rst) begin ov<=0; y<=0; previous<=0; end
    else begin
      ov<=iv;
      if(iv) begin
        previous<=a;
        if(ram_resp_valid && a==ram_resp_rdata && previous==16) y<=a;
        else y<=a;
      end
    end
  end
endmodule"""


def _contract():
    kind = api.FiniteType(8)
    return api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", kind, "a")],
                     api.sampled_edge("in", valid="iv"), "c", kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", kind, "y")],
                     api.sampled_edge("out", valid="ov"), "c", kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1)],
        [api.ClockDomain("c", "clk", "rst", reset_restores=True)],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))],
        memories=[MemoryInterface("ram", 2, 8, "c", [MemoryPort("rw")])])


@pytest.mark.parametrize("latency", [1, 3])
@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("storage", ["scalar", "array", "scoped"])
def test_write_reset_pending_response_query_and_native_hit(tmp_path, latency, scheduled, storage, monkeypatch):
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
    from behave.sim.rtl.source_boundary_capture import boundaries_of

    source = SOURCE
    if storage == "array":
        source = source.replace("reg [7:0] previous;", "reg [7:0] history [0:0];")
        source = source.replace("previous", "history[0]")
    elif storage == "scoped":
        source = source.replace("reg [7:0] previous;", "")
        source = source.replace("@(posedge clk) begin", "@(posedge clk) begin: history\n reg [7:0] previous;")
    machine, plan = _lower(tmp_path, source)
    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": b'''class M:
 def reset(self): self.ram=[0]*4
 def process(self,a):
  if a & 1: self.ram[(a>>4)&3]=a
  return {"y":a}
'''}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(tmp_path/"design.sv", "M", tmp_path/"build"))
    # Write 23 at address 1, drain the output, reset, then read that address.
    # The target input is presented while that read response is visible.
    target = 4+latency
    slots = [api.EventSlot(reset=(i in (0, 3)) and not scheduled,
        offers=("in",) if i in (1, 4, target) else (), controls={"rst": int(i in (0, 3))},
        ticks=("c",) if scheduled else (), resets=("c",) if scheduled and i in (0, 3) else ())
        for i in range(target+latency+3)]
    schedule = (GlobalSchedule([ClockTiming("c", 2, clock="clk", reset="rst")],
        [GlobalEventSlot(2*i, event=slot) for i, slot in enumerate(slots)]) if scheduled else None)
    seed = api.Stimulus({"in": [(23,), (16,), (0,)]}, slots, clock_schedule=schedule,
        memory_environment=MemoryEnvironment([MemoryRun("ram", initial={1: 7},
            read_latency=latency, ready={None: [1]*len(slots)})]))
    checked = handle.replay((seed,), reference=reference).evaluation
    assert checked.complete and checked.passed, checked.as_dict()
    trace = checked.results[0].trace
    before = trace.as_dict()
    goal = _goal(source, plan, "ram_resp_valid && a==ram_resp_rdata")
    assert not any(hit for row in trace.records for name, hit in row.hits if name == goal)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    relation = SourceIntervalTemplate(machine.procedure, env).relation(machine, (goal,))
    binding = SourceTraceBindings(machine, contract, trace.stimulus, trace)
    field = StimulusField("in", 2, 0, binding.releases["in", 2])
    query = binding.at((field,), state_names=relation.source.state_names)
    assert query.slot == target
    observation = ("history", "previous") if storage == "scoped" else (
        "history" if storage == "array" else "previous")
    previous = next(name for name, cell in machine.program.fields.items() if cell.observation == observation)
    expected = [16] if storage == "array" else 16
    assert query.state[previous] == expected
    observed = boundaries_of(trace.query_boundaries)[query.slot][observation]
    assert machine.program.kinds[previous].decode(observed) == expected
    response = next(name for name, cell in machine.program.fields.items() if cell.port == "ram_resp_rdata")
    assert [phase[response] for phase in query.phases] == [23, 23, 23]
    # Continuations recover the same nonzero prefix, including writes which
    # predate reset and a read still pending at the preceding boundary.
    for start in (target-1, target, target-1):
        protocol, memory = binding.product_boundary(start)
        assert dict(memory.state('ram').writes) == {1: 23}
        assert protocol.queues.accepted['in'] == sum(
            dict(record.accepted).get('in') is not None for record in trace.records[:start])
        if start == target:
            assert protocol.queues.released['in'] == 2
            assert drive_memory_environment(contract.memories, seed.memory_environment,
                                            memory, start)['ram_resp_rdata'] == 23

    # Exercise the actual parent/worker wire encoding, not a second handcrafted
    # memory image. No hit ledger or extra native replay is needed to bind.
    worker = object.__new__(PreparedSourceGeneration)
    worker.machine, worker.contract = machine, contract
    def exchange(op, payload, timeout):
        assert op == "bind"
        worker.bind(json.loads(json.dumps(payload)))
    session = SimpleNamespace(_binding=None, _prepare_timeout=17, _exchange=exchange)
    SourceGenerationSession.bind(session, trace.stimulus, trace)
    transported = worker.binding.at((field,), state_names=relation.source.state_names)
    assert transported.state == query.state and transported.phases == query.phases
    original_prefix = binding.product_boundary(target)
    transported_prefix = worker.binding.product_boundary(target)
    assert transported_prefix[0] == original_prefix[0]
    assert transported_prefix[1].as_dict() == original_prefix[1].as_dict()
    answer = query_source_interval(machine, env, query.state, query.phases[0], (goal,),
        following_inputs=query.phases[1:], mutable_inputs=query.mutable_inputs,
        event_bound=machine.event_bound, _relation=relation, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    # The isolated worker must also reach the solver when preparing the same
    # native boundary used its first slice. Resume the retained construction,
    # then replay its actual transported proposal through the public handle.
    from behave.sim.engines.rtl import source_session
    from behave.sim.engines.rtl.generation_job import decode_probe_reply

    worker.env, worker.catalogue = env, checked.catalogue
    worker.template = SourceIntervalTemplate(machine.procedure, env)
    request = ProbeRequest((goal,), 'memory-response', (field,),
                           catalogue_digest=worker.catalogue.digest())
    clock, original_at = [0.0], worker.binding.at

    def delayed_boundary(*args, **kwargs):
        bound = original_at(*args, **kwargs)
        clock[0] += 1
        return bound

    with monkeypatch.context() as patch:
        patch.setattr(source_session.time, 'perf_counter', lambda: clock[0])
        patch.setattr(worker.binding, 'at', delayed_boundary)
        reply = worker.probe(dict(request=request.as_dict(), timeout_ms=1))
        assert reply['outcome'] == 'constructing' and reply['candidate'] is None
        patch.setattr(worker.binding, 'at',
            lambda *args, **kwargs: pytest.fail('resuming must not reconstruct the native boundary'))
        answer = decode_probe_reply(worker.probe(dict(request=request.as_dict(), timeout_ms=30_000)),
                                    expected_catalogue_digest=worker.catalogue.digest())
    assert answer.outcome is probing.Outcome.SAT and answer.goal == goal, answer.detail
    assert worker._interval_progress is None
    proposal = answer.candidate
    assert dict(proposal.queues)["in"] == ((23,), (16,), (23,))
    replayed = handle.replay((proposal,), reference=reference).evaluation
    assert replayed.complete and replayed.passed, replayed.as_dict()
    witness = replayed.results[0].trace
    assert any(hit for row in witness.records for name, hit in row.hits if name == goal)
    assert dict(final_memory_state(contract, witness).state("ram").writes) == {1: 23}
    assert trace.as_dict() == before
