"""The atomic clock product through the public scoring boundary.

These are deliberately not tests of the legacy multi-clock replay.  A witness is
decoded into the same ``Stimulus`` the RTL path consumes, the admitted signal-level
artifact is run through the private live-device test seam, and expected transactions are reconstructed
from the artifact's accepted input transfers by the isolated reference.
"""


import pytest

from behave.sim import api, reference_isolated
from behave.sim.behavior.multiclock import (
    ATOMIC_GENERATION_POLICY,
    ATOMIC_WITNESS_VERSION,
)
from behave.sim.core.clock_schedule import ClockTiming, GlobalSchedule
from behave.sim.core.refusal import Refused
from behave.sim.stimulus_set import multiclock_stimulus_from_witness
from tests.support.device_boundary import evaluate_device

U8 = api.FiniteType(8)


def _contract(*, edges=None, a_input_type=U8):
    edges = dict(edges or {})
    endpoints = []
    for domain in ("a", "b"):
        endpoints.extend((
            api.Endpoint(
                f"{domain}_in", api.Direction.IN,
                [api.Field(
                    "data", a_input_type if domain == "a" else U8,
                    f"{domain}_in_data")],
                api.ready_valid(api.Direction.IN, f"{domain}_in_valid",
                                f"{domain}_in_ready"),
                clock=domain, kind=api.Kind.TRANSACTION),
            api.Endpoint(
                f"{domain}_out", api.Direction.OUT,
                [api.Field("data", U8, f"{domain}_out_data")],
                api.ready_valid(api.Direction.OUT, f"{domain}_out_valid",
                                f"{domain}_out_ready"),
                clock=domain, kind=api.Kind.TRANSACTION),
        ))
    return api.TaskContract(
        endpoints,
        [api.ClockDomain("a", "a_clk", "a_rst",
                         edge=edges.get("a", "rising")),
         api.ClockDomain("b", "b_clk", "b_rst",
                         edge=edges.get("b", "rising"))],
        source="two-domain test task")


def _same_domain_product_contract():
    contract = _contract()
    endpoints = list(contract.endpoints)
    endpoints.insert(1, api.Endpoint(
        "a_aux", api.Direction.IN,
        [api.Field("data", U8, "a_aux_data")],
        api.ready_valid(api.Direction.IN, "a_aux_valid", "a_aux_ready"),
        clock="a", kind=api.Kind.TRANSACTION))
    endpoints.insert(3, api.Endpoint(
        "a_side", api.Direction.OUT,
        [api.Field("data", U8, "a_side_data")],
        api.ready_valid(api.Direction.OUT, "a_side_valid", "a_side_ready"),
        clock="a", kind=api.Kind.TRANSACTION))
    return api.TaskContract(
        endpoints, contract.clocks,
        source="two-domain task with an input product in domain a")


def _realization(identity="atomic-device"):
    ports = []
    channels = {}
    for domain in ("a", "b"):
        ports.extend((
            api.Port(f"{domain}_clk", 1, driven_by="environment"),
            api.Port(f"{domain}_rst", 1, driven_by="environment"),
            api.Port(f"{domain}_in_data", 8, driven_by="environment",
                     clock=domain),
            api.Port(f"{domain}_in_valid", 1, driven_by="environment",
                     clock=domain),
            api.Port(f"{domain}_in_ready", 1, driven_by="artifact",
                     clock=domain),
            api.Port(f"{domain}_out_data", 8, driven_by="artifact",
                     clock=domain),
            api.Port(f"{domain}_out_valid", 1, driven_by="artifact",
                     clock=domain),
            api.Port(f"{domain}_out_ready", 1, driven_by="environment",
                     clock=domain),
        ))
        channels[f"{domain}_in"] = [f"{domain}_in_data"]
        channels[f"{domain}_out"] = [f"{domain}_out_data"]
    return api.Realization(
        "device", ports, channel_ports=channels, identity=identity)


