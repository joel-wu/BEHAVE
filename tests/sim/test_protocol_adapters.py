"""Executable protocol adapters on the public admission/replay/evaluation path."""

from __future__ import annotations

import shutil

import pytest

from behave.sim import api
from behave.sim.core.analysis import audit_environment_assumptions
from behave.sim.core.compare import compare
from behave.sim.core.monitor import Monitor
from behave.sim.core.physical_value import pack_value, packed_width, unpack_value
from behave.sim.core.protocol import (
    AdapterChannel,
    AdapterDrive,
    AdapterTransition,
    AdapterViolation,
    adapter_program_for,
    payload_signals,
    registered_adapter,
)
from behave.sim.core.protocol_exact import AdapterExactTable
from behave.sim.core.record_value import FrozenValueMap
from behave.sim.core.reference import ReferenceModel
from tests.support.device_boundary import evaluate_device, run_device

U8 = api.FiniteType(8)


def test_recursive_logical_transactions_cross_one_packed_port_per_field():
    meta = api.ValueType.record({
        "flags": api.ValueType.array(2, api.ValueType.boolean()),
        "mode": api.ValueType.enumeration(("idle", "run", "hold")),
    })
    signed = api.ValueType.integer(api.FiniteType(4, signed=True))
    contract = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("meta", meta, "in_meta"),
                 api.Field("delta", signed, "in_delta")],
                api.ready_valid("in", "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("meta", meta, "out_meta"),
                 api.Field("delta", signed, "out_delta")],
                api.ready_valid("out", "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
    )
    value = ({"flags": (True, False), "mode": "hold"}, -3)
    source, sink = contract.inputs()[0], contract.outputs()[0]
    assert packed_width(meta) == 4

    program = adapter_program_for(contract)
    state = program.initial_state(contract)
    slot = api.EventSlot(
        offers=("in",), controls={"rst": 0, "out_ready": 1})
    drive = program.drive(
        contract, state, slot, {"in": value}, dict(slot.controls))
    assert payload_signals(source, drive.presentation("in")) == {
        "in_meta": pack_value(meta, value[0]),
        "in_delta": pack_value(signed, value[1]),
    }

    transition = program.step(contract, state, slot, drive, {
        "in_ready": 1,
        "out_valid": 1,
        "out_meta": pack_value(meta, value[0]),
        # Cocotb may report a signed physical port as a negative integer.
        "out_delta": -3,
    })
    frozen_value = (unpack_value(meta, pack_value(meta, value[0])), -3)
    assert transition.accepted_inputs["in"] == frozen_value
    assert transition.emitted_outputs["out"] == frozen_value
    assert sink.admits(transition.emitted_outputs["out"]) == []

    # Reference rows remain logical until this boundary.  Pre-packing them here would
    # make a recursive record look like an integer value on the second normalization.
    reference = object.__new__(ReferenceModel)
    expected = reference._answers_for(sink, {
        "out_meta": value[0], "out_delta": value[1],
    })
    assert compare(sink, expected, [frozen_value]).ok


def test_adapter_records_freeze_recursive_logical_payloads():
    payload = ({"flag": True, "rows": [1, 2]},)

    channel = AdapterChannel(
        offered=True, ready=True, transferred=True, payload=payload)
    drive = AdapterDrive({}, {"in": payload})
    transition = AdapterTransition(
        state={}, accepted_inputs={"in": payload},
        emitted_outputs={"out": payload})

    payload[0]["rows"].append(3)
    for got in (
        channel.payload,
        drive.presentations["in"],
        transition.accepted_inputs["in"],
        transition.emitted_outputs["out"],
    ):
        assert type(got[0]) is FrozenValueMap
        assert got[0]["flag"] is True
        assert got[0]["rows"] == (1, 2)


