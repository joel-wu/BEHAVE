"""Source-local input proposals start from actual Driver/native trace values."""

import pytest

from behave.sim import api
from behave.sim.symbolic import ranges
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_boundary import SourceTraceBindings
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.generation import StimulusField

from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver,
              pytest.mark.usefixtures("search_lifetime")]


def _framing_binding(tmp_path, *, framed=True):
    from .test_input_framing import contract, nested

    source = '''module M(input clk,rst,valid,out_ready, input [7:0] pixel,
        input sof,eof,sol,eol, output ready,out_valid, output [7:0] out_pixel);
        assign ready=1; assign out_valid=0; assign out_pixel=pixel;
    endmodule'''
    machine, _ = _lower(tmp_path, source)
    task = contract(nested(2, 3) if framed else None)
    payloads = [(5,1,0,1,0), (6,0,0,0,1), (7,1,0,1,0), (8,0,1,0,1)]
    positions = {0:payloads[0], 2:payloads[1], 4:payloads[2], 5:payloads[3]}
    seed = api.Stimulus({'in':payloads}, [api.EventSlot(
        reset=i == 3, offers=('in',) if i in positions else (),
        controls={'rst':int(i == 3), 'out_ready':1}) for i in range(7)])
    trace = api.AuditedTrace(seed, records=[api.SlotRecord(
        accepted={'in':positions[i]} if i in positions else {},
        outputs={'ready':1, 'out_valid':0, 'out_pixel':0}) for i in range(7)])
    return SourceTraceBindings(machine, task, seed, trace)


def test_prefix_resume_preserves_frames_reset_and_backward_selection(tmp_path, monkeypatch):
    from behave.sim.core.input_framing import InputDomainCursor

    binding = _framing_binding(tmp_path)
    advance, consumed = InputDomainCursor.advance, []

    def counted(self, slot, accepted, *, index):
        consumed.append(index)
        return advance(self, slot, accepted, index=index)

    monkeypatch.setattr(InputDomainCursor, 'advance', counted)
    for start, expected_indexes in ((2,[0,1]), (5,[2,3,4]), (6,[5]), (1,[0]), (5,[1,2,3,4])):
        consumed.clear()
        actual, _ = binding.product_boundary(start)
        assert consumed == expected_indexes
        independent = InputDomainCursor(binding.contract)
        for index in range(start):
            advance(independent, binding.seed.slots[index],
                    dict(binding.trace.records[index].accepted), index=index)
        assert actual.framing == independent.states


def test_cancelled_prefix_does_not_publish_partial_frame_state(tmp_path):
    binding = _framing_binding(tmp_path)
    binding.product_boundary(2)
    completed = binding._prefix
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        if checks == 4:  # adapter lookup, then rows 2 and 3, before row 4
            raise TimeoutError('diagnostic cancellation')

    with pytest.raises(TimeoutError, match='diagnostic cancellation'):
        binding.product_boundary(5, check=cancel)
    assert binding._prefix is completed
    assert completed[1].framing == {'in': (0, 1)}
    actual, _ = binding.product_boundary(5)
    assert actual.framing == {'in': (0, 1)}


def test_unframed_memoryless_prefix_needs_no_record_walk(tmp_path, monkeypatch):
    from behave.sim.core.input_framing import InputDomainCursor

    binding = _framing_binding(tmp_path, framed=False)
    monkeypatch.setattr(InputDomainCursor, 'advance', lambda *a, **k: pytest.fail(
        'an empty prefix product must not walk completed records'))
    for start in (2, 5, 1):
        boundary, memory = binding.product_boundary(start)
        assert boundary.framing == {} and not memory.memories


def _contract(clock=None, *, signed=False):
    kind = api.FiniteType(4, signed=signed)
    adapter = api.settled if clock is None else api.sampled_edge
    return api.TaskContract([
        api.Endpoint("input", "in", [api.Field("payload", kind, "a")], adapter("in"),
                     None if clock is None else "c", kind=api.Kind.TRANSACTION),
        api.Endpoint("output", "out", [api.Field("result", kind, "y")], adapter("out"),
                     None if clock is None else "c", kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1)],
        [] if clock is None else [api.ClockDomain("c", "clk", "rst", edge=clock)],
        protocol_instances=[api.ProtocolInstance("compute", ("input", "output"))])


