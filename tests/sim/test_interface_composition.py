"""Independent protocol machines, one edge, one explicit reference step."""

import pytest

from behave.sim import api
from behave.sim.core.driver import Driver
from behave.sim.core.monitor import Monitor
from behave.sim.core.protocol import adapter_program_for
from tests.sim.test_apb_protocol import _contract as apb_contract
from tests.sim.test_apb_protocol import _realization
from tests.sim.test_protocol_adapters import _evaluate, needs_verilator
from tests.support.reference import load


def _contract(reverse=False):
    apb = apb_contract()
    endpoints = list(apb.endpoints) + [
        api.Endpoint("data", "in", [api.Field("value", api.FiniteType(8), "data")],
                     api.ready_valid("in", "data_valid", "data_ready"), "clk",
                     kind="transaction", response_deadline=0, reset_policy="clears"),
        api.Endpoint("result", "out", [api.Field("value", api.FiniteType(8), "result")],
                     api.ready_valid("out", "result_valid", "result_ready"), "clk",
                     kind="transaction", answers_per_transaction=1, reset_policy="clears")]
    instances = list(apb.protocol_instances) + [api.ProtocolInstance("stream", ("data", "result"))]
    return api.TaskContract(endpoints[::-1] if reverse else endpoints, apb.clocks,
                            protocol_instances=instances[::-1] if reverse else instances)


def test_composition_cannot_hide_its_required_backpressure_policy():
    from behave.sim.core.supported import refusals_for
    from tests.sim.test_axi_lite_protocol import _full_contract

    for contract in (_contract(), _full_contract()):
        assert "unsupported-implicit-back-pressure" in {
            refusal.kind for refusal in refusals_for(contract, horizon=8)}
        assert "unsupported-implicit-back-pressure" not in {
            refusal.kind for refusal in refusals_for(contract, horizon=8, stall_every=0)}
    assert "unsupported-implicit-back-pressure" not in {
        refusal.kind for refusal in refusals_for(apb_contract(), horizon=8)}


@pytest.mark.parametrize("reverse", [False, True])
def test_drain_begins_after_all_interfaces_finish_injecting(reverse):
    from behave.sim.core.input_queue import input_completion_environment
    from tests.sim.test_axi_lite_protocol import _full_contract, _full_reference, _FullDevice

    contract, reference = _full_contract(reverse), _full_reference()
    stimulus = api.Stimulus({"write": [(0, 5, 15)], "read": [(0,)]}, [
        api.EventSlot(offers=("write",) if i == 0 else ("read",) if i == 4 else (),
                      controls={"rst": 0, "bready": int(i >= 4), "rready": 1})
        for i in range(8)])
    assert not input_completion_environment(stimulus, contract).faults
    task = api.admit(contract, _realization(contract, "axil-device"), reference=reference)
    result = _evaluate(task, stimulus, _FullDevice(), reference)
    assert result.passed, result.as_dict()
    trace = result.records[0]
    assert api.transfers(trace, "write_status") == [(0,)]
    assert api.transfers(trace, "read_result") == [(5, 0)]
    report = Monitor(contract).check(stimulus, trace)
    assert not report.assumption_violations and not report.violations
    assert report.opportunities["monitor.input-completion"] == 2

    # A stall in the actual global drain is still an environment exclusion,
    # even after the already observed responses have matched.
    bad = api.Stimulus(dict(stimulus.queues), [
        api.EventSlot(offers=slot.offers,
                      controls={**dict(slot.controls), **({"bready": 0} if i == 6 else {})})
        for i, slot in enumerate(stimulus.slots)])
    excluded = _evaluate(task, bad, _FullDevice(), reference)
    assert not excluded.complete and not excluded.passed
    report = Monitor(contract).check(bad, excluded.records[0])
    assert [(v.goal, v.slot, v.endpoint) for v in report.assumption_violations] == [
        ("environment.drain-not-receptive", 6, "write_status")]