def test_scalar_payload_reuse_keeps_exact_types_and_recursive_validation():
    from behave.sim.core.protocol import _payload_record

    value = (None, False, 1, "tag")
    assert _payload_record(value, "payload") is value
    assert type(_payload_record((True, 1), "payload")[0]) is bool
    assert type(_payload_record((True, 1), "payload")[1]) is int

    class Box(int):
        pass

    with pytest.raises(TypeError, match="invalid finite value"):
        _payload_record((Box(1),), "payload")
    with pytest.raises(TypeError, match="non-finite"):
        _payload_record((float("nan"),), "payload")
    assert _payload_record((1.25,), "payload") == (1.25,)
    loop = []
    loop.append(loop)
    with pytest.raises(TypeError, match="reference cycle"):
        _payload_record((loop,), "payload")


def _toolchain_present():
    from behave.core.eda import tool_env

    env = tool_env()
    return all(shutil.which(tool, path=env["PATH"])
               for tool in ("verilator", "make", "cocotb-config"))


needs_verilator = pytest.mark.skipif(
    not _toolchain_present(),
    reason="verilator, make or cocotb-config is not reachable")


def _contract(kind, *, version=1, reset_policy=api.ResetPolicy.CLEARS,
              deadline=None):
    if kind == "ready-valid":
        source = api.ready_valid("in", "in_valid", "in_ready", version=version)
        sink = api.ready_valid("out", "out_valid", "out_ready", version=version)
    else:
        source = api.start_done("in", "start", "done", version=version)
        sink = api.start_done("out", "start", "done", version=version)
    return api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN, [api.Field("a", U8, "in_a")],
                source, clock="clk", kind=api.Kind.TRANSACTION,
                reset_policy=reset_policy, response_deadline=deadline),
            api.Endpoint(
                "out", api.Direction.OUT, [api.Field("y", U8, "out_y")],
                sink, clock="clk", kind=api.Kind.TRANSACTION,
                answers_per_transaction=1, reset_policy=reset_policy),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source=f"{kind}/v{version} test contract")


def _realization(kind, identity):
    controls = (
        [api.Port("in_valid", 1, driven_by="environment"),
         api.Port("in_ready", 1, driven_by="artifact"),
         api.Port("out_valid", 1, driven_by="artifact"),
         api.Port("out_ready", 1, driven_by="environment")]
        if kind == "ready-valid"
        else [api.Port("start", 1, driven_by="environment"),
              api.Port("done", 1, driven_by="artifact")]
    )
    return api.Realization(
        "device",
        [api.Port("in_a", 8, driven_by="environment", clock="clk"),
         api.Port("out_y", 8, driven_by="artifact", clock="clk"),
         *controls,
         api.Port("clk", 1, driven_by="environment"),
         api.Port("rst", 1, driven_by="environment")],
        channel_ports={"in": ["in_a"], "out": ["out_y"]},
        identity=identity)


def _reference():
    source = b"""\
class Model:
    def reset(self):
        pass

    def process(self, in_a):
        return {"out_y": (in_a * 2) & 0xff}
"""
    from tests.support.reference import load

    return load({"behavior.py": source}, config={"class": "Model"})


class _ReadyValidDoubling:
    kind = "device"
    identity = "rv-device"

    def reset(self):
        self.held = None
        self.after = None

    def settle(self, presented, controls):
        taking = self.held is not None and controls["out_ready"]
        ready = self.held is None or taking
        self.after = None if taking else self.held
        if presented is not None and controls["in_valid"] and ready:
            self.after = (presented[0] * 2) & 0xFF
        return {"in_ready": int(ready), "out_valid": int(self.held is not None),
                "out_y": self.held or 0}

    def edge(self):
        self.held = self.after


class _StartDoneDoubling:
    kind = "device"
    identity = "sd-device"

    def __init__(self, latency=0, spurious=False):
        self.latency = latency
        self.spurious = spurious
        self.reset()

    def reset(self):
        self.pending = None
        self.remaining = 0
        self.after = (None, 0)

    def settle(self, presented, controls):
        done = self.spurious or (self.pending is not None and self.remaining == 0)
        pending, remaining = self.pending, max(0, self.remaining - 1)
        if done:
            pending, remaining = None, 0
        if presented is not None and controls["start"] and self.pending is None:
            pending, remaining = (presented[0] * 2) & 0xFF, self.latency
        self.after = pending, remaining
        return {"done": int(done), "out_y": self.pending or 0}

    def edge(self):
        self.pending, self.remaining = self.after