def _same_domain_product_realization(identity="atomic-same-domain-product"):
    base = _realization(identity=identity)
    ports = list(base.ports.values()) + [
        api.Port("a_aux_data", 8, driven_by="environment", clock="a"),
        api.Port("a_aux_valid", 1, driven_by="environment", clock="a"),
        api.Port("a_aux_ready", 1, driven_by="artifact", clock="a"),
        api.Port("a_side_data", 8, driven_by="artifact", clock="a"),
        api.Port("a_side_valid", 1, driven_by="artifact", clock="a"),
        api.Port("a_side_ready", 1, driven_by="environment", clock="a"),
    ]
    channels = {name: list(names) for name, names in base.channel_ports.items()}
    channels["a_aux"] = ["a_aux_data"]
    channels["a_side"] = ["a_side_data"]
    return api.Realization(
        "device", ports, channel_ports=channels, identity=identity)


REFERENCE = b"""
class Reference:
    def reset(self):
        pass

    def process_a(self, a_in_data):
        return {"a_out_data": (a_in_data * 2) & 255}

    def process_b(self, b_in_data):
        return {"b_out_data": (b_in_data * 3) & 255}
"""


def _reference():
    return api.load_reference(
        reference_isolated.ADAPTER, {"behavior.py": REFERENCE},
        config={"class": "Reference"}, loader="tests")


def _same_domain_product_reference():
    return api.load_reference(
        reference_isolated.ADAPTER,
        {"behavior.py": b"""
class Reference:
    def reset(self):
        pass

    def process_a(self, a_aux_data, a_in_data):
        return {
            "a_out_data": (a_aux_data + a_in_data) & 255,
            "a_side_data": (a_aux_data - a_in_data) & 255,
        }

    def process_b(self, b_in_data):
        return {"b_out_data": (b_in_data * 3) & 255}
"""},
        config={"class": "Reference"}, loader="tests")


def _witness(*, embedded_expected=None, edges=None):
    edges = dict(edges or {})
    timings = (
        ClockTiming("a", 2, clock="a_clk", reset="a_rst",
                    edge=edges.get("a", "rising")),
        ClockTiming("b", 3, clock="b_clk", reset="b_rst",
                    edge=edges.get("b", "rising")),
    )
    schedule = GlobalSchedule.with_reset_prefix(
        timings, 5, reset_edges={"a": 1, "b": 1})
    values = {"a": iter((2, 4, 6, 8)), "b": iter((3, 5, 7, 9))}
    slots = []
    for slot in schedule.slots:
        actions = []
        for domain in slot.events:
            if domain not in slot.resets:
                actions.append(["drive", domain, [next(values[domain])]])
        slots.append({**slot.as_dict(), "actions": actions})
    return {
        "version": ATOMIC_WITNESS_VERSION,
        "generation_policy": ATOMIC_GENERATION_POLICY,
        "schedule": schedule.as_dict(),
        "schedule_digest": schedule.digest,
        "memory_environment": {"version": "memory-environment/3", "runs": []},
        "domain_inputs": {"a": ["a_in_data"], "b": ["b_in_data"]},
        "slots": slots,
        # This field is intentionally hostile: the scoring boundary must not read
        # provider-computed answers as the reference stream.
        "expected": ([{"forged": True}] * len(slots)
                     if embedded_expected is None else embedded_expected),
        "source": "test-atomic-provider",
    }


