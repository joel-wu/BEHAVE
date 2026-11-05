"""Atomic source queries preserve native history and coincident clock commits."""

import pytest

from behave.sim import api
from behave.sim.symbolic import ranges
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_boundary import SourceTraceBindings
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.generation import StimulusField
from behave.sim.generation_trace import queue_origins
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.source_boundary_capture import boundaries_of

from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver, pytest.mark.cosim,
              pytest.mark.usefixtures("search_lifetime")]


def _fixture(tmp_path, edge):
    source = f'''module M(input ca,cb,ra,rb,va,vb,input [3:0] a,b,
                         output reg [3:0] ya,yb);
      always @(posedge ca or posedge ra) begin
        if(ra) ya<=0;
        else if(va) begin if(a==9 && yb==3) ya<=a; else ya<=a; end
      end
      always @({'posedge' if edge == 'rising' else 'negedge'} cb or posedge rb) begin
        if(rb) yb<=0;
        else if(vb) yb<=b;
      end
    endmodule'''
    clocks = [api.ClockDomain("left", "ca", "ra"),
              api.ClockDomain("right", "cb", "rb", edge=edge)]
    contract = api.TaskContract([
        api.Endpoint("ia", "in", [api.Field("a", api.FiniteType(4), "a")],
                     api.sampled_edge("in", valid="va"), "left", kind=api.Kind.TRANSACTION),
        api.Endpoint("ib", "in", [api.Field("b", api.FiniteType(4), "b")],
                     api.sampled_edge("in", valid="vb"), "right", kind=api.Kind.TRANSACTION),
    ], clocks)
    events = [
        api.EventSlot(ticks=("left", "right"), resets=("left", "right"), controls={"ra": 1, "rb": 1}),
        api.EventSlot(ticks=("right",), offers=("ib",), controls={"ra": 0, "rb": 0}),
        # No clock edge, but the scheduled asynchronous reset still changes.
        api.EventSlot(ticks=(), resets=("left",), controls={"ra": 1, "rb": 0}),
        api.EventSlot(ticks=("left",), offers=("ia",), controls={"ra": 0, "rb": 0}),
        api.EventSlot(ticks=("right",), controls={"ra": 0, "rb": 0}),
        api.EventSlot(ticks=("left", "right"), offers=("ia", "ib"), controls={"ra": 0, "rb": 0}),
        api.EventSlot(ticks=("right",), controls={"ra": 0, "rb": 0}),
    ]
    schedule = GlobalSchedule(
        tuple(ClockTiming(clock.name, 30 if clock.name == "left" else 20,
                          clock=clock.clock, reset=clock.reset, edge=clock.edge)
              for clock in clocks),
        tuple(GlobalEventSlot(time, event=event)
              for time, event in zip((0, 20, 25, 30, 40, 60, 80), events, strict=True)))
    seed = api.Stimulus({"ia": [(0,), (1,)], "ib": [(3,), (7,)]}, events, clock_schedule=schedule)
    machine, plan = _lower(tmp_path, source)
    artifact = RTLArtifact(tmp_path / "design.sv", "M", tmp_path / "replay")
    return source, contract, seed, machine, plan, artifact


