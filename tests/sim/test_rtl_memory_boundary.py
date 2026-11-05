"""Local RTL queries retain the complete sparse external-memory state."""


import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.core.frozen import freeze
from behave.sim.core.memory import (
    MemoryEnvironment,
    MemoryEnvironmentState,
    MemoryInterface,
    MemoryPort,
    MemoryRun,
    advance_memory_environment,
)
from behave.sim.engines import probing
from behave.sim.engines.rtl.generation import RTLGenerationProvider


def memory_run(*, horizon=7, clock="clk", width=16):
    memory = MemoryInterface("ram", width, 8, clock, (
        MemoryPort("r", "r"), MemoryPort("w", "w")))
    environment = MemoryEnvironment([MemoryRun(
        "ram", initial={1: 5}, read_latency=3,
        ready={"r": [1] * horizon, "w": [1] * horizon})])
    state = MemoryEnvironmentState.initial((memory,), environment)
    for slot in range(3):
        state = advance_memory_environment((memory,), environment, state, slot, {
            "ram__r_req_valid": 1, "ram__r_req_addr": slot,
            "ram__w_req_valid": int(slot == 0), "ram__w_req_addr": 2,
            "ram__w_req_wdata": 7,
        })
    return memory, environment, state


def scripted_memory(memory, environment, steps, *, reset_slots=()):
    """Independent fixed port observations and their concrete memory trajectory."""
    rows = [{"ram__r_req_valid": 1, "ram__r_req_addr": 1,
             "ram__w_req_valid": 1, "ram__w_req_addr": 2, "ram__w_req_wdata": 7}
            for _ in range(steps)]
    state = MemoryEnvironmentState.initial((memory,), environment)
    states = [state.as_dict()]
    for slot, row in enumerate(rows):
        state = advance_memory_environment(
            (memory,), environment, state, slot, row,
            reset_domains=(memory.clock,) if slot in reset_slots else ())
        states.append(state.as_dict())
    return rows, states


@pytest.mark.parametrize("field,value", [
    ("cells", [[True, 5]]), ("pending", [["r", [[1, 5, True]]]]),
    ("responses", [["r", [1, False]]]), ("writes", [[2, True]]),
])
def test_record_decoder_rejects_coercions_in_both_carriers(field, value):
    _memory, _environment, state = memory_run()
    image = state.as_dict()
    image["memories"][0][1][field] = value
    for record in (image, freeze(image)):
        with pytest.raises((TypeError, ValueError)):
            MemoryEnvironmentState.from_record(record)


def test_record_decoder_rejects_duplicate_record_keys_and_cycles():
    image = memory_run()[2].as_dict()
    with pytest.raises(ValueError):
        MemoryEnvironmentState.from_record(tuple(image.items()) * 2)
    cyclic = {"memories": []}
    cyclic["memories"].append(["ram", cyclic])
    with pytest.raises((TypeError, ValueError)):
        MemoryEnvironmentState.from_record(cyclic)