def _same_domain_product_witness():
    witness = _witness()
    # TaskContract canonicalises endpoint order by logical name; this is the exact
    # positional signature handed to the source handler and recorded by the witness.
    witness["domain_inputs"]["a"] = ["a_aux_data", "a_in_data"]
    auxiliary = iter((11, 13, 17, 19))
    for row in witness["slots"]:
        for action in row["actions"]:
            if action[1] == "a":
                action[2].insert(0, next(auxiliary))
    return witness


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("version", None, "atomic-witness-version-unsupported"),
        ("version", "atomic-witness/0", "atomic-witness-version-unsupported"),
        ("generation_policy", None, "atomic-generation-policy-unsupported"),
        ("generation_policy", "atomic-generation/0",
         "atomic-generation-policy-unsupported"),
    ],
)
def test_atomic_witness_requires_its_wire_and_generation_policy_identity(
        field, value, reason):
    witness = _witness()
    if value is None:
        witness.pop(field)
    else:
        witness[field] = value

    with pytest.raises(Refused, match=reason):
        multiclock_stimulus_from_witness(_contract(), witness)


def test_multiclock_admission_requires_a_registered_atomic_reference_adapter():
    from tests.support import reference as test_reference

    reference = test_reference.load(
        {"behavior.py": b"""
class Reference:
    def reset(self):
        pass
    def process(self, **inputs):
        return inputs
"""},
        config={"class": "Reference"})

    with pytest.raises(Refused, match="atomic-reference-adapter-unsupported"):
        api.admit(_contract(), _realization(), reference=reference)


def test_atomic_witness_splits_one_domain_invocation_into_endpoint_queues():
    contract = _same_domain_product_contract()
    stimulus = multiclock_stimulus_from_witness(
        contract, _same_domain_product_witness())
    queues = dict(stimulus.queues)

    assert queues["a_in"] == ((2,), (4,), (6,), (8,))
    assert queues["a_aux"] == ((11,), (13,), (17,), (19,))
    for slot in stimulus.slots:
        if "a" in slot.ticks and "a" not in slot.resets:
            assert {"a_in", "a_aux"} <= set(slot.offers)

    malformed = _same_domain_product_witness()
    next(action for row in malformed["slots"] for action in row["actions"]
         if action[1] == "a")[2].pop()
    with pytest.raises(Refused, match="atomic-witness-input-arity-mismatch"):
        multiclock_stimulus_from_witness(contract, malformed)


def test_atomic_witness_decodes_recursive_port_patterns_to_logical_values():
    value_type = api.ValueType.enumeration(("idle", "run", "hold"))
    contract = _contract(a_input_type=value_type)
    witness = _witness()
    patterns = iter((0, 1, 2, 0))
    for row in witness["slots"]:
        for action in row["actions"]:
            if action[1] == "a":
                action[2][0] = next(patterns)

    stimulus = multiclock_stimulus_from_witness(contract, witness)
    assert dict(stimulus.queues)["a_in"] == (
        ("idle",), ("run",), ("hold",), ("idle",))

    invalid = _witness()
    next(action for row in invalid["slots"] for action in row["actions"]
         if action[1] == "a")[2][0] = 3
    with pytest.raises(Refused, match="authority-provider-packed-value-invalid"):
        multiclock_stimulus_from_witness(contract, invalid)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"drain": True}, "atomic-schedule-drain-invalid"),
        ({"drain": "1"}, "atomic-schedule-drain-invalid"),
        ({"drain": -1}, "atomic-schedule-drain-invalid"),
        ({"stall_every": False}, "atomic-schedule-stall-invalid"),
        ({"stall_every": "2"}, "atomic-schedule-stall-invalid"),
        ({"stall_every": -1}, "atomic-schedule-stall-invalid"),
        ({"source": object()}, "atomic-schedule-source-invalid"),
    ],
)
def test_atomic_stimulus_policy_values_are_not_silently_coerced(kwargs, reason):
    with pytest.raises(Refused, match=reason):
        multiclock_stimulus_from_witness(
            _same_domain_product_contract(),
            _same_domain_product_witness(), **kwargs)