def _rv_stimulus():
    return api.Stimulus(
        {"in": [(3,)]},
        [api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 0}),
         api.EventSlot(offers=["in"], controls={"rst": 0, "out_ready": 1}),
         api.EventSlot(controls={"rst": 0, "out_ready": 1})],
        source="ready-valid schedule")


def _sd_stimulus(*body, queue=None):
    if queue is None:
        offered = sum(
            "in" in slot.offers and not slot.reset for slot in body)
        queue = ((3,), (5,))[:offered]
    return api.Stimulus(
        {"in": queue},
        [api.EventSlot(reset=True, controls={"rst": 1}), *body],
        source="start-done schedule")


def _evaluate(task, stimulus, device, reference):
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    return evaluate_device(
        task, [stimulus], device, reference=reference, catalogue=catalogue)


def test_start_done_registry_is_finite_total_and_immutable():
    contract = _contract("start-done")
    program = adapter_program_for(contract)

    assert len(program.state_space(contract)) == 2
    assert {tuple(action.items())
            for action in program.environment_action_space(contract)} == {
                (("start", 0),), (("start", 1),)}
    for state in program.state_space(contract):
        for starts in (False, True):
            slot = api.EventSlot(
                offers=["in"] if starts else (), controls={"rst": 0})
            drive = program.drive(
                contract, state, slot, (3,) if starts else None,
                dict(slot.controls))
            for done in (0, 1):
                stepped = program.step(
                    contract, state, slot, drive,
                    {"done": done, "out_y": 6})
                assert stepped.state in program.state_space(contract)
                assert set(stepped.accepted_inputs) <= {"in"}
                assert set(stepped.emitted_outputs) <= {"out"}
                assert all(channel.ready is None
                           for channel in stepped.channels.values())
    with pytest.raises(AttributeError):
        program.version = 7

    reset = api.EventSlot(reset=True, controls={"rst": 1})
    drive = program.drive(contract, program.state_space(contract)[1], reset,
                          None, dict(reset.controls))
    stepped = program.step(
        contract, program.state_space(contract)[1], reset, drive,
        {"done": "x", "out_y": "x"})
    assert stepped.state == {"adapter.age": 0, "adapter.busy": False}


def test_start_done_exact_table_preserves_state_transfers_and_goals():
    contract = _contract("start-done")
    program = adapter_program_for(contract)
    table = AdapterExactTable(program, contract)

    assert table.initial_state == 0
    assert len(table.states) == 2
    assert table.payload_independence == \
        program.exact_control_product_policy
    assert table.as_dict()["payload_independence"] == \
        program.exact_control_product_policy
    idle_start_and_done = next(
        row for row in table.transitions
        if row.state == 0 and not row.reset
        and dict(row.presented) == {"in": True}
        and dict(row.observed) == {"done": True})
    assert idle_start_and_done.next_state == 1
    assert idle_start_and_done.accepted == ("in",)
    assert idle_start_and_done.emitted == ("out",)
    assert idle_start_and_done.goals == (
        "monitor.start-done.done-without-outstanding",
    )
    # The generic ready/valid monitor accepts before retiring within a slot.  In
    # start/done, a simultaneous done retires only an older request, so its registered
    # adapter goal is intentionally not aliased to that different predicate.
    assert "monitor.output-without-transaction" not in Monitor(contract).goals()

    busy_restarted = next(
        row for row in table.transitions
        if row.state == 1 and not row.reset
        and dict(row.presented) == {"in": True}
        and dict(row.observed) == {"done": False})
    assert busy_restarted.next_state == 1
    assert busy_restarted.accepted == ()
    assert not busy_restarted.environment_admissible
    assert busy_restarted.goals == ()
    assert "monitor.start-done.start-while-busy" not in table.goal_names

    class BooleanAlgebra:
        literal = staticmethod(lambda value: value)
        equal = staticmethod(lambda left, right: left == right)
        land = staticmethod(lambda *values: all(values))
        lor = staticmethod(lambda *values: any(values))
        ite = staticmethod(
            lambda condition, when_true, when_false:
            when_true if condition else when_false)

    encoded = table.symbolic(
        busy_restarted.state, False, {"in": True}, {}, {"done": False},
        BooleanAlgebra())
    assert encoded.valid is False, \
        "an environment-invalid row is outside the exact trace relation"