@pytest.mark.parametrize("missing", [False, True])
def test_clock_projection_preserves_global_completion_and_padding(missing):
    from behave.sim.core.atomic import AtomicDriver
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
    from behave.sim.core.monitor import monitor_for

    endpoints = []
    for name in ("a", "b"):
        for direction in ("in", "out"):
            endpoint = f"{name}_{direction}"
            endpoints.append(api.Endpoint(
                endpoint, direction, [api.Field("value", api.FiniteType(8), endpoint + "_data")],
                api.ready_valid(direction, endpoint + "_valid", endpoint + "_ready"), name,
                kind="transaction", reset_policy="clears",
                answers_per_transaction=1 if direction == "out" else None))
    contract = api.TaskContract(endpoints, [
        api.ClockDomain(name, name + "_clk", name + "_rst") for name in ("a", "b")])
    slots = [api.EventSlot(
        offers=("a_in",) if i == 0 else ("b_in",) if i == 3 else (),
        controls={"a_rst": 0, "b_rst": 0, "a_out_ready": int(i >= 4), "b_out_ready": 1},
        ticks=("b",) if i % 2 else ("a",)) for i in range(8)]
    schedule = GlobalSchedule([
        ClockTiming(name, 2, phase=i, clock=name + "_clk", reset=name + "_rst")
        for i, name in enumerate(("a", "b"))],
        [GlobalEventSlot(i, event=slot) for i, slot in enumerate(slots)])
    stimulus = api.Stimulus({"a_in": [(7,)], "b_in": [(9,)]}, slots, clock_schedule=schedule)
    driver = AtomicDriver(contract, stimulus)
    for i in range(8):
        driver.observe(i, driver.drive(i), {
            "a_in_ready": int(not missing), "a_out_valid": int(not missing and 2 <= i <= 4),
            "a_out_data": 7, "b_in_ready": 1, "b_out_valid": int(i == 3), "b_out_data": 9})
    trace = driver.trace()
    monitor = monitor_for(contract)
    report = monitor.check(stimulus, trace)
    assert not report.assumption_violations
    assert report.opportunities["monitor.input-completion"] == 2
    completion = [(v.slot, v.endpoint) for v in report.violations
                  if v.goal == "monitor.input-completion"]
    assert completion == ([(7, "a_in")] if missing else [])
    assert [(h.slot, h.endpoint) for h in report.hits
            if h.goal == "monitor.input-completion"] == completion

    if missing:
        # A malformed cross-domain transfer record must not discharge the
        # obligation that the actual domain's observations still leave pending.
        corrupt = api.AuditedTrace(stimulus, records=[
            api.SlotRecord(accepted={"a_in": (7,)}, outputs=dict(row.outputs)) if i == 1 else row
            for i, row in enumerate(trace.records)])
        report = monitor.check(stimulus, corrupt)
        assert [(v.slot, v.endpoint) for v in report.violations
                if v.goal == "monitor.input-completion"] == [(7, "a_in")]
        for mode in (api.Completion.INCOMPLETE, api.Completion.PADDED):
            records = list(trace.records[:4])
            if mode is api.Completion.PADDED:
                records += [api.SlotRecord(monitor={"padding": 1}) for _ in range(4)]
            partial = api.AuditedTrace(stimulus, records=records, completion=mode)
            report = monitor.check(stimulus, partial)
            assert not any(hit.goal == "monitor.input-completion" for hit in report.hits)
            assert [(u.slot, u.endpoint) for u in report.unresolved
                    if u.goal == "monitor.input-completion"] == [(3, "a_in")]


def _stimulus():
    return api.Stimulus({"in": [(3,)], "data": [(7,), (7,)]}, [
        api.EventSlot(reset=True, controls={"rst": 1, "result_ready": 1}),
        api.EventSlot(offers=("in",), controls={"rst": 0, "result_ready": 1}),
        api.EventSlot(offers=("data",), controls={"rst": 0, "result_ready": 1}),
        api.EventSlot(offers=("data",), controls={"rst": 0, "result_ready": 1})])


def _reference():
    return load({"behavior.py": b"""class Model:
    def reset(self):
        self.config = 0
    def process_slot(self, slot):
        inputs = slot['inputs']
        outputs = {}
        # Both transfers observe the old configuration on this edge.
        if 'data' in inputs:
            outputs['result'] = {'result': (inputs['data']['data'] + self.config) & 255}
        if 'in' in inputs:
            self.config = inputs['in']['paddr']
            outputs['out'] = {'prdata': self.config * 2, 'pslverr': 0}
        return outputs
"""}, config={"class": "Model"})


class _Device:
    kind = "device"
    identity = "apb-device"

    def __init__(self, wrong_order=False, drop_stream=False):
        self.wrong_order, self.drop_stream = wrong_order, drop_stream

    def reset(self):
        self.config = self.after = 0

    def settle(self, presentations, controls):
        request, data = presentations["in"], presentations["data"]
        self.after = request[0] if controls["a_enable"] else self.config
        cfg = self.after if self.wrong_order else self.config
        return {"pready": 1, "prdata": request[0] * 2 if request else 0, "pslverr": 0,
                "data_ready": 1, "result_valid": int(data is not None and not self.drop_stream),
                "result": (data[0] + cfg) & 255 if data else 0}

    def edge(self):
        self.config = self.after


