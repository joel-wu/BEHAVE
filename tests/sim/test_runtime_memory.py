"""Runtime memory shares the finite transition, scoring and recorded schedule."""

import pytest

from behave.sim import api
from behave.sim.core.memory import MemoryState, advance_memory_state, drive_memory_state
from behave.sim.core.runtime_schedule import TransactionSchedule
from behave.sim.memory_driver._protocol import _counter_word

from .test_memory_result_score import SOURCE, setup


@pytest.mark.parametrize("stall_rate", [0.0, 0.35, 1.0])
@pytest.mark.parametrize("latency", [1, 3])
def test_runtime_ready_policy_matches_explicit_prefix_and_memory_transitions(stall_rate, latency):
    memory = api.MemoryInterface("ram", 3, 8, "clk", [api.MemoryPort("rw", "a"), api.MemoryPort("r", "b")])
    runtime = api.MemoryRun.from_policy(memory, None, seed=19, stall_rate=stall_rate,
                                       read_latency=latency, initial={1: 17})
    explicit = runtime.materialize(24)
    threshold = int(stall_rate * (1 << 256))
    assert explicit.ready == tuple((port.id, tuple(
        int(_counter_word("external-memory-ready/1", 19, "ram", port.id, index, slot) >= threshold)
        for slot in range(24))) for index, port in enumerate(memory.ports))
    assert explicit.as_dict() == api.MemoryRun.from_policy(memory, 24, seed=19, stall_rate=stall_rate,
                                                           read_latency=latency, initial={1: 17}).as_dict()
    environment = api.MemoryEnvironment([runtime])
    assert api.MemoryEnvironment.from_dict(environment.as_dict()).digest == environment.digest
    assert not environment.faults_against([memory], None)
    assert environment.faults_against([memory], 24)  # An unresolved policy is not a finite trace.
    left = right = MemoryState.initial(memory, runtime)
    for slot in range(24):
        assert drive_memory_state(memory, runtime, left, slot) == drive_memory_state(memory, explicit, right, slot)
        signals = {"ram__a_req_valid": 1, "ram__a_req_we": slot % 2,
                   "ram__a_req_addr": slot % 8, "ram__a_req_wdata": slot + 5,
                   "ram__b_req_valid": 1, "ram__b_req_addr": slot % 8}
        left = advance_memory_state(memory, runtime, left, slot, signals)
        right = advance_memory_state(memory, explicit, right, slot, signals)
        assert left == right


def test_memory_default_policy_does_not_inherit_analysis_horizon_or_queue_bound():
    from behave.sim.baseline import _materialize_baseline

    task, _ = setup()
    baseline = _materialize_baseline(task.contract, api.GenerationSpec(131072, 64, 3, 8192),
                                     api.BaselinePolicy())
    assert baseline.generation_spec is None
    assert all(s.depth("in") == 100 and not s.slots for s in baseline.stimuli)
    assert all(not bits for s in baseline.stimuli for run in s.memory_environment.runs
               for _, bits in run.ready)
    suite = api.build_replay_suite(task.contract, baseline.stimuli)
    assert len(suite.continuous) == 1
    stream = suite.continuous[0]
    assert stream.depth("in") == 100 * len(baseline.stimuli)
    assert stream.memory_environment.digest == baseline.stimuli[0].memory_environment.digest
    controller = TransactionSchedule(task.contract, stream, stream.replay_policy,
                                     expected_outputs={"out": stream.depth("in")})
    assert controller.request is stream


