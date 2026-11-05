"""The public concrete score over a same-clock endpoint product."""

from __future__ import annotations

import shutil

import pytest

from behave.sim import api
from behave.sim.core.monitor import Monitor
from tests.support.device_boundary import evaluate_device, run_device
from tests.support.reference import load

U8 = api.FiniteType(8)


def _endpoint(name, direction):
    return api.Endpoint(
        name,
        direction,
        [api.Field("data", U8, f"{name}_data")],
        api.ready_valid(
            direction, f"{name}_valid", f"{name}_ready"),
        clock="clk",
        kind=api.Kind.TRANSACTION,
    )


def _contract():
    return api.TaskContract(
        endpoints=[
            _endpoint("left", api.Direction.IN),
            _endpoint("right", api.Direction.IN),
            _endpoint("left_result", api.Direction.OUT),
            _endpoint("right_result", api.Direction.OUT),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="two independent same-clock ready/valid streams",
    )


def _realization(identity="same-clock-product-device"):
    ports = []
    for name in ("left", "right"):
        ports.extend((
            api.Port(f"{name}_data", 8, driven_by="environment", clock="clk"),
            api.Port(f"{name}_valid", 1, driven_by="environment", clock="clk"),
            api.Port(f"{name}_ready", 1, driven_by="artifact", clock="clk"),
        ))
    for name in ("left_result", "right_result"):
        ports.extend((
            api.Port(f"{name}_data", 8, driven_by="artifact", clock="clk"),
            api.Port(f"{name}_valid", 1, driven_by="artifact", clock="clk"),
            api.Port(f"{name}_ready", 1, driven_by="environment", clock="clk"),
        ))
    ports.extend((
        api.Port("clk", 1, driven_by="environment"),
        api.Port("rst", 1, driven_by="environment"),
    ))
    return api.Realization(
        "device",
        ports,
        channel_ports={name: [f"{name}_data"] for name in (
            "left", "right", "left_result", "right_result")},
        identity=identity,
    )


def _reference():
    return load({"behavior.py": b"""\
class Model:
    def reset(self):
        pass

    def process_slot(self, slot):
        outputs = {}
        if "left" in slot["inputs"]:
            value = slot["inputs"]["left"]["left_data"]
            outputs["left_result"] = {"left_result_data": (value + 1) & 0xff}
        if "right" in slot["inputs"]:
            value = slot["inputs"]["right"]["right_data"]
            outputs["right_result"] = {"right_result_data": (value + 2) & 0xff}
        return outputs
"""}, config={"class": "Model"})


def _stimulus():
    return api.Stimulus(
        queues={"left": [(10,), (11,)], "right": [(20,)]},
        slots=[
            api.EventSlot(
                reset=True,
                controls={
                    "rst": 1,
                    "left_result_ready": 0,
                    "right_result_ready": 0,
                },
            ),
            api.EventSlot(
                offers=("left", "right"),
                controls={
                    "rst": 0,
                    "left_result_ready": 1,
                    "right_result_ready": 1,
                },
            ),
            api.EventSlot(
                offers=("left", "right"),
                controls={
                    "rst": 0,
                    "left_result_ready": 1,
                    "right_result_ready": 1,
                },
            ),
        ],
        source="right stalls once while left advances",
    )


class _Device:
    kind = "device"
    identity = "same-clock-product-device"

    def reset(self):
        self.slot = 0

    def settle(self, presentations, controls):
        assert type(presentations) is dict
        left_ready = self.slot > 0
        right_ready = self.slot > 1
        left = presentations.get("left")
        right = presentations.get("right")
        left_transfer = bool(
            left is not None and controls["left_valid"] and left_ready)
        right_transfer = bool(
            right is not None and controls["right_valid"] and right_ready)
        result = {
            "left_ready": int(left_ready),
            "right_ready": int(right_ready),
            "left_result_valid": int(left_transfer),
            "right_result_valid": int(right_transfer),
            "left_result_data": 0 if left is None else (left[0] + 1) & 0xff,
            "right_result_data": 0 if right is None else (right[0] + 2) & 0xff,
        }
        self.slot += 1
        return result

    def edge(self):
        pass


def test_public_score_preserves_each_same_clock_endpoint_stream():
    contract = _contract()
    reference = _reference()
    task = api.admit(contract, _realization(), reference=reference)
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))

    evaluation = evaluate_device(
        task,
        [_stimulus()],
        _Device(),
        reference=reference,
        catalogue=catalogue,
    )

    assert evaluation.complete and evaluation.passed
    assert evaluation.accepted == 3
    assert evaluation.mismatch_count == 0
    trace = evaluation.records[0]
    assert api.transfers(trace, "left") == [(10,), (11,)]
    assert api.transfers(trace, "right") == [(20,)]
    assert api.transfers(trace, "left_result") == [(11,), (12,)]
    assert api.transfers(trace, "right_result") == [(22,)]


