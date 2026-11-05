"""Independent channel assembly and held responses through the common scorer."""

import pytest

from behave.sim import api
from behave.sim.core.driver import Driver
from behave.sim.core.monitor import Monitor
from behave.sim.core.protocol import AdapterDrive, adapter_program_for
from behave.sim.goal_policy import evaluation_goal_policy
from tests.sim.test_apb_protocol import _realization
from tests.sim.test_protocol_adapters import _evaluate, needs_verilator
from tests.support.reference import load


def _contract(write=True, *, wait_limit=4, deadline=4):
    channels = ("aw", "w", "b") if write else ("ar", "r")
    controls = {channel + suffix: channel + suffix
                for channel in channels for suffix in ("valid", "ready")}
    roles = ({"address": "awaddr", "data": "wdata", "strobe": "wstrb", "response": "bresp"}
             if write else {"address": "araddr", "data": "rdata", "response": "rresp"})
    helper = api.axi_lite_write if write else api.axi_lite_read
    request = ([api.Field("address", api.FiniteType(32), "awaddr"),
                api.Field("data", api.FiniteType(32), "wdata"),
                api.Field("strobe", api.FiniteType(4), "wstrb")]
               if write else [api.Field("address", api.FiniteType(32), "araddr")])
    response = ([api.Field("status", api.FiniteType(2), "bresp")] if write else [
        api.Field("data", api.FiniteType(32), "rdata"),
        api.Field("status", api.FiniteType(2), "rresp")])
    return api.TaskContract([
        api.Endpoint("in", "in", request, helper("in", **controls), "clk", kind="transaction",
                     max_outstanding=1, response_deadline=deadline, reset_policy="clears"),
        api.Endpoint("out", "out", response, helper("out", **controls), "clk", kind="transaction",
                     answers_per_transaction=1, reset_policy="clears")],
        [api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=1)],
        protocol_instances=[api.ProtocolInstance(
            "write" if write else "read", ("in", "out"), mapping="single-outstanding/1",
            bindings={**controls, **roles}, max_wait_cycles=wait_limit)])


def _reference(write=True, *, status=None):
    params = "awaddr, wdata, wstrb" if write else "araddr"
    address = "awaddr" if write else "araddr"
    code = str(status) if status is not None else f"2 if {address} >= 128 else 0"
    result = ("{'bresp': code}" if write else "{'rdata': (araddr * 3) & 0xffffffff, 'rresp': code}")
    return load({"behavior.py": f"""class Model:
    def reset(self):
        pass
    def process(self, {params}):
        code = {code}
        return {result}
""".encode()}, config={"class": "Model"})


class _Device:
    kind = "device"
    identity = "axil-device"

    def __init__(self, write=True, *, delays=(0, 0), status=None, duplicate=False, missing=False):
        self.write, self.delays, self.status = write, delays, status
        self.duplicate, self.missing = duplicate, missing
        self.samples = []
        self.reset()

    def reset(self):
        self.age, self.aw, self.w, self.pending = 0, False, False, None

    def settle(self, presented, controls):
        response = "b" if self.write else "r"
        ready = tuple(int(self.age >= delay) for delay in self.delays)
        signals = {response + "valid": int(self.pending is not None and not self.missing),
                   response + "resp": self.pending[1] if self.pending else 0}
        if not self.write:
            signals["rdata"] = self.pending[0] if self.pending else 0
        signals.update({"awready": ready[0], "wready": ready[1]} if self.write else {"arready": ready[0]})
        self.samples.append((presented, dict(controls), dict(signals)))
        self.next_age, self.next_aw, self.next_w, self.next_pending = self.age, self.aw, self.w, self.pending
        if self.pending is not None:
            if controls[response + "ready"] and not self.duplicate and not self.missing:
                self.next_pending, self.next_aw, self.next_w, self.next_age = None, False, False, 0
        elif presented is not None:
            self.next_age += 1
            self.next_aw |= bool(controls["awvalid" if self.write else "arvalid"] and ready[0])
            self.next_w |= bool(controls["wvalid"] and ready[1]) if self.write else True
            if self.next_aw and self.next_w:
                code = self.status if self.status is not None else (2 if presented[0] >= 128 else 0)
                self.next_pending = ((presented[0] * 3) & 0xFFFFFFFF, code)
        return signals

    def edge(self):
        self.age, self.aw, self.w, self.pending = self.next_age, self.next_aw, self.next_w, self.next_pending