@pytest.mark.cosim
@pytest.mark.parametrize("atomic", [False, True], ids=["single", "atomic"])
def test_real_memory_provider_crosses_worker_boundary_and_replays_hit(tmp_path, atomic):
    """Actual RTL -> Select -> isolated Probe -> independent RTL hit observation."""
    from behave.sim.core.monitor import monitor_for
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.stimulus_set import GenerationSpec

    memory = MemoryInterface("ram", 16, 4, "clk", [MemoryPort("r")])
    environment = MemoryEnvironment([MemoryRun(
        "ram", initial={1: 5, 2: 9}, read_latency=3, ready={None: [1] * 8})])
    clocks = [api.ClockDomain("clk", "clk", "rst")]
    if atomic:
        clocks.append(api.ClockDomain("aux", "aux_clk", "aux_rst"))
    contract = api.TaskContract([
        api.Endpoint("in", api.Direction.IN,
            [api.Field("data", api.FiniteType(4), "in_data")],
            api.ready_valid(api.Direction.IN, "in_valid", "in_ready"),
            clock="clk", kind=api.Kind.TRANSACTION, response_deadline=0),
        api.Endpoint("out", api.Direction.OUT,
            [api.Field("data", api.FiniteType(4), "out_data")],
            api.ready_valid(api.Direction.OUT, "out_valid", "out_ready"),
            clock="clk", kind=api.Kind.TRANSACTION, answers_per_transaction=1),
    ], clocks, memories=(memory,))
    events = [api.EventSlot(
        offers=("in",) if i in (1, 4) else (), reset=not atomic and i == 0,
        ticks=tuple(clock.name for clock in clocks) if atomic else (),
        resets=tuple(clock.name for clock in clocks) if atomic and i == 0 else (),
        controls={"rst": int(i == 0), "out_ready": 1,
                  **({"aux_rst": int(i == 0)} if atomic else {})}) for i in range(8)]
    schedule = (GlobalSchedule([
        ClockTiming(clock.name, 1, clock=clock.clock, reset=clock.reset)
        for clock in clocks], [GlobalEventSlot(i, event=event)
                              for i, event in enumerate(events)]) if atomic else None)
    seed = api.Stimulus(queues={"in": [(1,), (2,)]}, slots=events,
                        clock_schedule=schedule, memory_environment=environment)
    assert seed.faults_against(contract) == []
    extra_ports = ", input logic aux_clk, input logic aux_rst" if atomic else ""
    # Both products run exactly the same request/response datapath. The extra
    # clock makes the second case use the production atomic product. The state
    # guard is a source port, so this does not require generated-condition
    # observation (a separate, still-open frontend capability).
    rtl = tmp_path / "memory_reader.sv"
    rtl.write_text("""
module memory_reader(
  input logic clk, input logic rst,
  input logic in_valid, output logic in_ready, input logic [3:0] in_data,
  output logic out_valid, input logic out_ready, output logic [3:0] out_data,
  output logic ram_req_valid, output logic [15:0] ram_req_addr,
  input logic ram_req_ready, input logic ram_resp_valid,
  input logic [3:0] ram_resp_rdata EXTRA_PORTS
);
  logic observed;
  initial observed = 1'b0;
  always_ff @(posedge clk) begin
    if (rst) observed <= 1'b0;
    else if (in_valid) observed <= 1'b1;
  end
  assign in_ready = ram_req_ready;
  assign ram_req_valid = !rst && in_valid;
  assign ram_req_addr = {12'b0, in_data};
  assign out_valid = ram_resp_valid && observed;
  assign out_data = ram_resp_rdata;
endmodule
""".replace("EXTRA_PORTS", extra_ports))
    artifact = RTLArtifact(str(rtl), "memory_reader", tmp_path / "sim")
    manifest = artifact.frontend_catalogue(expected_identity=artifact.identity)
    assert type(manifest) is not api.Refusal, manifest
    goal = "monitor.response-deadline"
    provider = RTLGenerationProvider.from_artifact(
        artifact, contract, GenerationSpec(8, 2, 1, 3), manifest,
        targetable_goals=(goal,), clock_schedule=schedule,
        workdir=tmp_path / "provider", timeout_ms=5000)
    selection = provider.select(seed)
    assert type(selection) is api.ProbeSelection, selection
    assert provider.exact_job_context().memory_environment is environment
    assert len(selection.requests) == 2
    try:
        result = provider.probe(seed, selection.requests[-1])
    except api.Refused as exc:
        pytest.fail(str(exc.refusal.as_dict()))
    assert result.outcome is probing.Outcome.SAT, result.detail
    assert result.candidate.memory_environment.as_dict() == environment.as_dict()
    assert result.candidate.slots == seed.slots
    assert dict(result.candidate.queues)["in"][0] == (1,)
    # Generation's SAT is not scoring authority: check it with a separate replay.
    trace = artifact.trace(contract, result.candidate)
    assert type(trace) is api.AuditedTrace, trace
    hits = monitor_for(contract).check(result.candidate, trace).hits
    assert any(hit.goal == goal and 4 <= hit.slot < 8 for hit in hits)