def test_exact_table_requires_the_registrys_versioned_payload_policy():
    """Representative payloads are not themselves a generality proof."""

    program = adapter_program_for(_contract("start-done"))

    class UndeclaredPayloadPolicy(type(program)):
        exact_control_product_policy = None

    with pytest.raises(
            api.Refused,
            match="adapter-exact-payload-independence-unbound"):
        AdapterExactTable(
            UndeclaredPayloadPolicy(), _contract("start-done"))


@pytest.mark.parametrize('kind', ['ready-valid', 'start-done', 'sampled', 'valid-only'])
@pytest.mark.parametrize('private', [False, True])
def test_exact_drive_is_causal_and_equivalent_to_the_complete_table(kind, private):
    import z3
    from z3.z3util import get_vars

    from behave.sim.rtl.task_symbolic import Z3Algebra
    from tests.sim.test_sampled_protocol import _contract as sampled_contract

    contract = (sampled_contract(input_valid=kind == 'valid-only', output_valid=True)
                if kind in ('sampled', 'valid-only') else _contract(kind, deadline=1))
    table = AdapterExactTable(adapter_program_for(contract), contract)
    context = z3.Context() if private else None
    a = Z3Algebra(context)
    state, reset = z3.Int('adapter_state', ctx=context), z3.Bool('adapter_reset', ctx=context)
    groups = [{name: z3.Bool(prefix + name, ctx=context) for name in names}
              for prefix, names in (('present_', table.presented_names),
                  ('schedule_', table.scheduled_names), ('sample_', table.observed_names))]
    presented, scheduled, observed = groups
    encoded = table.symbolic(state, reset, presented, scheduled, observed, a)
    guards = []
    for row in table.transitions:
        guards.append(z3.And(state == row.state, reset == row.reset,
            *(terms[name] == value for terms, cells in zip(groups,
                (row.presented, row.scheduled, row.observed), strict=True) for name, value in cells)))

    def legacy_select(name):
        value = z3.BoolVal(dict(table.transitions[-1].drive)[name], ctx=context)
        for row, guard in reversed(tuple(zip(table.transitions[:-1], guards[:-1], strict=True))):
            value = z3.If(guard, dict(row.drive)[name], value)
        return value

    differences = [encoded.valid != z3.Or(*(guard for guard, row in
        zip(guards, table.transitions, strict=True) if row.environment_admissible))]
    differences.extend(value != legacy_select(name) for name, value in encoded.drive.items())
    solver = z3.Solver(ctx=context)
    solver.add(z3.Or(*differences))
    # Deliberately no domain assumptions: preserve even the old invalid-state
    # default values, while valid=False still excludes those states.
    assert solver.check() == z3.unsat
    samples = {term.get_id() for term in observed.values()}
    assert all(samples.isdisjoint(v.get_id() for v in get_vars(term))
               for term in (encoded.valid, *encoded.drive.values()))


def test_adapter_event_cannot_cross_its_declared_owner_boundary():
    contract = _contract("start-done")
    program = adapter_program_for(contract)
    forged = AdapterTransition(
        state=program.initial_state(contract),
        violations=(AdapterViolation(
            program.START_WHILE_BUSY, "in",
            "an artifact transition tried to report an environment assumption"),),
    )

    with pytest.raises(api.Refused, match="adapter-protocol-event-owner-mismatch"):
        program.artifact_events(contract, forged)