def _evaluate_device(contract, stimulus, device, reference=None):
    reference = _reference(device.write, status=device.status) if reference is None else reference
    task = api.admit(contract, _realization(contract, device.identity), reference=reference)
    return _evaluate(task, stimulus, device, reference)


def _stimulus(contract, write=True, *, stall=False):
    response = "b" if write else "r"
    values = [(3, 0xFF00, 2), (129, 7, 0), (3, 0xFF00, 2)] if write else [(3,), (129,), (3,)]
    offers = (0, 6, 7) if stall else (0, 1, 2)
    return api.Stimulus({"in": values}, [
        api.EventSlot(reset=True, controls={"rst": 1, response + "ready": 1}),
        *[api.EventSlot(offers=("in",) if i in offers else (),
                        controls={"rst": 0, response + "ready": int(not stall or i >= 5)})
          for i in range(19)]])


@pytest.mark.parametrize("delays", [(0, 0), (0, 2), (2, 0)])
@pytest.mark.parametrize("stall", [False, True])
def test_aw_w_accept_independently_and_each_write_is_consumed_once(delays, stall):
    contract, device = _contract(), _Device(delays=delays)
    result = _evaluate_device(contract, _stimulus(contract, stall=stall), device)
    assert result.passed, result.as_dict()
    trace = result.records[0]
    assert api.transfers(trace, "in") == [(3, 0xFF00, 2), (129, 7, 0), (3, 0xFF00, 2)]
    assert api.transfers(trace, "out") == [(0,), (2,), (0,)]
    active = [(p, c, s) for p, c, s in device.samples if p is not None]
    assert active[0][1]["awvalid"] == active[0][1]["wvalid"] == 1
    if delays == (0, 2):
        assert active[1][1]["awvalid"] == 0 and active[1][1]["wvalid"] == 1
    if delays == (2, 0):
        assert active[1][1]["awvalid"] == 1 and active[1][1]["wvalid"] == 0


@pytest.mark.parametrize("stall", [False, True])
def test_read_response_waits_for_receiver_without_missing_its_offer_deadline(stall):
    contract, device = _contract(False, deadline=1), _Device(False)
    result = _evaluate_device(contract, _stimulus(contract, False, stall=stall), device)
    assert result.passed, result.as_dict()
    assert api.transfers(result.records[0], "out") == [(9, 0), (387, 2), (9, 0)]


@pytest.mark.parametrize("write", [False, True])
def test_functional_status_and_protocol_failures_remain_separate(write):
    contract, stimulus = _contract(write), _stimulus(_contract(write), write)
    wrong = _evaluate_device(contract, stimulus, _Device(write, status=3), _reference(write))
    assert wrong.complete and not wrong.passed and not wrong.safety_hits
    invalid = _evaluate_device(contract, stimulus, _Device(write, status=1))
    assert invalid.complete and not invalid.passed
    assert "monitor.axil.response-code" in invalid.safety_hits
    assert invalid.results[0].comparison.ok
    duplicate = _evaluate_device(contract, stimulus, _Device(write, duplicate=True))
    assert duplicate.complete and not duplicate.passed
    assert "monitor.axil.response-before-request" in duplicate.safety_hits
    missing = _evaluate_device(contract, stimulus, _Device(write, missing=True))
    assert missing.complete and not missing.passed
    assert "monitor.response-deadline" in missing.safety_hits