class _UnstableOutputDevice:
    kind = "device"
    identity = "same-clock-product-device"

    def reset(self):
        self.slot = 0

    def settle(self, _presentations, _controls):
        data = 0 if self.slot == 0 else self.slot + 6
        result = {
            "left_ready": 0,
            "right_ready": 0,
            "left_result_valid": int(self.slot > 0),
            "right_result_valid": 0,
            "left_result_data": data,
            "right_result_data": 0,
        }
        self.slot += 1
        return result

    def edge(self):
        pass


def test_stall_stability_is_checked_for_each_output_endpoint():
    contract = _contract()
    reference = _reference()
    task = api.admit(contract, _realization(), reference=reference)
    stimulus = api.Stimulus(
        queues={"left": [], "right": []},
        slots=[
            api.EventSlot(
                reset=True,
                controls={
                    "rst": 1,
                    "left_result_ready": 0,
                    "right_result_ready": 0,
                },
            ),
            api.EventSlot(controls={
                "rst": 0,
                "left_result_ready": 0,
                "right_result_ready": 1,
            }),
            api.EventSlot(controls={
                "rst": 0,
                "left_result_ready": 0,
                "right_result_ready": 1,
            }),
        ],
        source="left output changes while stalled",
    )

    trace = run_device(task, stimulus, _UnstableOutputDevice())
    report = Monitor(contract).check(stimulus, trace)
    violations = [item for item in report.violations
                  if item.goal == "monitor.sink-stability"]

    assert len(violations) == 1
    assert violations[0].endpoint == "left_result"
    assert report.opportunities["monitor.sink-stability"] == 1


def _toolchain_present():
    from behave.core.eda import tool_env

    env = tool_env()
    return all(shutil.which(tool, path=env["PATH"])
               for tool in ("verilator", "make", "cocotb-config"))


ENDPOINT_PRODUCT_RTL = """\
module endpoint_product(
    input wire clk, input wire rst,
    input wire left_valid, output wire left_ready,
    input wire [7:0] left_data,
    input wire right_valid, output wire right_ready,
    input wire [7:0] right_data,
    output wire left_result_valid, input wire left_result_ready,
    output wire [7:0] left_result_data,
    output wire right_result_valid, input wire right_result_ready,
    output wire [7:0] right_result_data
);
    assign left_ready = !rst && left_result_ready;
    assign right_ready = !rst && right_result_ready;
    assign left_result_valid = left_valid && !rst;
    assign right_result_valid = right_valid && !rst;
    assign left_result_data = left_data + 8'd1;
    assign right_result_data = right_data + 8'd2;
endmodule
"""