def test_start_done_deadline_uses_its_registered_slot_entry_transition():
    contract = _contract("start-done", deadline=1)
    program = adapter_program_for(contract)
    table = AdapterExactTable(program, contract)
    states = program.state_space(contract)

    assert len(states) == 4  # idle plus busy ages 0, 1 and the saturated age 2
    start_slot = api.EventSlot(offers=("in",), controls={"rst": 0})
    drive = program.drive(
        contract, states[0], start_slot, (3,), dict(start_slot.controls))
    accepted = program.step(
        contract, states[0], start_slot, drive, {"done": 0, "out_y": 0})
    assert accepted.state == {"adapter.age": 0, "adapter.busy": True}

    wait_slot = api.EventSlot(controls={"rst": 0})
    drive = program.drive(
        contract, accepted.state, wait_slot, None, dict(wait_slot.controls))
    boundary = program.step(
        contract, accepted.state, wait_slot, drive, {"done": 0, "out_y": 0})
    assert boundary.state == {"adapter.age": 1, "adapter.busy": True}
    assert not boundary.violations

    # Deadline checking precedes done at this slot entry.  A completion two slots after
    # acceptance is late for deadline 1 even though it retires the request here.
    drive = program.drive(
        contract, boundary.state, wait_slot, None, dict(wait_slot.controls))
    late = program.step(
        contract, boundary.state, wait_slot, drive, {"done": 1, "out_y": 6})
    assert [item.goal for item in late.violations] == [
        "monitor.response-deadline"]
    assert late.state == {"adapter.age": 0, "adapter.busy": False}

    exact_row = next(
        row for row in table.transitions
        if row.state == table.state_index(boundary.state)
        and not row.reset and not dict(row.presented)["in"]
        and dict(row.observed)["done"])
    assert exact_row.next_state == table.state_index(late.state)
    assert exact_row.goals == ("monitor.response-deadline",)


@pytest.mark.parametrize(
    "policy,expected",
    [
        (api.ResetPolicy.CLEARS,
         {"adapter.age": 0, "adapter.busy": False}),
        (api.ResetPolicy.KEEPS,
         {"adapter.age": 1, "adapter.busy": True}),
    ],
)
def test_start_done_reset_applies_one_rule_to_busy_and_deadline_age(
        policy, expected):
    contract = _contract("start-done", deadline=1, reset_policy=policy)
    program = adapter_program_for(contract)
    state = {"adapter.age": 1, "adapter.busy": True}
    slot = api.EventSlot(reset=True, controls={"rst": 1})
    drive = program.drive(contract, state, slot, None, dict(slot.controls))
    stepped = program.step(
        contract, state, slot, drive, {"done": "x", "out_y": "x"})
    assert stepped.state == expected
    assert stepped.state in program.state_space(contract)


def test_adapter_evidence_constructors_do_not_coerce_impostors():
    class Printable:
        def __str__(self):
            return "monitor.looks-valid"

        def __bool__(self):
            return True

    fake = Printable()
    with pytest.raises((TypeError, ValueError)):
        AdapterViolation(fake, "in", "not an identifier")
    with pytest.raises(TypeError):
        AdapterChannel(offered=fake, ready=None, transferred=False)
    with pytest.raises(TypeError):
        AdapterDrive({"start": True}, None)
    with pytest.raises(TypeError):
        AdapterTransition(state={}, channels={}, checks=(fake,))
    with pytest.raises(TypeError):
        registered_adapter("start-done", True)