def test_response_cannot_precede_the_edge_that_assembles_the_request():
    contract = _contract()
    stimulus = api.Stimulus({"in": [(3, 4, 15)]}, [
        api.EventSlot(offers=("in",), controls={"rst": 0, "bready": 1})])
    driver = Driver(contract, stimulus)
    driver.observe(0, driver.present(0), {"awready": 1, "wready": 1, "bvalid": 1, "bresp": 0})
    assert {v.goal for v in Monitor(contract).check(stimulus, driver.trace()).violations} >= {
        "monitor.axil.response-before-request"}


def test_partial_channel_acceptance_survives_stall_but_reset_restarts_both_channels():
    contract = _contract()
    stimulus = api.Stimulus({"in": [(3, 4, 15)]}, [
        api.EventSlot(offers=("in",), controls={"rst": 0, "bready": 1}),
        api.EventSlot(reset=True, controls={"rst": 1, "bready": 1}),
        api.EventSlot(controls={"rst": 0, "bready": 1}),
        api.EventSlot(controls={"rst": 0, "bready": 1})])
    driver = Driver(contract, stimulus)
    for i in range(4):
        if i == 2:
            assert driver.controls(i)["awvalid"] == driver.controls(i)["wvalid"] == 1
        driver.observe(i, driver.present(i), {
            "awready": 1, "wready": int(i != 0), "bvalid": int(i == 3), "bresp": 0})
    assert api.transfers(driver.trace(), "in") == [(3, 4, 15)]
    assert api.transfers(driver.trace(), "out") == [(0,)]
    assert not Monitor(contract).check(stimulus, driver.trace()).violations


def test_wait_bound_and_stalled_response_stability_have_concrete_replay_hits():
    contract = _contract(wait_limit=1)
    result = _evaluate_device(contract, _stimulus(contract), _Device(delays=(0, 3)))
    assert result.results[0].comparison.ok and not result.passed
    assert "monitor.axil.request-wait-limit" in result.safety_hits
    stimulus = api.Stimulus({"in": [(3, 4, 15)]}, [
        api.EventSlot(offers=("in",) if i == 0 else (), controls={"rst": 0, "bready": int(i == 2)})
        for i in range(3)])
    driver = Driver(contract, stimulus)
    for i in range(3):
        driver.observe(i, driver.present(i), {"awready": 1, "wready": 1,
                       "bvalid": int(i > 0), "bresp": 2 if i == 2 else 0})
    assert "monitor.sink-stability" in {
        v.goal for v in Monitor(contract).check(stimulus, driver.trace()).violations}


def test_binding_roundtrip_phase_limit_and_environment_hold_are_explicit():
    contract = _contract()
    restored = api.TaskContract.from_dict(contract.as_dict())
    assert restored.digest == contract.digest
    program = adapter_program_for(restored)
    policy = evaluation_goal_policy(contract, api.CatalogueManifest(()))
    policy.validate(api.CONCRETE_PHASE)
    policy.validate(api.EXACT_ANALYSIS_PHASE)
    state = dict(program.initial_state(contract))
    state["adapter.request"] = (3, 4, 15)
    slot = api.EventSlot(controls={"rst": 0, "bready": 1})
    violations = program.environment_violations(contract, state, slot, AdapterDrive(
        {"awvalid": 1, "wvalid": 1, "bready": 1, "rst": 0}, {"in": (3, 5, 15)}))
    assert violations[0].goal == "monitor.axil.request-sequence"


@pytest.mark.parametrize("change,code", [
    (lambda c: c["endpoints"][0].update(max_outstanding=2), "adapter-axil-logical-mapping"),
    (lambda c: c["endpoints"][0].update(reset_policy="keeps"), "adapter-axil-reset-policy"),
    (lambda c: c["endpoints"][0]["fields"][1]["type"].update(width=16), "adapter-axil-field-width"),
    (lambda c: c["endpoints"][0]["fields"][1]["type"].update(signed=True), "adapter-axil-field-type"),
    (lambda c: c["protocol_instances"][0]["physical"]["bindings"].update(wready="unknown"),
     "adapter-axil-role-ownership"),
])
def test_unimplemented_configurations_are_refused_before_execution(change, code):
    image = _contract().as_dict()
    change(image)
    with pytest.raises(api.Refused, match=code):
        adapter_program_for(api.TaskContract.from_dict(image))