def test_resolution_cannot_change_ready_bits_initial_cells_or_latency():
    from behave.sim.core.stimulus import validate_resolved_request

    task, _ = setup()
    request = api.build_baseline(task.contract, policy=api.BaselinePolicy(case_limit=1)).stimuli[0]
    slots = (api.EventSlot(reset=True), api.EventSlot())
    actual = request.memory_environment.materialize(len(slots))
    good = api.Stimulus(queues=request.queues, slots=slots, memory_environment=actual)
    validate_resolved_request(good, request)
    base, = actual.runs
    for changed in (
        api.MemoryRun("ram", ready={None: [0, 1]}),
        api.MemoryRun("ram", ready={None: [1, 1]}, initial={0: 9}),
        api.MemoryRun("ram", ready={None: [1, 1]}, read_latency=2),
    ):
        assert changed.as_dict() != base.as_dict()
        forged = api.Stimulus(queues=request.queues, slots=slots,
                             memory_environment=api.MemoryEnvironment([changed]))
        with pytest.raises(ValueError, match="changed its request"):
            validate_resolved_request(forged, request)


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ["behavior", pytest.param("rtl", marks=pytest.mark.eda)])
@pytest.mark.parametrize("missing_write", [False, True])
def test_public_runtime_memory_score_checks_final_cells_in_both_phases(tmp_path, kind, missing_write):
    from behave.sim.reference_isolated import ADAPTER

    reference = api.load_reference(ADAPTER, {"behavior.py": SOURCE.encode()}, config={"class": "M"})
    template, _ = setup(reference)
    contract = template.contract
    spec = api.GenerationSpec(131072, 64, 3, 8192)
    if kind == "behavior":
        source = SOURCE.replace("self.ram[1] = in_a", "pass") if missing_write else SOURCE
        path = tmp_path / "candidate.py"
        path.write_text(source)
        task = api.admit(contract, api.realization_of_behavior_source(source, contract, file=str(path)),
                         reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=str(path)), spec,
                                      bounded_analysis=False)
    else:
        source = """module writer(input clk,rst,input [7:0] in_a,input in_valid,output in_ready,
          output [7:0] out_y,output out_valid,input out_ready,
          output ram_req_valid,output [1:0] ram_req_addr,output ram_req_we,
          output [7:0] ram_req_wdata,input ram_req_ready,ram_resp_valid,input [7:0] ram_resp_rdata);
          assign in_ready=ram_req_ready && out_ready;
          assign out_valid=in_valid && ram_req_ready && !rst;
          assign out_y=1;
          assign ram_req_valid=EN && in_valid && out_ready && !rst;
          assign ram_req_addr=1;
          assign ram_req_we=1;
          assign ram_req_wdata=in_a;
        endmodule""".replace("EN", "0" if missing_write else "1")
        path = tmp_path / "candidate.sv"
        path.write_text(source)
        task = api.admit(contract, api.realization_of_rtl_source(source, "writer", top="writer", contract=contract),
                         reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "writer", tmp_path / "rtl"), spec)
    request = handle.command_request(run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3), search_policy=api.SearchPolicy(time_budget_s=0),
        run_analysis=False)
    assert all(s.depth("in") == 100 for s in request.baseline.stimuli)
    result = api.handle_command(request)
    assert result.evaluation is not None, result.failure
    evaluation = result.evaluation
    if missing_write:
        # Public scoring stops after a proven memory mismatch. A fresh exhaustive
        # replay independently checks that both phases still compare final cells.
        assert result.decision.authoritative and not result.decision.passed
        assert evaluation.memory_mismatch_count > 0
        assert evaluation.failure_witness is not None
        suite = api.build_replay_suite(contract, result.generated.stimuli)
        evaluation = handle.replay(suite, reference=reference).evaluation
    assert evaluation.complete, result.as_dict()
    assert evaluation.mismatch_count == 0
    assert bool(evaluation.memory_mismatch_count) is missing_write
    assert evaluation.passed is not missing_write
    assert len(evaluation.results) == len(request.baseline.stimuli) + 1
    for run in evaluation.results:
        env = run.trace.stimulus.memory_environment
        assert env.faults_against(contract.memories, len(run.trace.stimulus.slots)) == ()
        assert all(r.ready_policy is None for r in env.runs)
        if kind == "rtl":
            window = api.workload_window(contract, run.trace,
                expected_outputs={"out": run.stimulus.depth("in")})
            assert window["observation_slots"] == 200
            assert window["finish_slot"] + 200 == len(run.trace.records)
