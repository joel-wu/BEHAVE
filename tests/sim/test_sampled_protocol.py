"""Plain/valid-only samples use the shared Driver and concrete score boundary."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.core.failure import CandidateFault
from behave.sim.core.monitor import Monitor
from behave.sim.core.protocol import adapter_program_for
from behave.sim.core.protocol_exact import AdapterExactTable
from tests.sim.test_protocol_adapters import _evaluate, _reference, needs_verilator
from tests.support.device_boundary import run_device


def _contract(*, input_valid=False, output_valid=False, deadline=None, edge="rising",
              reset_policy=api.ResetPolicy.CLEARS):
    return api.TaskContract(
        [api.Endpoint(
            "in", "in", [api.Field("a", api.FiniteType(8), "in_a")],
            api.sampled_edge("in", valid="in_valid" if input_valid else None),
            clock="clk", kind=api.Kind.TRANSACTION,
            response_deadline=deadline, reset_policy=reset_policy),
         api.Endpoint(
            "out", "out", [api.Field("y", api.FiniteType(8), "out_y")],
            api.sampled_edge("out", valid="out_valid" if output_valid else None),
            clock="clk", kind=api.Kind.TRANSACTION,
            answers_per_transaction=1, reset_policy=reset_policy)],
        [api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=2, edge=edge)],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))])


def _realization(contract, identity="sampled-device"):
    ports = [api.Port("clk", 1, driven_by="environment"),
             api.Port("rst", 1, driven_by="environment")]
    for endpoint in contract.endpoints:
        owner = "environment" if endpoint.direction is api.Direction.IN else "artifact"
        ports.extend(api.Port(field.port, 8, driven_by=owner, clock="clk")
                     for field in endpoint.fields)
        ports.extend(api.Port(pin, 1, driven_by="environment")
                     for pin in endpoint.adapter.environment_controls)
        ports.extend(api.Port(pin, 1, driven_by="artifact")
                     for pin in endpoint.adapter.artifact_controls)
    return api.Realization(
        "device", ports, identity=identity,
        channel_ports={endpoint.name: [field.port for field in endpoint.fields]
                       for endpoint in contract.endpoints})


class _Doubling:
    kind = "device"
    identity = "sampled-device"

    def __init__(self, *, pipeline=False, spurious=False):
        self.pipeline = pipeline
        self.spurious = spurious
        self.reset()

    def reset(self):
        self.pending = self.after = None

    def settle(self, presented, controls):
        value = None if presented is None else (presented[0] * 2) & 255
        self.after = value
        out = self.pending if self.pipeline else value
        return {"out_valid": int(out is not None or self.spurious),
                "out_y": out if out is not None else 123}

    def edge(self):
        self.pending = self.after


def _task(contract):
    reference = _reference()
    return api.admit(contract, _realization(contract), reference=reference), reference


@pytest.mark.parametrize("input_valid,output_valid", tuple(itertools.product((False, True), repeat=2)))
def test_plain_and_valid_only_each_sample_equal_values_again(input_valid, output_valid):
    contract = _contract(input_valid=input_valid, output_valid=output_valid)
    task, reference = _task(contract)
    stimulus = api.reset_then(contract, [(3,), (3,), (5,)], drain=0)
    result = _evaluate(task, stimulus, _Doubling(), reference)
    assert result.passed, result.as_dict()
    assert api.transfers(result.records[0], "in") == [(3,), (3,), (5,)]
    assert api.transfers(result.records[0], "out") == [(6,), (6,), (10,)]
    goals = Monitor(contract).goals()
    assert "monitor.sink-stability" not in goals
    assert all("ready" not in name for name in contract.environment_controls())


def test_inflight_reset_cancels_only_the_pending_response():
    contract = _contract(input_valid=True, output_valid=True, deadline=1)
    task, reference = _task(contract)
    stimulus = api.Stimulus(
        {"in": [(3,), (5,), (5,)]},
        [api.EventSlot(reset=True, controls={"rst": 1}),
         api.EventSlot(offers=("in",), controls={"rst": 0}),
         # The first pending answer is explicitly discarded by reset.
         api.EventSlot(reset=True, controls={"rst": 1}),
         api.EventSlot(controls={"rst": 0}),
         api.EventSlot(offers=("in",), controls={"rst": 0}),
         api.EventSlot(offers=("in",), controls={"rst": 0}),
         api.EventSlot(controls={"rst": 0})])
    result = _evaluate(task, stimulus, _Doubling(pipeline=True), reference)
    assert result.complete and result.passed, result.as_dict()
    assert result.results[0].comparison.cancelled == (("out", 2, (6,)),)
    assert api.transfers(result.records[0], "out") == [(10,), (10,)]


def test_valid_only_pipeline_idle_quiescent_reset_and_last_drain_sample():
    contract = _contract(input_valid=True, output_valid=True, deadline=1)
    task, reference = _task(contract)
    stimulus = api.Stimulus(
        {"in": [(3,), (5,), (5,)]},
        [api.EventSlot(reset=True, controls={"rst": 1}),
         api.EventSlot(offers=("in",), controls={"rst": 0}),
         api.EventSlot(controls={"rst": 0}),
         api.EventSlot(reset=True, controls={"rst": 1}),
         api.EventSlot(controls={"rst": 0}),
         api.EventSlot(offers=("in",), controls={"rst": 0}),
         api.EventSlot(offers=("in",), controls={"rst": 0}),
         api.EventSlot(controls={"rst": 0})])
    result = _evaluate(task, stimulus, _Doubling(pipeline=True), reference)
    assert result.passed, result.as_dict()
    assert api.transfers(result.records[0], "out") == [(6,), (10,), (10,)]


@pytest.mark.parametrize("extra_idle", [False, True])
def test_plain_input_cannot_hide_a_physical_sample_behind_no_offer(extra_idle):
    contract = _contract()
    task, _reference_model = _task(contract)
    stimulus = (api.reset_then(contract, [(3,)], drain=1) if extra_idle
                else api.Stimulus({"in": [(3,)]}, [api.EventSlot(controls={"rst": 0})]))
    with pytest.raises(api.Refused, match="stimulus-sampled-input-missing"):
        run_device(task, stimulus, _Doubling())


@pytest.mark.parametrize("fault", ["deadline", "spurious", "missing"])
def test_declared_latency_and_output_obligations_still_enter_one_score(fault):
    contract = _contract(input_valid=True, output_valid=True,
                         deadline=0 if fault == "deadline" else None)
    task, reference = _task(contract)
    stimulus = api.reset_then(contract, [(3,)], drain=1 if fault != "missing" else 0)
    device = _Doubling(pipeline=fault != "spurious", spurious=fault == "spurious")
    result = _evaluate(task, stimulus, device, reference)
    assert not result.passed, result.as_dict()
    if fault == "deadline":
        assert "monitor.response-deadline" in result.safety_hits
        assert api.transfers(result.records[0], "out") == [(6,)]
    elif fault == "spurious":
        assert "monitor.output-without-transaction" in result.safety_hits
    else:
        assert api.transfers(result.records[0], "out") == []


def test_unreadable_payload_only_matters_at_a_valid_sample():
    contract = _contract(input_valid=True, output_valid=True)
    program = adapter_program_for(contract)
    slot = api.EventSlot(controls={"rst": 0})
    state = program.initial_state(contract)
    drive = program.drive(contract, state, slot, None, dict(slot.controls))
    assert not program.step(contract, state, slot, drive,
                            {"out_valid": 0, "out_y": None}).emitted_outputs
    with pytest.raises(CandidateFault, match="driver-output-unreadable"):
        program.step(contract, state, slot, drive, {"out_valid": 1, "out_y": None})
    with pytest.raises(CandidateFault, match="driver-handshake-unreadable"):
        program.step(contract, state, slot, drive, {"out_valid": "x", "out_y": 6})


def test_plain_and_valid_binding_are_identity_and_have_monitor_queries():
    plain = _contract()
    valid = _contract(input_valid=True, output_valid=True)
    assert plain.digest != valid.digest
    assert _contract(edge="falling").digest != plain.digest
    assert api.TaskContract.from_dict(valid.as_dict()).digest == valid.digest
    task, _reference_model = _task(valid)
    policy = api.evaluation_policy(task, api.CatalogueManifest(()))
    original = policy.manifest.digest()
    assert policy.validate(api.CONCRETE_PHASE) is policy
    assert policy.enabled_providers(api.LOCAL_GUIDANCE)
    assert policy.validate(api.EXACT_ANALYSIS_PHASE) is policy
    assert AdapterExactTable(adapter_program_for(valid), valid).states
    assert policy.manifest.digest() == original


def test_plain_baseline_supplies_every_sample_in_every_case():
    contract = _contract()
    baseline = api.build_baseline(
        contract, api.GenerationSpec(5, 3, 2, 0),
        policy=api.BaselinePolicy(case_limit=4, max_queue_depth=3))
    task, reference = _task(contract)
    assert len(baseline.stimuli) >= 3
    for stimulus in baseline.stimuli:
        assert len(dict(stimulus.queues)["in"]) == 3
        assert _evaluate(task, stimulus, _Doubling(), reference).passed


@pytest.mark.parametrize("generation,policy,code", [
    (api.GenerationSpec(5, 3, 2, 1), api.BaselinePolicy(max_queue_depth=3),
     "generation-baseline-sampled-idle"),
    (api.GenerationSpec(5, 2, 2, 0), api.BaselinePolicy(max_queue_depth=3),
     "generation-baseline-required-input-depth"),
    (api.GenerationSpec(5, 3, 2, 0), api.BaselinePolicy(max_queue_depth=2),
     "generation-baseline-required-input-depth"),
])
def test_plain_baseline_refuses_an_impossible_sampling_budget(generation, policy, code):
    with pytest.raises(api.Refused, match=code):
        api.build_baseline(_contract(), generation, policy=policy)


@pytest.mark.parametrize("direction,env,art", [
    ("in", (), ("ready",)), ("out", ("ready",), ()),
    ("in", ("v0", "v1"), ()), ("out", (), ("v0", "v1")),
])
def test_sampling_rejects_unimplemented_pin_roles(direction, env, art):
    contract = _contract()
    row = contract.as_dict()
    endpoint = next(item for item in row["endpoints"] if item["direction"] == direction)
    endpoint["adapter"].update(environment_controls=list(env), artifact_controls=list(art))
    malformed = api.TaskContract.from_dict(row)
    with pytest.raises(api.Refused, match="adapter-sampled-edge-controls"):
        adapter_program_for(malformed)


def _rtl_source(valid, pipeline, edge):
    optional_pins = ", input wire in_valid, output reg out_valid" if valid else ""
    if pipeline:
        active = "posedge" if edge == "rising" else "negedge"
        body = f"""
        always @({active} clk) begin
            if (rst) begin out_valid <= 0; out_y <= 0; end
            else begin out_valid <= in_valid; out_y <= in_a << 1; end
        end
        """
    else:
        body = "always @* out_y = in_a << 1;"
        if valid:
            body += "always @* out_valid = in_valid && !rst;"
    return f"""
    module sampled_double(input wire clk, input wire rst,
        input wire [7:0] in_a, output reg [7:0] out_y {optional_pins});
        {body}
    endmodule
    """


@pytest.mark.cosim
@needs_verilator
@pytest.mark.parametrize("valid,pipeline,edge", [
    (False, False, "rising"), (True, False, "rising"),
    (True, True, "rising"), (True, True, "falling"),
])
def test_real_rtl_sampling_uses_the_existing_driver(tmp_path, valid, pipeline, edge):
    from behave.sim.rtl.artifact import RTLArtifact

    source = _rtl_source(valid, pipeline, edge)
    rtl = tmp_path / "sampled_double.sv"
    rtl.write_text(source)
    artifact = RTLArtifact(rtl, "sampled_double", tmp_path / "sim")
    contract = _contract(input_valid=valid, output_valid=valid, edge=edge)
    reference = _reference()
    task = api.admit(contract, _realization(contract, artifact.identity), reference=reference)
    stimulus = api.reset_then(contract, [(3,), (3,), (5,)], drain=int(pipeline))
    trace = run_device(task, stimulus, artifact)
    assert api.transfers(trace, "in") == [(3,), (3,), (5,)]
    assert api.transfers(trace, "out") == [(6,), (6,), (10,)]
    assert api.compare_run(task, reference, trace).ok
    assert not Monitor(contract).check(stimulus, trace).violations


@pytest.mark.cosim
@needs_verilator
@pytest.mark.parametrize("clears", [False, True])
def test_real_rtl_inflight_reset_uses_declared_cancellation(tmp_path, clears):
    from behave.sim.rtl.artifact import RTLArtifact

    rtl = tmp_path / "sampled_double.sv"
    rtl.write_text(_rtl_source(True, True, "rising"))
    artifact = RTLArtifact(rtl, "sampled_double", tmp_path / "sim")
    contract = _contract(
        input_valid=True, output_valid=True,
        reset_policy=api.ResetPolicy.CLEARS if clears else api.ResetPolicy.KEEPS)
    reference = _reference()
    task = api.admit(contract, _realization(contract, artifact.identity), reference=reference)
    stimulus = api.Stimulus(
        {"in": [(3,), (5,)]},
        [api.EventSlot(reset=True, controls={"rst": 1}),
         api.EventSlot(offers=("in",), controls={"rst": 0}),
         api.EventSlot(reset=True, controls={"rst": 1}),
         api.EventSlot(offers=("in",), controls={"rst": 0}),
         api.EventSlot(controls={"rst": 0})])
    trace = run_device(task, stimulus, artifact)
    assert api.transfers(trace, "out") == [(10,)]
    result = api.compare_run(task, reference, trace)
    assert result.ok is clears
    assert result.cancelled == ((("out", 2, (6,)),) if clears else ())


@pytest.mark.cosim
@needs_verilator
@pytest.mark.parametrize("valid", [False, True])
def test_public_sampled_analysis_requires_an_explicit_action_schedule(tmp_path, valid):
    from tests.sim.test_rtl_public_analysis_path import _BaselineOnly

    source = _rtl_source(valid, valid, "rising")
    path = tmp_path / "sampled_double.sv"
    path.write_text(source)
    contract = _contract(input_valid=valid, output_valid=valid)
    reference = _reference()
    task = api.admit(
        contract, api.realization_of_rtl_source(
            source, "sampled fixture", top="sampled_double", contract=contract),
        reference=reference)
    generation = api.GenerationSpec(
        horizon=3 + int(valid), queue_bound=1, reset_hold_cycles=2, drain=int(valid))
    handle = api.prepare_rtl(
        task, api.RTLConfig(path, "sampled_double", tmp_path / "rtl-work",
                            timeout_s=300), generation)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "invocation",
        baseline=api.build_baseline(
            contract, generation, policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1)),
        providers=(_BaselineOnly(),), probe_budget=0,
        run_analysis=True, reference=reference,
        feasibility_timeout_ms=5000, query_timeout_ms=5000,
        analysis_wall_timeout_seconds=180))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    # A monitor encoding does not supply the missing finite action schedule.
    # Refuse that unbound Analysis domain without discarding the concrete score.
    assert result.analysis.verdict is api.Verdict.REFUSED, result.analysis.as_dict()
    refusal = result.analysis.evidence["refusal"]
    assert refusal["kind"] == "rtl-exact-action-schedule-unbound"
    assert "analysis_clock_schedule" in refusal["why"]
    persisted = json.loads(Path(result.record_path).read_text())
    assert persisted["authority"]["passed"] is True
    assert persisted["authority"]["evaluation"] == json.loads(json.dumps(
        result.evaluation.as_evidence_dict()))
    assert persisted["report"]["analysis"] == json.loads(json.dumps(result.analysis.as_dict()))