def _full_contract(reverse=False):
    endpoints, instances = [], []
    for write in (True, False):
        base = _contract(write)
        names = {"in": "write" if write else "read", "out": "write_status" if write else "read_result"}
        for endpoint in base.endpoints:
            row = endpoint.as_dict()
            row["name"] = names[endpoint.name]
            endpoints.append(api.Endpoint.from_dict(row))
        row = base.protocol_instances[0].as_dict()
        row["endpoints"] = list(names.values())
        instances.append(api.ProtocolInstance.from_dict(row))
    return api.TaskContract(endpoints[::-1] if reverse else endpoints, base.clocks,
                            protocol_instances=instances[::-1] if reverse else instances)


def _full_stimulus():
    return api.Stimulus({"write": [(0, 0x11223344, 15), (128, 0xAABBCCDD, 5), (0, 0, 0)],
                         "read": [(0,), (0,), (0,)]}, [
        api.EventSlot(reset=True, controls={"rst": 1, "bready": 1, "rready": 1}),
        *[api.EventSlot(offers=("read", "write") if i < 3 else (),
                        controls={"rst": 0, "bready": 1, "rready": 1}) for i in range(10)]])


def _full_reference():
    return load({"behavior.py": b"""class Model:
    def reset(self):
        self.value = 0
    def process_slot(self, slot):
        inputs = slot['inputs']
        outputs = {}
        if 'read' in inputs:
            outputs['read_result'] = {'rdata': self.value, 'rresp': 0}
        if 'write' in inputs:
            request = inputs['write']
            mask = sum(255 << (8 * i) for i in range(4) if request['wstrb'] & (1 << i))
            self.value = (self.value & ~mask) | (request['wdata'] & mask)
            # This TASK retains the write effect even when returning its error.
            outputs['write_status'] = {'bresp': 2 if request['awaddr'] >= 128 else 0}
        return outputs
"""}, config={"class": "Model"})


class _FullDevice:
    kind = "device"
    identity = "axil-device"

    def __init__(self, wrong_order=False):
        self.wrong_order = wrong_order

    def reset(self):
        self.value, self.b, self.r = 0, None, None

    def settle(self, presented, controls):
        self.after = self.value
        write, read = presented["write"], presented["read"]
        if write is not None:
            mask = sum(255 << (8 * i) for i in range(4) if write[2] & (1 << i))
            self.after = (self.value & ~mask) | (write[1] & mask)
        self.next_b = (2 if write[0] >= 128 else 0) if write else (
            None if controls['bready'] else self.b)
        self.next_r = (self.after if self.wrong_order else self.value) if read else (
            None if controls['rready'] else self.r)
        return {"awready": 1, "wready": 1, "arready": 1,
                "bvalid": int(self.b is not None), "bresp": self.b or 0,
                "rvalid": int(self.r is not None), "rdata": self.r or 0, "rresp": 0}

    def edge(self):
        self.value, self.b, self.r = self.after, self.next_b, self.next_r


def test_read_write_composition_keeps_strobes_error_effects_and_atomic_order():
    reference, stimulus = _full_reference(), _full_stimulus()
    for reverse in (False, True):
        contract = _full_contract(reverse)
        task = api.admit(contract, _realization(contract, "axil-device"), reference=reference)
        result = _evaluate(task, stimulus, _FullDevice(), reference)
        assert result.passed, result.as_dict()
        assert api.transfers(result.records[0], "read_result") == [
            (0, 0), (0x11223344, 0), (0x11BB33DD, 0)]
        assert api.transfers(result.records[0], "write_status") == [(0,), (2,), (0,)]
        wrong = _evaluate(task, stimulus, _FullDevice(wrong_order=True), reference)
        assert wrong.complete and not wrong.passed
        assert not wrong.results[0].comparison.ok
    assert _full_contract().digest == _full_contract(True).digest