def test_ready_valid_and_start_done_reach_the_same_logical_result():
    """Two pin schedules, one accepted transaction and one reference answer."""
    reference = _reference()
    rv_contract = _contract("ready-valid")
    sd_contract = _contract("start-done")
    rv_task = api.admit(
        rv_contract, _realization("ready-valid", "rv-device"), reference=reference)
    sd_task = api.admit(
        sd_contract, _realization("start-done", "sd-device"), reference=reference)
    rv_stimulus = _rv_stimulus()
    sd_stimulus = _sd_stimulus(
        api.EventSlot(offers=["in"], controls={"rst": 0}),
        api.EventSlot(controls={"rst": 0}))

    rv = _evaluate(rv_task, rv_stimulus, _ReadyValidDoubling(), reference)
    sd = _evaluate(sd_task, sd_stimulus, _StartDoneDoubling(), reference)

    assert rv.passed and sd.passed
    assert rv.accepted == sd.accepted == 1
    assert api.transfers(rv.records[0], "out") == [(6,)]
    assert api.transfers(sd.records[0], "out") == [(6,)]
    assert "out_ready" in dict(rv_stimulus.slots[1].controls)
    assert "out_ready" not in dict(sd_stimulus.slots[1].controls)
    assert {"in_ready", "out_valid"} <= set(dict(rv.records[0].records[1].outputs))
    assert {"done"} <= set(dict(sd.records[0].records[1].outputs))
    rv_goals = set(Monitor(rv_contract).goals())
    sd_goals = set(Monitor(sd_contract).goals())
    assert "monitor.start-done.start-while-busy" not in rv_goals
    assert "monitor.start-done.start-while-busy" not in sd_goals
    assert "monitor.start-done.done-without-outstanding" in sd_goals
    goals = Monitor(sd_contract).goals()
    assert dict(goals[
        "monitor.start-done.done-without-outstanding"].provenance) == {
            "owed_by": "artifact", "role": "out"}
    assert "monitor.sink-stability" in rv_goals
    assert "monitor.sink-stability" not in sd_goals
    assert "monitor.sink-stability" not in Monitor(sd_contract).unchecked()


@pytest.mark.cosim
@needs_verilator
@pytest.mark.parametrize("explicit_instance", [False, True])
def test_start_done_runs_through_the_rtl_driver(tmp_path, explicit_instance):
    """The generated cocotb loop uses the registered adapter, not an RV copy."""
    from behave.sim.rtl.artifact import RTLArtifact

    source = """\
module start_done_double (
    input wire clk, input wire rst,
    input wire start, input wire [7:0] in_a,
    output reg done, output reg [7:0] out_y
);
    reg busy;
    reg [7:0] saved;
    always @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            saved <= 8'd0;
            done <= 1'b0;
            out_y <= 8'd0;
        end else begin
            done <= 1'b0;
            if (busy) begin
                done <= 1'b1;
                out_y <= saved << 1;
                busy <= 1'b0;
            end
            if (start && !busy) begin
                saved <= in_a;
                busy <= 1'b1;
            end
        end
    end
endmodule
"""
    rtl = tmp_path / "start_done_double.sv"
    rtl.write_text(source)
    artifact = RTLArtifact(rtl, "start_done_double", tmp_path / "sim")
    contract = _contract("start-done")
    if explicit_instance:
        contract = api.TaskContract(
            contract.endpoints, contract.clocks,
            protocol_instances=[api.ProtocolInstance("compute", ["in", "out"])])
    reference = _reference()
    task = api.admit(
        contract, _realization("start-done", artifact.identity),
        reference=reference)
    stimulus = api.reset_then(contract, [(3,)], drain=3)

    trace = run_device(task, stimulus, artifact)

    assert api.transfers(trace, "in") == [(3,)]
    assert api.transfers(trace, "out") == [(6,)]
    assert api.compare_run(task, reference, trace).ok
    assert not Monitor(contract).check(stimulus, trace).violations


def test_start_done_queues_a_second_request_while_the_adapter_is_busy():
    contract = _contract("start-done")
    reference = _reference()
    task = api.admit(
        contract, _realization("start-done", "sd-device"), reference=reference)
    stimulus = _sd_stimulus(
        api.EventSlot(offers=["in"], controls={"rst": 0}),
        api.EventSlot(offers=["in"], controls={"rst": 0}),
        *[api.EventSlot(controls={"rst": 0}) for _ in range(6)])
    trace = run_device(task, stimulus, _StartDoneDoubling(latency=2))
    report = Monitor(contract).check(stimulus, trace)

    assert api.transfers(trace, "in") == [(3,), (5,)]
    assert api.transfers(trace, "out") == [(6,), (10,)]
    assert not report.violations
    assert not report.assumption_violations
    assert "monitor.start-done.start-while-busy" not in \
        Monitor(contract).goals()
    audited = audit_environment_assumptions(contract, stimulus, trace)
    assert audited is None

    evaluation = _evaluate(
        task, stimulus, _StartDoneDoubling(latency=2), reference)
    assert evaluation.results[0].was_compared
    assert evaluation.passed
    telemetry = dict(evaluation.telemetry)
    assert telemetry["environment_assumption_violations"] == ()
    assert "monitor.start-done.start-while-busy" not in evaluation.hits