class _AtomicDevice:
    kind = "device"
    identity = "atomic-device"

    def __init__(self, gains=(2, 3)):
        self.gains = dict(zip(("a", "b"), gains))

    def atomic_step(self, slot, drive):
        result = {}
        for domain in slot.events:
            result[f"{domain}_in_ready"] = 1
            valid = 0 if domain in slot.resets else drive[f"{domain}_in_valid"]
            result[f"{domain}_out_valid"] = valid
            result[f"{domain}_out_data"] = (
                drive[f"{domain}_in_data"] * self.gains[domain]) & 255
        return result


class _AtomicSameDomainProductDevice:
    kind = "device"
    identity = "atomic-same-domain-product"

    def atomic_step(self, slot, drive):
        result = {}
        for domain in slot.events:
            if domain == "a":
                result["a_in_ready"] = 1
                result["a_aux_ready"] = 1
                result["a_out_valid"] = (
                    0 if domain in slot.resets else
                    drive["a_in_valid"] & drive["a_aux_valid"])
                result["a_out_data"] = (
                    drive["a_in_data"] + drive["a_aux_data"]) & 255
                result["a_side_valid"] = result["a_out_valid"]
                result["a_side_data"] = (
                    drive["a_aux_data"] - drive["a_in_data"]) & 255
            else:
                result["b_in_ready"] = 1
                result["b_out_valid"] = (
                    0 if domain in slot.resets else drive["b_in_valid"])
                result["b_out_data"] = (drive["b_in_data"] * 3) & 255
        return result


class _AtomicPartialProductDevice(_AtomicSameDomainProductDevice):
    def atomic_step(self, slot, drive):
        result = super().atomic_step(slot, drive)
        if "a" in slot.events and "a" not in slot.resets:
            result["a_aux_ready"] = 0
        return result


def _evaluate(device, *, edges=None):
    contract = _contract(edges=edges)
    reference = _reference()
    task = api.admit(contract, _realization(), reference=reference)
    stimulus = multiclock_stimulus_from_witness(
        contract, _witness(edges=edges))
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    return evaluate_device(
        task, (stimulus,), device, reference=reference, catalogue=catalogue)


def test_public_score_uses_atomic_edges_and_the_frozen_reference():
    got = _evaluate(_AtomicDevice())

    assert got.passed and got.complete
    assert got.accepted > 0
    assert not got.mismatches and not got.infrastructure
    trace = got.records[0]
    assert trace.stimulus.clock_schedule is not None
    assert any(len(slot.ticks) == 2 for slot in trace.stimulus.slots)


def test_atomic_artifact_state_is_audit_evidence_not_reference_state():
    from behave.sim.core.atomic import run_atomic_in_memory

    contract = _contract()
    reference = _reference()
    task = api.admit(contract, _realization(), reference=reference)
    stimulus = multiclock_stimulus_from_witness(contract, _witness())
    trace = run_atomic_in_memory(contract, stimulus, _AtomicDevice())
    observed = api.AuditedTrace(
        trace.stimulus,
        initial_state={"frontend.q": 123},
        records=trace.records,
        completion=trace.completion,
        domain=trace.domain,
        artifact=trace.artifact,
    )

    comparison = api.compare_run(task, reference, observed)

    assert comparison.ok


def test_a_wrong_domain_result_is_a_normal_score_mismatch():
    got = _evaluate(_AtomicDevice(gains=(2, 4)))

    assert not got.passed
    assert got.mismatch_count > 0
    assert not got.infrastructure


def test_public_atomic_score_consumes_a_same_domain_endpoint_product():
    contract = _same_domain_product_contract()
    reference = _same_domain_product_reference()
    task = api.admit(
        contract, _same_domain_product_realization(), reference=reference)
    stimulus = multiclock_stimulus_from_witness(
        contract, _same_domain_product_witness())
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))

    got = evaluate_device(
        task, (stimulus,), _AtomicSameDomainProductDevice(),
        reference=reference, catalogue=catalogue)

    assert got.passed and got.complete
    assert not got.mismatches and not got.infrastructure
    assert got.accepted > 0