@pytest.mark.parametrize("write", [False, True])
def test_baseline_and_drain_use_the_adapter_response_acceptance_contract(write):
    from behave.sim.core.input_queue import input_drain_environment
    from behave.sim.core.protocol import receptive_controls
    from behave.sim.stimulus_set import GenerationSpec, _multiclock_controls

    contract = _contract(write)
    response = "bready" if write else "rready"
    assert receptive_controls(contract) == (response,)
    assert _multiclock_controls(contract, (), 2, None)[response] == 1
    baseline = api.build_baseline(contract, GenerationSpec(
        horizon=12, queue_bound=2, reset_hold_cycles=1, drain=6, stall_every=2))
    for stimulus in baseline.stimuli:
        assert not input_drain_environment(stimulus, contract).faults
        assert all(dict(slot.controls)[response] == 1 for slot in stimulus.slots[-6:])
        assert _evaluate_device(contract, stimulus, _Device(write)).passed
    bad = api.Stimulus({"in": [(3, 4, 15)] if write else [(3,)]}, [
        api.EventSlot(offers=("in",), controls={"rst": 0, response: 0}),
        api.EventSlot(controls={"rst": 0, response: 0})])
    assert input_drain_environment(bad, contract).faults[0].kind == "environment.drain-not-receptive"
    framed = api.reset_then(contract, [(3, 4, 15)] if write else [(3,)], drain=4)
    assert all(dict(slot.controls)[response] == 1 for slot in framed.slots[-4:])


@pytest.mark.cosim
@needs_verilator
def test_real_rtl_independent_write_channels_and_response_stalls(tmp_path):
    import json
    from pathlib import Path

    from behave.sim.rtl.artifact import RTLArtifact
    from tests.sim.test_rtl_public_analysis_path import _BaselineOnly
    from tests.support.device_boundary import run_device

    rtl = tmp_path / "axil.sv"
    rtl.write_text("""module axil(input clk, rst, awvalid, wvalid, bready,
      input [31:0] awaddr, wdata, input [3:0] wstrb,
      output awready, wready, output reg bvalid, output reg [1:0] bresp);
      reg aw_seen, w_seen;
      reg [31:0] address;
      assign awready = !aw_seen && !bvalid;
      // Address-first fixture: the DRIVER must still offer W from the outset.
      assign wready = aw_seen && !w_seen && !bvalid;
      always @(posedge clk) begin
        if (rst) begin aw_seen <= 0; w_seen <= 0; bvalid <= 0; bresp <= 0; address <= 0; end
        else begin
          if (awvalid && awready) begin aw_seen <= 1; address <= awaddr; end
          if (wvalid && wready) w_seen <= 1;
          if (aw_seen && wvalid && wready) begin bvalid <= 1; bresp <= address >= 128 ? 2 : 0; end
          if (bvalid && bready) begin bvalid <= 0; aw_seen <= 0; w_seen <= 0; end
        end
      end
    endmodule""")
    contract, reference = _contract(), _reference()
    artifact = RTLArtifact(rtl, "axil", tmp_path / "sim")
    task = api.admit(contract, _realization(contract, artifact.identity), reference=reference)
    stimulus = _stimulus(contract, stall=True)
    trace = run_device(task, stimulus, artifact)
    assert api.transfers(trace, "out") == [(0,), (2,), (0,)]
    assert api.compare_run(task, reference, trace).ok
    assert not Monitor(contract).check(stimulus, trace).violations
    # Also exercise the public preparation/baseline/record boundary, not just
    # a device with an explicitly supplied stimulus.
    from tests.sim.test_task_interface import write_interface

    directory = tmp_path / "task"
    directory.mkdir()
    write_interface(directory, contract)
    (directory / "spec.md").write_text("The normative interface is task.toml's companion JSON.")
    (directory / "rtl.sv").write_text(rtl.read_text())
    (directory / "behavior.py").write_text("""class Model:
    def reset(self): pass
    def process(self, awaddr, wdata, wstrb):
        return {'bresp': 2 if awaddr >= 128 else 0}
""")
    declared = api.contract_of_task(directory)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": (directory / "behavior.py").read_bytes()}, config={"class": "Model"})
    public_task = api.admit(declared, api.realization_of_rtl_source(
        rtl.read_text(), top="axil", contract=declared), reference=reference)
    assert public_task.contract.digest == contract.digest
    handle = api.prepare_rtl(public_task, api.RTLConfig(
        rtl, "axil", tmp_path / "public-rtl", timeout_s=300),
        api.GenerationSpec(64, 4, declared.clocks[0].reset_hold_cycles, 16))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "invocation", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=True,
        analysis_wall_timeout_seconds=180))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    assert result.analysis.verdict is api.Verdict.REFUSED
    assert result.analysis.evidence["refusal"]["kind"] == "rtl-exact-action-schedule-unbound"
    assert json.loads(Path(result.record_path).read_text())["authority"]["passed"] is True