@pytest.mark.parametrize("edge,scheduled", [
    (None, False), ("rising", False), ("falling", False),
    ("rising", True), ("falling", True),
])
def test_boundary_uses_successor_not_clocked_samples_and_preserves_signed_cells(tmp_path, edge, scheduled):
    source = ("module M(input signed [3:0] a,output reg signed [3:0] y); always_comb y=a; endmodule"
              if edge is None else
              f"module M(input clk,rst,input signed [3:0] a,output reg signed [3:0] y); "
              f"always @({'posedge' if edge == 'rising' else 'negedge'} clk) y<=a; endmodule")
    machine, _ = _lower(tmp_path, source)
    task = _contract(edge, signed=True)
    controls = {} if edge is None else {"rst": 0}
    slots = [api.EventSlot(offers=("input",), controls=controls,
                          ticks=("c",) if scheduled else ()) for _ in range(2)]
    schedule = None
    if scheduled:
        from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule

        schedule = GlobalSchedule((ClockTiming("c", 10, clock="clk", reset="rst", edge=edge),),
            tuple(GlobalEventSlot(i * 10, event=slot) for i, slot in enumerate(slots)))
    seed = api.Stimulus({"input": [(0,), (1,)]}, slots, clock_schedule=schedule)
    trace = api.AuditedTrace(seed, records=[
        api.SlotRecord(accepted={"input": (value,), "output": (2,)},
                       outputs={"a": value, "y": 2}, state={"y": 15} if edge else {})
        for value in (0, 1)])
    query = SourceTraceBindings(machine, task, seed, trace).at((StimulusField("input", 1, 0, 1),))
    y = next(name for name, cell in machine.program.fields.items() if cell.port == "y")
    assert query.slot == 1
    assert query.state[y] == (-1 if edge else 2)
    assert len(query.phases) == (3 if edge else 1)
    if edge:
        clk = next(name for name, cell in machine.program.fields.items() if cell.port == "clk")
        assert [phase[clk] for phase in query.phases] == ([0, 1, 0] if edge == "rising" else [1, 0, 1])
    pin, = query.mutable_inputs
    proposal = query.proposal({pin: -3})
    assert dict(proposal.queues)["input"] == ((0,), (-3,))
    assert dict(seed.queues)["input"] == ((0,), (1,))
    assert proposal.slots == seed.slots


def test_missing_internal_boundary_is_not_filled_from_samples_or_zero(tmp_path):
    source = """module M(input clk,rst,input [3:0] a,output reg [3:0] y);
      reg [3:0] x;
      always @(posedge clk) begin x<=a; y<=x; end
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    task = _contract("rising")
    seed = api.Stimulus({"input": [(0,), (1,)]}, [
        api.EventSlot(offers=("input",), controls={"rst": 0}) for _ in range(2)])
    records = [api.SlotRecord(accepted={"input": (a,)}, outputs={"a": a, "y": 4, "x": 9}, state={"y": 7})
               for a in (0, 1)]
    trace = api.AuditedTrace(seed, records=records)
    with pytest.raises(Refused, match="rtl-source-trace-boundary-unavailable"):
        SourceTraceBindings(machine, task, seed, trace).at((StimulusField("input", 1, 0, 1),))
    # A genuinely observed internal successor, not its earlier sample, completes
    # the same compiler-bound image without changing the boundary rule.
    trace = api.AuditedTrace(seed, records=[api.SlotRecord(accepted=dict(row.accepted),
        outputs=dict(row.outputs), state={"y": 7, "x": 6}) for row in records])
    query = SourceTraceBindings(machine, task, seed, trace).at((StimulusField("input", 1, 0, 1),))
    x = next(name for name, cell in machine.program.fields.items() if cell.observation == "x")
    assert query.state[x] == 6


def test_held_queue_cell_binds_at_presentation_not_earlier_release(tmp_path):
    source = """module M(input clk,rst,in_valid,out_ready,input [3:0] a,
      output wire in_ready,out_valid,output reg [3:0] y);
      assign in_ready=1;
      assign out_valid=0;
      always @(posedge clk) if(in_valid) y<=a;
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    kind = api.FiniteType(4)
    task = api.TaskContract([
        api.Endpoint("input", "in", [api.Field("payload", kind, "a")],
                     api.ready_valid("in", "in_valid", "in_ready"), "c"),
        api.Endpoint("output", "out", [api.Field("result", kind, "y")],
                     api.ready_valid("out", "out_valid", "out_ready"), "c")],
        [api.ClockDomain("c", "clk", "rst")])
    seed = api.Stimulus({"input": [(5,), (7,)]}, [
        api.EventSlot(offers=("input",) if slot < 2 else (), controls={"rst": 0, "out_ready": 1}) for slot in range(4)])
    trace = api.AuditedTrace(seed, records=[api.SlotRecord(
        accepted={"input": (5 if slot == 2 else 7,)} if slot >= 2 else {},
        outputs={"a": 5 if slot < 3 else 7, "y": 0, "in_ready": int(slot >= 2), "out_valid": 0},
        state={"y": 0, "in_ready": 1, "out_valid": 0}) for slot in range(4)])
    # This is a coordinate-mapping fixture, not a claim that these synthetic
    # stall records are executions of the small source design above.
    query = SourceTraceBindings(machine, task, seed, trace).at((StimulusField("input", 1, 0, 1),))
    a, = query.mutable_inputs
    assert query.slot == 3 and query.state[a] == 5
    assert all(phase[a] == 7 for phase in query.phases)
    with pytest.raises(Refused, match="rtl-source-trace-release-mismatch"):
        SourceTraceBindings(machine, task, seed, trace).at((StimulusField("input", 1, 0, 3),))


