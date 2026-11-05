"""Completed APB accesses share the ordinary Driver, monitor and score."""

import copy

import pytest

from behave.sim import api
from behave.sim.core.driver import Driver
from behave.sim.core.failure import CandidateFault
from behave.sim.core.monitor import Monitor
from behave.sim.core.protocol import AdapterDrive, adapter_program_for
from tests.sim.test_protocol_adapters import _evaluate, needs_verilator


def _contract(*, wait_limit=2):
    def adapter(direction):
        # Lexical order deliberately differs from the protocol roles.
        return api.apb_access(direction, select="z_sel", enable="a_enable", ready="pready")

    return api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", api.FiniteType(8), "paddr")],
                     adapter("in"), "clk", kind="transaction", reset_policy="clears"),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(8), "prdata"),
                                     api.Field("error", api.FiniteType(1), "pslverr")],
                     adapter("out"), "clk", kind="transaction", reset_policy="clears",
                     answers_per_transaction=1)],
        [api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=1)],
        protocol_instances=[api.ProtocolInstance(
            "bus", ("in", "out"), mapping="completed-access/1",
            bindings={"select": "z_sel", "enable": "a_enable", "ready": "pready"},
            max_wait_cycles=wait_limit)])


def _realization(contract, identity="apb-device"):
    ports = [api.Port("clk", 1, driven_by="environment"),
             api.Port("rst", 1, driven_by="environment")]
    for endpoint in contract.endpoints:
        owner = "environment" if endpoint.direction is api.Direction.IN else "artifact"
        ports.extend(api.Port(field.port, field.type.width, driven_by=owner, clock="clk")
                     for field in endpoint.fields)
        ports.extend(api.Port(pin, 1, driven_by="environment")
                     for pin in endpoint.adapter.environment_controls)
        ports.extend(api.Port(pin, 1, driven_by="artifact")
                     for pin in endpoint.adapter.artifact_controls)
    return api.Realization("device", ports, identity=identity,
                           channel_ports={e.name: [f.port for f in e.fields]
                                          for e in contract.endpoints})


def _reference():
    from tests.support.reference import load

    source = b"""class Model:
    def reset(self):
        pass
    def process(self, paddr):
        return {"prdata": (paddr * 2) & 255, "pslverr": int(paddr >= 128)}
"""
    return load({"behavior.py": source}, config={"class": "Model"})


class _Device:
    kind = "device"
    identity = "apb-device"

    def __init__(self, waits=0, bad_status=False):
        self.waits, self.bad_status = waits, bad_status
        self.reset()

    def reset(self):
        self.remaining = self.waits
        self.next_remaining = self.waits

    def settle(self, presented, controls):
        self.next_remaining = self.remaining
        if not controls["a_enable"]:
            self.next_remaining = self.waits
        elif self.remaining:
            self.next_remaining -= 1
        a = presented[0] if presented else 0
        return {"pready": int(not self.remaining), "prdata": (a * 2) & 255,
                "pslverr": int(a >= 128) ^ self.bad_status}

    def edge(self):
        self.remaining = self.next_remaining


def _task(contract):
    reference = _reference()
    return api.admit(contract, _realization(contract), reference=reference), reference


@pytest.mark.parametrize("waits", [0, 1, 2])
def test_waited_accesses_and_correct_error_responses_share_the_score(waits):
    contract = _contract()
    task, reference = _task(contract)
    stimulus = api.reset_then(contract, [(3,), (129,), (3,)], drain=14)
    result = _evaluate(task, stimulus, _Device(waits), reference)
    assert result.passed, result.as_dict()
    assert api.transfers(result.records[0], "in") == [(3,), (129,), (3,)]
    assert api.transfers(result.records[0], "out") == [(6, 0), (2, 1), (6, 0)]
    assert not result.safety_hits


def test_wrong_response_status_is_a_mismatch_not_a_protocol_violation():
    contract = _contract()
    task, reference = _task(contract)
    result = _evaluate(task, api.reset_then(contract, [(129,)], drain=4),
                       _Device(bad_status=True), reference)
    assert result.complete and not result.passed
    assert not result.safety_hits


def test_wait_limit_is_a_safety_fault_even_when_the_final_response_matches():
    contract = _contract(wait_limit=1)
    task, reference = _task(contract)
    result = _evaluate(task, api.reset_then(contract, [(3,)], drain=7), _Device(2), reference)
    assert result.complete and not result.passed, result.as_dict()
    assert "monitor.apb.wait-limit" in result.safety_hits
    assert result.results[0].comparison.ok