@pytest.mark.cosim
@needs_verilator
def test_real_rtl_read_write_composition_preserves_the_declared_effect_point(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact
    from tests.support.device_boundary import run_device

    rtl = tmp_path / "register_bank.sv"
    rtl.write_text("""module register_bank(input clk, rst, awvalid, wvalid, bready,
      input arvalid, rready, input [31:0] awaddr, wdata, araddr, input [3:0] wstrb,
      output awready, wready, arready, output reg bvalid, rvalid,
      output reg [1:0] bresp, rresp, output reg [31:0] rdata);
      reg [31:0] value, address, data;
      reg [3:0] strobe;
      reg aw_seen, w_seen;
      wire aw_fire = awvalid && awready;
      wire w_fire = wvalid && wready;
      wire [31:0] current_addr = aw_seen ? address : awaddr;
      wire [31:0] current_data = w_seen ? data : wdata;
      wire [3:0] current_strobe = w_seen ? strobe : wstrb;
      wire assembled = (aw_seen || aw_fire) && (w_seen || w_fire) && !bvalid;
      assign awready = !aw_seen && !bvalid;
      assign wready = !w_seen && !bvalid;
      assign arready = !rvalid;
      integer i;
      always @(posedge clk) begin
        if (rst) begin
          value <= 0; address <= 0; data <= 0; strobe <= 0; aw_seen <= 0; w_seen <= 0;
          bvalid <= 0; rvalid <= 0; bresp <= 0; rresp <= 0; rdata <= 0;
        end else begin
          if (aw_fire) begin address <= awaddr; aw_seen <= 1; end
          if (w_fire) begin data <= wdata; strobe <= wstrb; w_seen <= 1; end
          if (assembled) begin
            for (i=0; i<4; i=i+1) if (current_strobe[i]) value[i*8 +: 8] <= current_data[i*8 +: 8];
            bvalid <= 1; bresp <= current_addr >= 128 ? 2 : 0;
          end
          if (arvalid && arready) begin rvalid <= 1; rdata <= value; rresp <= 0; end
          if (bvalid && bready) begin bvalid <= 0; aw_seen <= 0; w_seen <= 0; end
          if (rvalid && rready) rvalid <= 0;
        end
      end
    endmodule""")
    contract, reference, stimulus = _full_contract(), _full_reference(), _full_stimulus()
    artifact = RTLArtifact(rtl, "register_bank", tmp_path / "sim")
    task = api.admit(contract, _realization(contract, artifact.identity), reference=reference)
    trace = run_device(task, stimulus, artifact)
    assert api.transfers(trace, "read_result") == [(0, 0), (0x11223344, 0), (0x11BB33DD, 0)]
    assert api.transfers(trace, "write_status") == [(0,), (2,), (0,)]
    assert api.compare_run(task, reference, trace).ok
    assert not Monitor(contract).check(stimulus, trace).violations