def test_partial_acceptance_of_one_domain_product_cannot_forge_agreement():
    contract = _same_domain_product_contract()
    reference = _same_domain_product_reference()
    task = api.admit(
        contract, _same_domain_product_realization(), reference=reference)
    stimulus = multiclock_stimulus_from_witness(
        contract, _same_domain_product_witness(), drain=2)
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))

    got = evaluate_device(
        task, (stimulus,), _AtomicPartialProductDevice(),
        reference=reference, catalogue=catalogue)

    # The reference cannot be advanced with a value for only half of its declared
    # handler signature.  The comparison is therefore withheld by name, never filled
    # with a guessed value or reported as agreement.
    assert not got.complete
    assert not got.passed
    assert got.uncompared
    _, failure = got.no_result_failures[0]
    assert failure.origin is api.FailureOrigin.REFERENCE
    assert "atomic-reference-input-product-partial" in str(got.uncompared)


def test_mixed_task_owned_edges_are_admitted_and_a_schedule_cannot_swap_one():
    edges = {"a": "rising", "b": "falling"}
    contract = _contract(edges=edges)
    stimulus = multiclock_stimulus_from_witness(
        contract, _witness(edges=edges))

    assert stimulus.faults_against(contract) == []
    forged = multiclock_stimulus_from_witness(
        contract, _witness(edges={"a": "rising", "b": "rising"}))
    faults = forged.faults_against(contract)
    assert any("active edge" in fault for fault in faults)
    assert (stimulus.clock_schedule.digest
            != forged.clock_schedule.digest)


@pytest.mark.cosim
@pytest.mark.parametrize("spurious", [False, True])
def test_production_rtl_replay_consumes_the_same_mixed_edge_stimulus(tmp_path, spurious):
    """The signal-level RTL path consumes the public atomic driver, not smoke replay."""
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.tool_protocol import ToolIsolationError

    rtl = tmp_path / "atomic_pair.sv"
    rtl.write_text(r"""
module atomic_pair (
    input logic a_clk, input logic a_rst,
    input logic a_in_valid, output logic a_in_ready,
    input logic [7:0] a_in_data,
    output logic a_out_valid, input logic a_out_ready,
    output logic [7:0] a_out_data,
    input logic b_clk, input logic b_rst,
    input logic b_in_valid, output logic b_in_ready,
    input logic [7:0] b_in_data,
    output logic b_out_valid, input logic b_out_ready,
    output logic [7:0] b_out_data
);
    assign a_in_ready = 1'b1;
    assign a_out_valid = a_in_valid & ~a_rst;
    assign a_out_data = a_in_data * 8'd2;
    assign b_in_ready = 1'b1;
    assign b_out_valid = (b_in_valid | SPURIOUS) & ~b_rst;
    assign b_out_data = b_in_data * 8'd3;
endmodule
""".lstrip().replace("SPURIOUS", "1'b1" if spurious else "1'b0"))
    artifact = RTLArtifact(rtl, "atomic_pair", tmp_path / "rtl-work")
    contract = _contract(edges={"a": "rising", "b": "falling"})
    reference = _reference()
    task = api.admit(
        contract, _realization(identity=artifact.identity), reference=reference)
    stimulus = multiclock_stimulus_from_witness(
        contract, _witness(edges={"a": "rising", "b": "falling"}), drain=6)
    catalogue = api.evaluation_manifest(task, artifact.frontend_catalogue())

    try:
        got = evaluate_device(
            task, (stimulus,), artifact, reference=reference,
            catalogue=catalogue)
    except ToolIsolationError as exc:
        if exc.code == "isolation-capability-missing":
            pytest.skip(f"production RTL isolation unavailable: {exc.why}")
        raise

    assert got.complete
    assert got.passed is not spurious
    assert got.accepted > 0
    assert not got.infrastructure
    assert bool(got.mismatches) is spurious
    assert got.records[0].stimulus.clock_schedule.digest == (
        stimulus.clock_schedule.digest)