def test_setup_wait_reset_and_repeated_requests_use_one_input_queue():
    contract = _contract()
    stimulus = api.Stimulus({"in": [(3,), (3,)]}, [
        api.EventSlot(offers=("in",), controls={"rst": 0}),
        api.EventSlot(controls={"rst": 0}),
        api.EventSlot(reset=True, controls={"rst": 1}),
        api.EventSlot(controls={"rst": 0}),
        api.EventSlot(controls={"rst": 0}),
        api.EventSlot(offers=("in",), controls={"rst": 0}),
        api.EventSlot(controls={"rst": 0})])
    driver = Driver(contract, stimulus)
    selected, enabled = [], []
    for index in range(len(stimulus.slots)):
        controls = driver.controls(index)
        selected.append(controls["z_sel"])
        enabled.append(controls["a_enable"])
        driver.observe(index, driver.present(index), {
            "pready": int(index != 1), "prdata": 6, "pslverr": 0})
    assert selected == [1, 1, 0, 1, 1, 1, 1]
    assert enabled == [0, 1, 0, 0, 1, 0, 1]
    assert [i for i, record in enumerate(driver.records) if dict(record.accepted).get("in")] == [4, 6]
    report = Monitor(contract).check(stimulus, driver.trace())
    assert not report.violations and not report.assumption_violations


def test_physical_roles_and_wait_limit_are_immutable_semantic_identity():
    contract = _contract()
    assert api.TaskContract.from_dict(contract.as_dict()).digest == contract.digest
    assert _contract(wait_limit=3).digest != contract.digest
    image = copy.deepcopy(contract.as_dict())
    image["protocol_instances"][0]["physical"]["bindings"].update(
        select="a_enable", enable="z_sel")
    assert api.TaskContract.from_dict(image).digest != contract.digest
    image["version"] = "task-contract/6"
    with pytest.raises(TypeError, match="task-contract"):
        api.TaskContract.from_dict(image)


def test_environment_sequence_fault_and_unreadable_access_are_not_status_errors():
    contract = _contract()
    program = adapter_program_for(contract)
    state = program.initial_state(contract)
    slot = api.EventSlot(controls={"rst": 0})
    drive = program.drive(contract, state, slot, (3,), {"rst": 0})
    # PREADY/data outside a completed access are immaterial, even if unknown.
    state = program.step(contract, state, slot, drive, {}).state
    drive = program.drive(contract, state, slot, (5,), {"rst": 0})
    assert program.environment_violations(contract, state, slot, drive)[0].goal == program.SEQUENCE
    drive = AdapterDrive({"rst": 0, "z_sel": 1, "a_enable": 0}, {"in": (3,)})
    assert program.environment_violations(contract, state, slot, drive)
    drive = program.drive(contract, state, slot, (3,), {"rst": 0})
    with pytest.raises(CandidateFault, match="driver-handshake-unreadable"):
        program.step(contract, state, slot, drive, {})


def test_apb_shared_transition_exposes_monitor_query_capabilities():
    task, _ = _task(_contract())
    policy = api.evaluation_policy(task, api.CatalogueManifest(()))
    policy.validate(api.CONCRETE_PHASE)
    policy.validate(api.EXACT_ANALYSIS_PHASE)


def test_wait_bound_is_a_safety_obligation_and_setup_constrains_baseline_depth():
    contract = _contract()
    monitor = Monitor(contract)
    assert monitor.goals()["monitor.apb.wait-limit"].family == "timing"
    providers = {provider.identity.name: provider for provider in monitor.goal_providers()}
    assert "monitor.apb.wait-limit" in providers["task-monitor"].catalogue
    assert providers["task-monitor"].schema.name == "monitor-obligation"
    assert not any(provider.schema.name == "latency" for provider in providers.values())
    assert adapter_program_for(contract).baseline_queue_range(
        contract.inputs()[0], offers=4, samples=5) == (1, 2)


@pytest.mark.parametrize("physical", [
    {"bindings": {}, "max_wait_cycles": True},
    {"bindings": {"select": "p", "enable": "p"}, "max_wait_cycles": 1},
    {"bindings": {"select": 1}, "max_wait_cycles": 1},
])
def test_malformed_physical_declarations_refuse_without_coercion(physical):
    with pytest.raises(api.Refused, match="contract-interface-"):
        api.ProtocolInstance("bus", ("in", "out"), mapping="completed-access/1", **physical)


@pytest.mark.cosim
@needs_verilator
def test_real_rtl_apb_setup_wait_and_error_status(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact
    from tests.support.device_boundary import run_device

    contract = _contract()
    rtl = tmp_path / "device.sv"
    rtl.write_text("""module M(input clk, rst, z_sel, a_enable,
      input [7:0] paddr, output pready, output [7:0] prdata, output pslverr);
      reg waited;
      always @(posedge clk) if (rst || !a_enable) waited <= 0;
        else waited <= 1;
      assign pready = waited;
      assign prdata = paddr * 2;
      assign pslverr = paddr[7];
    endmodule""")
    reference = _reference()
    artifact = RTLArtifact(rtl, "M", tmp_path / "sim")
    task = api.admit(contract, _realization(contract, artifact.identity), reference=reference)
    stimulus = api.reset_then(contract, [(3,), (129,)], drain=10)
    trace = run_device(task, stimulus, artifact)
    assert api.transfers(trace, "out") == [(6, 0), (2, 1)]
    comparison = api.compare_run(task, reference, trace)
    assert comparison.ok, comparison.as_dict()
    assert not Monitor(contract).check(stimulus, trace).violations