@pytest.mark.cosim
@pytest.mark.skipif(not _toolchain_present(), reason="RTL toolchain is unavailable")
def test_generated_rtl_driver_carries_the_full_endpoint_product(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact

    source = tmp_path / "endpoint_product.sv"
    source.write_text(ENDPOINT_PRODUCT_RTL)
    artifact = RTLArtifact(source, "endpoint_product", tmp_path / "sim")
    contract = _contract()
    reference = _reference()
    task = api.admit(
        contract, _realization(artifact.identity), reference=reference)
    stimulus = api.Stimulus(
        queues={"left": [(4,)], "right": [(9,)]},
        slots=[
            api.EventSlot(
                reset=True,
                controls={
                    "rst": 1,
                    "left_result_ready": 0,
                    "right_result_ready": 0,
                }),
            api.EventSlot(
                offers=("left", "right"),
                controls={
                    "rst": 0,
                    "left_result_ready": 1,
                    "right_result_ready": 1,
                }),
        ],
        source="one simultaneous endpoint-product transfer",
    )

    trace = run_device(task, stimulus, artifact)

    assert api.transfers(trace, "left") == [(4,)]
    assert api.transfers(trace, "right") == [(9,)]
    assert api.transfers(trace, "left_result") == [(5,)]
    assert api.transfers(trace, "right_result") == [(11,)]
    assert api.compare_run(task, reference, trace).ok


@pytest.mark.cosim
@pytest.mark.parametrize("right_count,wrong", [(0, False), (3, False), (3, True)])
def test_public_runtime_replay_completes_declared_same_clock_product(tmp_path, right_count, wrong):
    from behave.sim.core.runtime_schedule import validate_resolution
    from behave.sim.runtime_execution import completion_targets

    base = _contract()
    contract = api.TaskContract([
        api.Endpoint(endpoint.name, endpoint.direction, endpoint.fields, endpoint.adapter,
                     clock=endpoint.clock, kind=endpoint.kind,
                     answers_per_transaction=1 if endpoint.direction is api.Direction.OUT else None)
        for endpoint in base.endpoints], base.clocks, protocol_instances=[
            api.ProtocolInstance(name, (name, name + "_result")) for name in ("left", "right")])
    text = ENDPOINT_PRODUCT_RTL.replace("left_data + 8'd1", "left_data + 8'd3") if wrong else ENDPOINT_PRODUCT_RTL
    source = tmp_path / "endpoint_product.sv"
    source.write_text(text)
    reference = _reference()
    task = api.admit(contract, api.realization_of_rtl_source(
        text, "same-clock declared product", top="endpoint_product", contract=contract), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(source, "endpoint_product", tmp_path / "sim"))
    request = api.Stimulus(queues={"left": [(4,), (5,)], "right": [(9,)] * right_count},
                          replay_policy=api.ReplayPolicy(reset_hold_cycles=1, drain_slots=3, stall_every=3))
    expected = completion_targets(task, request, reference)
    assert expected == {"left_result": 2, "right_result": right_count}
    authority = handle.replay((request,), reference=reference)
    evaluation = authority.evaluation
    assert evaluation.complete, evaluation.as_dict()
    assert evaluation.passed is not wrong, evaluation.as_dict()
    assert evaluation.accepted == 2 + right_count
    trace = evaluation.records[0]
    assert api.transfers(trace, "left") == [(4,), (5,)]
    assert api.transfers(trace, "right") == [(9,)] * right_count
    assert len(api.transfers(trace, "left_result")) == 2
    assert len(api.transfers(trace, "right_result")) == right_count
    assert bool(evaluation.mismatch_count) is wrong
    validate_resolution(contract, request, trace.stimulus, trace.records, expected)
    # The resolved finite schedule retains the exact native acceptance order.
    fixed = handle.replay((trace.stimulus,), reference=reference).evaluation
    assert fixed.passed is not wrong
    # Runtime and fixed requests are different domains. All execution evidence
    # must match, but erasing the original untimed request would lose provenance.
    original, replayed = trace.as_dict(), fixed.records[0].as_dict()
    assert original.pop("request") == request.as_dict()
    assert "request" not in replayed
    assert original.pop("domain") != replayed.pop("domain")
    assert replayed == original


def test_runtime_completion_does_not_guess_undeclared_slot_pairing():
    from behave.sim.runtime_execution import completion_targets

    contract, reference = _contract(), _reference()
    task = api.admit(contract, _realization(), reference=reference)
    request = api.Stimulus(queues={"left": [(4,)], "right": [(9,)]},
                          replay_policy=api.ReplayPolicy.for_contract(contract))
    with pytest.raises(api.Refused, match="runtime-completion-product-undeclared"):
        completion_targets(task, request, reference)