def test_configuration_and_stream_share_one_edge_and_one_score():
    traces = []
    for reverse in (False, True):
        contract = _contract(reverse)
        reference = _reference()
        task = api.admit(contract, _realization(contract), reference=reference)
        result = _evaluate(task, _stimulus(), _Device(), reference)
        assert result.passed, result.as_dict()
        traces.append([dict(row.accepted) for row in result.records[0].records])
        assert api.transfers(result.records[0], "result") == [(7,), (10,)]
        assert set(traces[-1][2]) == {"in", "out", "data", "result"}
    assert traces[0] == traces[1]
    assert _contract().digest == _contract(True).digest


def test_configuration_effect_order_is_not_guessed_by_endpoint_iteration():
    contract, reference = _contract(), _reference()
    task = api.admit(contract, _realization(contract), reference=reference)
    result = _evaluate(task, _stimulus(), _Device(wrong_order=True), reference)
    assert result.complete and not result.passed, result.as_dict()
    assert not result.results[0].comparison.ok


def test_each_instance_keeps_its_own_response_obligations():
    contract, reference = _contract(), _reference()
    task = api.admit(contract, _realization(contract), reference=reference)
    result = _evaluate(task, _stimulus(), _Device(drop_stream=True), reference)
    assert result.complete and not result.passed, result.as_dict()
    assert "monitor.response-deadline" in result.safety_hits
    assert api.transfers(result.records[0], "out") == [(6, 0)]
    assert api.transfers(result.records[0], "result") == []


def test_product_state_and_replay_use_the_same_component_transitions():
    contract, stimulus = _contract(), _stimulus()
    driver, device = Driver(contract, stimulus), _Device()
    device.reset()
    for index, slot in enumerate(stimulus.slots):
        if slot.reset:
            device.reset()
        presented, controls = driver.presentations(index), driver.controls(index)
        driver.observe(index, presented, device.settle(presented, controls))
        device.edge()
    program = adapter_program_for(contract)
    replay = program.replay(contract, stimulus, driver.trace())
    for index, row in enumerate(replay):
        recorded = dict(driver.records[index].accepted)
        for endpoint, channel in row.channels.items():
            assert channel.transferred == (recorded.get(endpoint) is not None)
    assert len({component.name for component in driver.manifest}) == len(driver.manifest)
    assert not Monitor(contract).check(stimulus, driver.trace()).violations


def test_composition_is_explicit_and_shared_products_supply_monitor_queries():
    contract = _contract()
    legacy = api.TaskContract(contract.endpoints, contract.clocks)
    with pytest.raises(api.Refused, match="admission-adapter-combination-unsupported"):
        adapter_program_for(legacy)
    task = api.admit(contract, _realization(contract), reference=_reference())
    policy = api.evaluation_policy(task, api.CatalogueManifest(()))
    policy.validate(api.CONCRETE_PHASE)
    policy.validate(api.EXACT_ANALYSIS_PHASE)


@pytest.mark.cosim
@needs_verilator
def test_real_rtl_configuration_and_stream_commit_on_one_edge(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact
    from tests.support.device_boundary import run_device

    rtl = tmp_path / "configured.sv"
    rtl.write_text("""module configured(input clk, rst, z_sel, a_enable,
      input [7:0] paddr, output pready, output [7:0] prdata, output pslverr,
      input [7:0] data, input data_valid, output data_ready,
      output [7:0] result, output result_valid, input result_ready);
      reg [7:0] cfg;
      always @(posedge clk) if (rst) cfg <= 0;
        else if (z_sel && a_enable && pready) cfg <= paddr;
      assign pready = 1;
      assign prdata = paddr * 2;
      assign pslverr = 0;
      assign data_ready = result_ready;
      assign result_valid = data_valid;
      assign result = data + cfg;
    endmodule""")
    contract, reference = _contract(), _reference()
    artifact = RTLArtifact(rtl, "configured", tmp_path / "sim")
    task = api.admit(contract, _realization(contract, artifact.identity), reference=reference)
    trace = run_device(task, _stimulus(), artifact)
    assert api.transfers(trace, "result") == [(7,), (10,)]
    assert api.compare_run(task, reference, trace).ok
    assert not Monitor(contract).check(_stimulus(), trace).violations