def test_done_without_an_outstanding_request_is_a_protocol_monitor_violation():
    contract = _contract("start-done")
    reference = _reference()
    task = api.admit(
        contract, _realization("start-done", "sd-device"), reference=reference)
    stimulus = _sd_stimulus(api.EventSlot(controls={"rst": 0}))
    trace = run_device(task, stimulus, _StartDoneDoubling(spurious=True))
    report = Monitor(contract).check(stimulus, trace)

    assert api.transfers(trace, "out") == [(0,)]
    assert any(v.goal == "monitor.start-done.done-without-outstanding"
               for v in report.violations)
    assert not report.assumption_violations
    assert audit_environment_assumptions(contract, stimulus, trace) is None
    assert "monitor.start-done.done-without-outstanding" in \
        Monitor(contract).goals()


@pytest.mark.parametrize(
    "policy,want_busy",
    [(api.ResetPolicy.CLEARS, 0), (api.ResetPolicy.KEEPS, 1)],
)
def test_start_done_reset_follows_the_declared_outstanding_policy(
        policy, want_busy):
    contract = _contract("start-done", reset_policy=policy)
    reference = _reference()
    task = api.admit(
        contract, _realization("start-done", "sd-device"), reference=reference)
    stimulus = _sd_stimulus(
        api.EventSlot(offers=["in"], controls={"rst": 0}),
        api.EventSlot(reset=True, controls={"rst": 1}))
    trace = run_device(task, stimulus, _StartDoneDoubling(latency=4))

    monitor = dict(trace.records[-1].monitor)
    assert monitor["adapter.reset"] == 1
    assert monitor["adapter.busy"] == want_busy
    assert monitor["outstanding"] == want_busy


def test_start_done_version_is_identity_and_unknown_versions_refuse_at_admission():
    one = _contract("start-done", version=1)
    two = _contract("start-done", version=2)
    assert one.digest != two.digest
    with pytest.raises(api.Refused, match="admission-adapter-unsupported"):
        api.admit(two, _realization("start-done", "sd-device"),
                  reference=_reference())


def test_start_done_refuses_two_reset_rules_for_its_one_busy_state():
    contract = _contract("start-done")
    source, sink = contract.endpoints
    conflicting = api.TaskContract(
        endpoints=[source, api.Endpoint(
            sink.name, sink.direction, sink.fields, sink.adapter,
            clock=sink.clock, kind=sink.kind,
            answers_per_transaction=sink.answers_per_transaction,
            reset_policy=api.ResetPolicy.KEEPS)],
        clocks=contract.clocks,
        source="conflicting start/done reset rules")
    with pytest.raises(api.Refused, match="adapter-start-done-reset-policy"):
        api.admit(conflicting, _realization("start-done", "sd-device"),
                  reference=_reference())


def test_start_done_refuses_an_undeclared_busy_reset_rule():
    contract = _contract("start-done", reset_policy=None)
    with pytest.raises(
            api.Refused, match="adapter-start-done-reset-policy-undeclared"):
        api.admit(contract, _realization("start-done", "sd-device"),
                  reference=_reference())


def test_adapter_metadata_cannot_replace_the_registered_monitor_state():
    contract = _contract("start-done")
    source, sink = contract.endpoints
    forged = api.Adapter(
        "start-done", 1, environment_controls=("start",),
        monitor_state=("not-busy",))
    conflicting = api.TaskContract(
        endpoints=[api.Endpoint(
            source.name, source.direction, source.fields, forged,
            clock=source.clock, kind=source.kind,
            reset_policy=source.reset_policy), sink],
        clocks=contract.clocks,
        source="forged adapter monitor schema")
    with pytest.raises(
            api.Refused, match="admission-adapter-monitor-state-mismatch"):
        api.admit(conflicting, _realization("start-done", "sd-device"),
                  reference=_reference())