@pytest.mark.cosim
@pytest.mark.parametrize("clocked", [False, True])
@pytest.mark.parametrize("scoped", [False, True])
def test_public_baseline_record_to_solver_proposal_to_native_confirmed_hit(tmp_path, monkeypatch, clocked, scoped):
    from behave.sim.rtl.artifact import RTLArtifact

    source = """module M(input [3:0] a,output reg [3:0] y);
      always_comb if(a==9) y=a; else y=a;
    endmodule"""
    contract = _contract()
    if clocked:
        source = """module M(input clk,rst,in_valid,input [3:0] a,output reg out_valid,output reg [3:0] y);
          always @(posedge clk) begin
            if(rst) begin out_valid<=0; y<=0; end
            else begin
              out_valid<=in_valid;
              if(in_valid) begin if(a==9) y<=a; else y<=a; end
            end
          end
        endmodule"""
        kind = api.FiniteType(4)
        contract = api.TaskContract([
            api.Endpoint("input", "in", [api.Field("payload", kind, "a")],
                         api.sampled_edge("in", valid="in_valid"), "c", kind=api.Kind.TRANSACTION),
            api.Endpoint("output", "out", [api.Field("result", kind, "y")],
                         api.sampled_edge("out", valid="out_valid"), "c", kind=api.Kind.TRANSACTION,
                         answers_per_transaction=1)],
            [api.ClockDomain("c", "clk", "rst", reset_restores=True)],
            protocol_instances=[api.ProtocolInstance("compute", ("input", "output"))])
    if scoped:
        if clocked:
            source = source.replace("@(posedge clk) begin", "@(posedge clk) begin: history\n reg [3:0] previous;")
            source = source.replace("out_valid<=0; y<=0;", "out_valid<=0; y<=0; previous<=0;")
            source = source.replace("if(a==9) y<=a; else y<=a;",
                "previous<=a; if(a==9 && previous==0) y<=a; else y<=a;")
        else:
            source = source.replace("always_comb if(a==9) y=a; else y=a;",
                "always_comb begin: history\n reg [3:0] previous;\n"
                "previous=a; if(a==9) y=previous; else y=previous; end")
    machine, plan = _lower(tmp_path, source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b'class M:\n def reset(self): pass\n def process(self,a): return {"y":a}\n'},
        config={"class": "M"})
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(tmp_path / "design.sv", "M", tmp_path / "build"))
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=1))
    assert len(baseline.stimuli) == 1 and baseline.stimuli[0].depth("input") == 100
    calls, original = [], RTLArtifact._traces_per_process

    def counted(artifact, bound, seeds, *args, **kwargs):
        seeds = tuple(seeds)
        calls.append(seeds)
        return original(artifact, bound, seeds, *args, **kwargs)

    monkeypatch.setattr(RTLArtifact, "_traces_per_process", counted)
    baseline_result = handle.replay(baseline.stimuli, reference=reference).evaluation
    if not baseline_result.complete:
        pytest.fail(str([run.run_failure.as_dict() for run in baseline_result.results if run.run_failure]))
    assert baseline_result.complete and baseline_result.passed, baseline_result.as_dict()
    trace = baseline_result.results[0].trace
    before = trace.as_dict()
    goal = _goal(source, plan, "a==9")
    assert goal in handle.frontend_catalogue
    assert not any(hit for row in trace.records for name, hit in row.hits if name == goal)
    binding = SourceTraceBindings(machine, contract, trace.stimulus, trace)
    query = binding.at((StimulusField("input", 1, 0, binding.releases["input", 1]),))
    if scoped:
        from behave.sim.rtl.source_boundary_capture import boundaries_of

        name = next(name for name, field in machine.program.fields.items()
                    if field.observation == ("history", "previous"))
        assert boundaries_of(trace.query_boundaries)[query.slot]["history", "previous"] == 0
        assert query.state[name] == 0
    assert len(calls) == 1  # Binding consumed records; it did not replay the seed.
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, query.state, query.phases[0], (goal,),
        following_inputs=query.phases[1:], mutable_inputs=query.mutable_inputs, event_bound=3)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    if scoped and clocked:
        # The static local is history, not a disposable temporary: changing
        # only its bound value must change the exact local reachability answer.
        other = query_source_interval(machine, env, {**query.state, name: 1},
            query.phases[0], (goal,), following_inputs=query.phases[1:],
            mutable_inputs=query.mutable_inputs, event_bound=3)
        assert other.outcome is probing.Outcome.LOCAL_UNSAT, other.detail
    proposal = query.proposal(answer.candidate)
    checked = handle.replay((proposal,), reference=reference).evaluation
    assert checked.complete and checked.passed, checked.as_dict()
    assert any(hit for row in checked.results[0].trace.records for name, hit in row.hits if name == goal)
    assert len(calls) == 2 and calls[-1] == (proposal,)
    assert trace.as_dict() == before