@pytest.mark.parametrize("edge", ["rising", "falling"])
def test_atomic_native_boundary_query_and_replay(tmp_path, edge):
    source, contract, seed, machine, plan, artifact = _fixture(tmp_path, edge)
    ports = {field.port: name for name, field in machine.program.fields.items() if field.port}
    # Public replay backend, not fabricated successor values or symbolic hits.
    trace, = artifact.traces_per_process(contract, (seed,), workers=1, native_events=True,
                                         observe_names=tuple(ports))
    assert type(trace) is api.AuditedTrace, trace.as_dict()
    assert trace.completion is api.Completion.NORMAL
    assert len(trace.records) == len(seed.slots)
    target = _goal(source, plan, "a==9 && yb==3")
    assert target in artifact.frontend_catalogue()
    assert not any(dict(row.hits).get(target, False) for row in trace.records)
    captured = boundaries_of(trace.query_boundaries)
    assert set(captured) == {1, 3, 5}
    assert captured[5]["yb"] == 3

    binding = SourceTraceBindings(machine, contract, seed, trace)
    assert {key: origin.presented for key, origin in queue_origins(contract, seed, trace).items()} == binding.presented
    # Compare every reconstructed source input against physical pre-edge reads,
    # including empty slots and held inactive-domain valid pins, not only clocks.
    for pins, record in zip(binding.pins, trace.records, strict=True):
        samples = dict(record.outputs)
        for port, name in ports.items():
            if machine.program.fields[name].input:
                assert pins[name] == samples[port], (port, pins[name], samples[port])
    assert binding.pins[2][ports["vb"]] == 1
    assert binding.pins[2][ports["b"]] == 0
    assert binding.pins[2][ports["ra"]] == 1

    query = binding.at((StimulusField("ia", 1, 0, 5),))
    assert query.slot == 5 and query.state[ports["yb"]] == 3
    assert len(query.phases) == 3
    assert [(p[ports["ca"]], p[ports["cb"]]) for p in query.phases] == (
        [(0, 0), (1, 1), (0, 0)] if edge == "rising" else [(0, 1), (1, 0), (0, 1)])
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, query.state, query.phases[0], (target,),
        following_inputs=query.phases[1:], mutable_inputs=query.mutable_inputs,
        event_bound=machine.event_bound)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate[ports["a"]] == 9
    proposal = query.proposal(answer.candidate)
    assert proposal.clock_schedule == seed.clock_schedule and proposal.slots == seed.slots
    assert dict(proposal.queues)["ib"] == dict(seed.queues)["ib"]
    confirmed, = artifact.traces_per_process(contract, (proposal,), workers=1, native_events=True,
                                             observe_names=("ya", "yb"))
    assert type(confirmed) is api.AuditedTrace, confirmed.as_dict()
    assert confirmed.completion is api.Completion.NORMAL and dict(confirmed.records[5].hits)[target]
    # Both domains see the same prior history: right writes 7 in this slot,
    # while left's branch still reads 3. Serializing right first would miss it.
    assert dict(confirmed.records[6].outputs)["yb"] == 7
    other = query_source_interval(machine, env, {**query.state, ports["yb"]: 7}, query.phases[0], (target,),
        following_inputs=query.phases[1:], mutable_inputs=query.mutable_inputs,
        event_bound=machine.event_bound)
    assert other.outcome is probing.Outcome.LOCAL_UNSAT, other.detail


@pytest.mark.parametrize("edge", ["rising", "falling"])
def test_single_active_domain_does_not_invent_other_clock_edge(tmp_path, edge):
    _source, contract, seed, machine, _plan, artifact = _fixture(tmp_path, edge)
    trace, = artifact.traces_per_process(contract, (seed,), workers=1, native_events=True)
    assert type(trace) is api.AuditedTrace, trace.as_dict()
    binding = SourceTraceBindings(machine, contract, seed, trace)
    ports = {field.port: name for name, field in machine.program.fields.items() if field.port}
    query = binding.at((StimulusField("ia", 0, 0, 3),))
    assert len(query.phases) == 3
    assert [p[ports["ca"]] for p in query.phases] == [0, 1, 0]
    assert [p[ports["cb"]] for p in query.phases] == [int(edge == "falling")] * 3


def test_every_declared_source_goal_matches_exhaustive_atomic_native_replay(tmp_path):
    _source, contract, seed, machine, _plan, artifact = _fixture(tmp_path, "rising")
    trace, = artifact.traces_per_process(contract, (seed,), workers=1)
    assert type(trace) is api.AuditedTrace, trace.as_dict()
    binding = SourceTraceBindings(machine, contract, seed, trace)
    query = binding.at((StimulusField("ia", 1, 0, 5),))
    name, = query.mutable_inputs
    proposals = tuple(query.proposal({name: value}) for value in range(16))
    traces = artifact.traces_per_process(contract, proposals, workers=1)
    assert all(type(row) is api.AuditedTrace for row in traces), [type(row).__name__ for row in traces]
    oracle = [{goal for goal, hit in row.records[5].hits if hit} for row in traces]
    catalogue = artifact.frontend_catalogue()
    assert set(catalogue) == {goal for _mark, _key, goal in machine.occurrences}
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    for goal in catalogue:
        answer = query_source_interval(machine, env, query.state, query.phases[0], (goal,),
            following_inputs=query.phases[1:], mutable_inputs=query.mutable_inputs,
            event_bound=machine.event_bound)
        reachable = any(goal in hits for hits in oracle)
        assert answer.outcome is (probing.Outcome.SAT if reachable else probing.Outcome.LOCAL_UNSAT), (
            goal, answer.detail)
        if reachable:
            assert goal in oracle[answer.candidate[name]]
