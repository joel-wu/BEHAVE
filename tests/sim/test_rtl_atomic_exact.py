"""The exact RTL product over one authenticated global event schedule."""

from __future__ import annotations

import itertools

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.clock_schedule import (
    ClockTiming,
    GlobalEventSlot,
    GlobalSchedule,
)
from behave.sim.core.contract import ValueType
from behave.sim.core.driver import check_channel_shape
from behave.sim.core.hit_vector import HIT_VECTOR_ENCODING, HitVector
from behave.sim.core.monitor import MonitorProduct, monitor_for
from behave.sim.core.physical_value import packed_width
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.core.stimulus import (
    AuditedTrace,
    EventSlot,
    SlotRecord,
    Stimulus,
)
from behave.sim.rtl.artifact import (
    ATOMIC_DRIVER_TRACE_VERSION,
    RTLArtifact,
)
from behave.sim.rtl.exact_job_context import RTLExactJobContext
from behave.sim.rtl.execution_policy import execution_policy
from behave.sim.rtl.state_ownership import RTLStateField, RTLStateOwnership
from behave.sim.stimulus_set import GenerationSpec

U8 = api.FiniteType(8)
STRUCTURAL = "rtl.tests.atomic-never"
_NAMES = itertools.count()


def _wire_records(records, catalogue=()):
    catalogue = tuple(sorted(catalogue))
    out = []
    for record in records:
        reached = {goal for goal, value in record.hits if value is True}
        unreadable = {goal for goal, value in record.hits if value is None}
        image = record.as_dict()
        image["hits"] = HitVector.from_names(
            catalogue, reached, unreadable).as_compact_dict()
        out.append(image)
    return out


def _contract(field_type=U8):
    return api.TaskContract(
        endpoints=[
            api.Endpoint(
                "a_out", api.Direction.OUT,
                [api.Field("data", field_type, "a_out_data")],
                api.ready_valid(
                    api.Direction.OUT, "a_out_valid", "a_out_ready"),
                clock="a", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "b_out", api.Direction.OUT,
                [api.Field("data", field_type, "b_out_data")],
                api.ready_valid(
                    api.Direction.OUT, "b_out_valid", "b_out_ready"),
                clock="b", kind=api.Kind.TRANSACTION),
        ],
        clocks=[
            api.ClockDomain("a", "a_clk", "a_rst"),
            api.ClockDomain("b", "b_clk", "b_rst"),
        ],
        source="atomic exact test task")


def _scheduled(timings, count, *, reset_a=False, ready_a=1, ready_b=1):
    """Add the task-owned controls to an otherwise periodic canonical schedule."""

    periodic = GlobalSchedule.periodic(timings, count)
    slots = []
    for index, slot in enumerate(periodic.slots):
        resets = ("a",) if reset_a and index == 0 else ()
        controls = {
            "a_out_ready": ready_a,
            "a_rst": int("a" in resets),
            "b_out_ready": ready_b,
            "b_rst": int("b" in resets),
        }
        slots.append(GlobalEventSlot(
            slot.time,
            event=EventSlot(
                controls=controls, ticks=slot.events, resets=resets)))
    return GlobalSchedule(timings, slots)


def _start_done_contract(*, deadline=None, field_type=None):
    from .test_protocol_adapters import _contract as adapter_contract

    if field_type is None:
        return adapter_contract("start-done", deadline=deadline)
    return api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("a", field_type, "in_a")],
                api.start_done("in", "start", "done"),
                clock="clk", kind=api.Kind.TRANSACTION,
                reset_policy=api.ResetPolicy.CLEARS,
                response_deadline=deadline),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("y", field_type, "out_y")],
                api.start_done("out", "start", "done"),
                clock="clk", kind=api.Kind.TRANSACTION,
                answers_per_transaction=1,
                reset_policy=api.ResetPolicy.CLEARS),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="recursive start-done test contract",
    )


def _reset_claim_contract():
    base = _contract()
    return api.TaskContract(
        endpoints=base.endpoints,
        clocks=(
            api.ClockDomain(
                "a", "a_clk", "a_rst", reset_hold_cycles=1,
                reset_restores=True),
            api.ClockDomain(
                "b", "b_clk", "b_rst", reset_hold_cycles=1,
                reset_restores=True),
        ),
        source=base.source,
    )


def _reset_state_ownership():
    return RTLStateOwnership("top", (
        RTLStateField(
            "a_out_data", 8, "a", "a_clk", "rising",
            "a_rst", False, True),
        RTLStateField(
            "b_out_data", 8, "b", "b_clk", "rising",
            "b_rst", False, False),
    ))


def _same_domain_output_product_contract(*, reverse=False):
    """The two independent output streams are both sampled on domain ``a``."""

    endpoints = [
            api.Endpoint(
                "a_out", api.Direction.OUT,
                [api.Field("data", U8, "a_out_data")],
                api.ready_valid(
                    api.Direction.OUT, "a_out_valid", "a_out_ready"),
                clock="a", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "b_out", api.Direction.OUT,
                [api.Field("data", U8, "b_out_data")],
                api.ready_valid(
                    api.Direction.OUT, "b_out_valid", "b_out_ready"),
                clock="a", kind=api.Kind.TRANSACTION),
        ]
    return api.TaskContract(
        endpoints=list(reversed(endpoints)) if reverse else endpoints,
        clocks=[
            api.ClockDomain("a", "a_clk", "a_rst"),
            api.ClockDomain("b", "b_clk", "b_rst"),
        ],
        source="two endpoint-local monitors in one atomic clock domain",
    )


def _stimulus(schedule):
    return Stimulus(
        queues={}, slots=[slot.event for slot in schedule.slots],
        source="atomic exact test", clock_schedule=schedule)


def _model(instance, stimulus):
    formula = instance._symbolic(len(stimulus.slots))
    initial_solver = formula.solver()
    assert initial_solver.check() == z3.sat
    initial = formula.semantic_image(initial_solver.model())["rtl_states"][0]
    rows = tuple(instance.codec.encode(stimulus, initial_state=initial))
    solver = formula.solver()
    solver.add(*formula.request_constraints(rows))
    assert solver.check() == z3.sat
    return rows, formula.semantic_image(solver.model())


def _trace_from_image(instance, stimulus, image, hits, *, source_evidence=None):
    records = []
    for slot, vector in enumerate(hits):
        records.append(SlotRecord(
            accepted=image["accepted"][slot], state=image["rtl_states"][slot + 1],
            outputs=image["raw_outputs"][slot], hits=vector, monitor={}))
    return AuditedTrace(
        stimulus, initial_state=image["rtl_states"][0], records=records,
        artifact=instance.analysis_identity(), source_evidence=source_evidence)


def _empty_native_record(instance, stimulus):
    """Explicit empty record for decoder tests, not evidence of an RTL run."""
    from behave.sim.rtl.native_events import NativeEventPlan
    from behave.sim.rtl.native_evidence_codec import pack
    from behave.sim.rtl.native_observation import VERSION
    from behave.sim.rtl.source_bindings import BoundOccurrence, EventBindings

    manifest = instance.raw.catalogue_manifest()
    entries = tuple(BoundOccurrence(key, goal, index, ())
                    for index, (key, goal) in enumerate(manifest.occurrences))
    plan = NativeEventPlan(EventBindings(len(entries), entries), "f" * 64, 100)
    phases = ["startup", *[phase for _ in stimulus.slots for phase in ("control", "clock", "advance")]]
    return {"version": VERSION, "plan": plan.evidence_image,
            "execution": execution_policy(native_events=True), "stimulus": stimulus.fingerprint,
            "completed_steps": len(stimulus.slots),
            "intervals": [{"phase": phase, "callbacks": 0,
                           "events": pack(bytes((len(entries) + 7) // 8))}
                          for phase in phases]}


def _output_only_realization(width, *, signed=False):
    return api.Realization(
        "rtl",
        [
            api.Port("a_out_data", width, signed=signed,
                     driven_by="artifact", clock="a"),
            api.Port("a_out_valid", 1, driven_by="artifact"),
            api.Port("a_out_ready", 1, driven_by="environment"),
            api.Port("b_out_data", width, signed=signed,
                     driven_by="artifact", clock="b"),
            api.Port("b_out_valid", 1, driven_by="artifact"),
            api.Port("b_out_ready", 1, driven_by="environment"),
            api.Port("a_clk", 1, driven_by="environment"),
            api.Port("a_rst", 1, driven_by="environment"),
            api.Port("b_clk", 1, driven_by="environment"),
            api.Port("b_rst", 1, driven_by="environment"),
        ],
        channel_ports={"a_out": ("a_out_data",),
                       "b_out": ("b_out_data",)},
        identity="structured-rtl",
    )


@pytest.mark.parametrize(("value_type", "signed"), [
    (ValueType.boolean(), False),
    (ValueType.enumeration(("idle", "run")), False),
    (ValueType.record({"flag": ValueType.boolean()}), False),
    (ValueType.array(2, ValueType.boolean()), False),
    (ValueType.integer(api.FiniteType(4, signed=True)), True),
])
def test_signal_level_recursive_values_use_the_task_owned_packed_layout(
        value_type, signed):
    contract = _contract(value_type)
    width = packed_width(value_type)
    realization = _output_only_realization(width, signed=signed)

    admitted = api.admit(contract, realization)
    assert admitted.checks["physical_value_layout"] is True


@pytest.mark.parametrize(("width_delta", "signed", "kind"), [
    (-1, False, "admission-narrower-field"),
    (1, False, "admission-wider-field"),
    (0, True, "admission-signedness"),
])
def test_recursive_physical_port_shape_is_checked_at_admission(
        width_delta, signed, kind):
    value_type = ValueType.record({
        "flag": ValueType.boolean(),
        "mode": ValueType.enumeration(("idle", "run", "hold")),
    })
    contract = _contract(value_type)
    realization = _output_only_realization(
        packed_width(value_type) + width_delta, signed=signed)

    with pytest.raises(api.FailureRaised, match=kind):
        api.admit(contract, realization)


def test_empty_recursive_physical_type_is_a_typed_admission_refusal():
    value_type = ValueType.array(0, ValueType.boolean())
    contract = _contract(value_type)
    realization = api.Realization(
        "rtl", [
            # Port itself remains physically well formed; the task field is not.
            api.Port("a_out_data", 1, driven_by="artifact", clock="a"),
            api.Port("a_out_valid", 1, driven_by="artifact"),
            api.Port("a_out_ready", 1, driven_by="environment"),
            api.Port("b_out_data", 1, driven_by="artifact", clock="b"),
            api.Port("b_out_valid", 1, driven_by="artifact"),
            api.Port("b_out_ready", 1, driven_by="environment"),
            api.Port("a_clk", 1, driven_by="environment"),
            api.Port("a_rst", 1, driven_by="environment"),
            api.Port("b_clk", 1, driven_by="environment"),
            api.Port("b_rst", 1, driven_by="environment"),
        ], channel_ports={"a_out": ("a_out_data",),
                          "b_out": ("b_out_data",)})

    with pytest.raises(Refused, match="admission-empty-physical-value"):
        api.admit(contract, realization)


def test_exact_context_refuses_source_change_after_freezing_the_artifact(tmp_path):
    source = tmp_path / "atomic.sv"
    source.write_text("module top; endmodule\n")
    artifact = RTLArtifact(source, "top", tmp_path / "work")
    schedule = _scheduled((
        ClockTiming("a", 2, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 3, clock="b_clk", reset="b_rst"),
    ), 1)
    empty = CatalogueManifest()
    context = RTLExactJobContext(
        artifact, _contract(), GenerationSpec(1, 1, 0, 0), empty, empty,
        workdir=tmp_path / "work", clock_schedule=schedule)
    frozen = context.artifact_identity

    source.write_text("module top; wire changed = 1'b1; endmodule\n")
    with pytest.raises(Refused) as caught:
        context.prepare_source()

    assert caught.value.refusal.kind == "rtl-exact-source-changed-after-context-binding"
    assert context.artifact_identity == frozen


def test_inactive_other_domain_slots_do_not_advance_a_monitor_component():
    contract = _contract()
    schedule = _scheduled((
        ClockTiming("a", 3, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 2, phase=1, clock="b_clk", reset="b_rst"),
    ), 3, ready_a=0)
    assert [slot.events for slot in schedule.slots] == [
        ("a",), ("b",), ("a", "b")]
    stimulus = _stimulus(schedule)
    records = [
        SlotRecord(
            accepted={}, outputs={
                "a_out_data": 7, "a_out_valid": 1,
                "b_out_data": 0, "b_out_valid": 0,
            }, hits={}, monitor={}),
        # A's pins move while only B has an active edge.  This is not an A protocol
        # observation and cannot discharge or violate A's held-offer obligation.
        SlotRecord(
            accepted={}, outputs={
                "a_out_data": 99, "a_out_valid": 0,
                "b_out_data": 0, "b_out_valid": 0,
            }, hits={}, monitor={}),
        SlotRecord(
            accepted={}, outputs={
                "a_out_data": 8, "a_out_valid": 1,
                "b_out_data": 0, "b_out_valid": 0,
            }, hits={}, monitor={}),
    ]
    trace = AuditedTrace(
        stimulus, records=records, artifact="atomic-source-artifact")
    monitor = monitor_for(contract)
    assert type(monitor) is MonitorProduct

    report = monitor.check(stimulus, trace)

    stability = [item for item in report.violations
                 if item.goal == "monitor.sink-stability"]
    assert [(item.slot, item.endpoint) for item in stability] == [(2, "a_out")]
    a_domain, a_contract, _component = next(
        row for row in monitor._components if row[0] == "a")
    _local_stimulus, _local_trace, indexes = monitor._project(
        a_domain, a_contract, stimulus, trace)
    assert indexes == (0, 2)


@pytest.mark.parametrize("missing", ("artifact", "version", "hit_encoding"))
def test_atomic_trace_document_refuses_a_missing_binding_field(missing):
    schedule = _scheduled((
        ClockTiming("a", 2, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 3, clock="b_clk", reset="b_rst"),
    ), 1)
    stimulus = _stimulus(schedule)
    document = {
        "version": ATOMIC_DRIVER_TRACE_VERSION,
        "artifact": "frozen-atomic-artifact",
        "records": _wire_records((SlotRecord(
            accepted={}, outputs={}, hits={}, monitor={}),)),
        "structural_refusals": {},
        "hit_encoding": HIT_VECTOR_ENCODING,
        "execution_policy": execution_policy(),
        "hit_catalogue": [],
    }
    document.pop(missing)
    artifact = RTLArtifact("unused.sv", "top", "/tmp/unused-atomic-artifact")

    got = artifact._trace_of(
        _contract(), stimulus, document, check_channel_shape,
        "frozen-atomic-artifact")

    assert type(got) is Refusal
    assert got.kind == (
        "rtl-artifact-structural-record-malformed"
        if missing == "hit_encoding"
        else "rtl-atomic-artifact-binding-mismatch")


def test_atomic_trace_refuses_the_unversioned_legacy_record_list():
    schedule = _scheduled((
        ClockTiming("a", 2, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 3, clock="b_clk", reset="b_rst"),
    ), 1)
    stimulus = _stimulus(schedule)
    records = [SlotRecord(
        accepted={}, outputs={}, hits={}, monitor={}).as_dict()]
    artifact = RTLArtifact("unused.sv", "top", "/tmp/unused-atomic-artifact")

    got = artifact._trace_of(
        _contract(), stimulus, records, check_channel_shape,
        "frozen-atomic-artifact")

    assert type(got) is Refusal
    assert got.kind == "rtl-atomic-artifact-binding-mismatch"


def test_atomic_trace_document_refuses_a_tampered_artifact_identity():
    schedule = _scheduled((
        ClockTiming("a", 2, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 3, clock="b_clk", reset="b_rst"),
    ), 1)
    stimulus = _stimulus(schedule)
    document = {
        "version": ATOMIC_DRIVER_TRACE_VERSION,
        "artifact": "another-artifact",
        "records": _wire_records((SlotRecord(
            accepted={}, outputs={}, hits={}, monitor={}),)),
        "structural_refusals": {},
        "hit_encoding": HIT_VECTOR_ENCODING,
        "execution_policy": execution_policy(),
        "hit_catalogue": [],
    }
    artifact = RTLArtifact("unused.sv", "top", "/tmp/unused-atomic-artifact")

    got = artifact._trace_of(
        _contract(), stimulus, document, check_channel_shape,
        "frozen-atomic-artifact")

    assert type(got) is Refusal
    assert got.kind == "rtl-atomic-artifact-binding-mismatch"


def test_rendered_atomic_driver_validates_and_forwards_the_plan_binding():
    from behave.sim.harness import render_template

    source = render_template("driver_tb.py.tmpl", period_ns=10)
    compile(source, "driver_tb.py", "exec")
    assert "from behave.sim.rtl.replay_runtime import replay" in source
    assert "await replay(dut, Path(__file__).parent, period_ns=10)" in source


def test_exact_state_replay_build_exports_internal_verilator_handles(tmp_path):
    from behave.sim.harness import generate_makefile

    generate_makefile(
        tmp_path, "top", tb_module="driver_tb", expose_internal=True)

    assert "EXTRA_ARGS += --public-flat-rw" in (tmp_path / "Makefile").read_text()


def test_authoritative_makefile_can_omit_diagnostic_coverage(tmp_path):
    from behave.sim.harness import generate_makefile

    generate_makefile(
        tmp_path, "top", tb_module="driver_tb", coverage=False)

    makefile = (tmp_path / "Makefile").read_text()
    assert "--coverage" not in makefile
    assert "--public-flat-rw" not in makefile


@pytest.mark.cosim
def test_verilator_replay_exports_the_complete_two_clock_owned_state(tmp_path):
    """The real trusted build carries a hidden reset-owned register into the trace."""
    from behave.sim.rtl.state_ownership import read_state_ownership_with_execution

    rtl = tmp_path / "top.sv"
    rtl.write_text(r"""
module top(
    input logic a_clk, b_clk, a_rst, b_rst,
    input logic a_out_ready, b_out_ready,
    output logic a_out_valid, b_out_valid,
    output logic [7:0] a_out_data,
    output logic [7:0] b_out_data
);
    logic [7:0] hidden_b_q;
    initial begin a_out_data = 0; hidden_b_q = 0; end
    assign a_out_valid = 1'b1;
    assign b_out_valid = 1'b1;
    assign b_out_data = hidden_b_q + 8'd1;
    always @(posedge a_clk or posedge a_rst) begin
        if (a_rst) a_out_data <= 0;
        else a_out_data <= a_out_data + 1'b1;
    end
    always @(posedge b_clk or posedge b_rst) begin
        if (b_rst) hidden_b_q <= 0;
        else hidden_b_q <= hidden_b_q + 2'd2;
    end
endmodule
""")
    contract = _reset_claim_contract()
    ownership, _execution = read_state_ownership_with_execution(
        rtl, "top", contract)
    assert ownership.names == ("a_out_data", "hidden_b_q")
    assert ownership.observable_names == ("a_out_data",)

    timings = (
        ClockTiming("a", 2, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 3, clock="b_clk", reset="b_rst"),
    )

    def event(time, ticks, resets=()):
        resets = tuple(resets)
        return GlobalEventSlot(time, event=EventSlot(
            controls={
                "a_out_ready": 1, "a_rst": int("a" in resets),
                "b_out_ready": 1, "b_rst": int("b" in resets),
            },
            ticks=tuple(ticks), resets=resets))

    schedule = GlobalSchedule(timings, (
        event(0, ("a", "b"), ("a", "b")),
        event(2, ("a",)),
        event(3, ("b",)),
        event(4, ("a",)),
    ))
    stimulus = _stimulus(schedule)
    artifact = RTLArtifact(
        rtl, "top", tmp_path / "replay", timeout_s=180)

    trace = artifact.exact_trace(
        contract, stimulus,
        observe_names=(
            "a_out_data", "a_out_valid", "b_out_data", "b_out_valid"),
        state_names=ownership.names,
        state_widths=dict(ownership.widths))

    assert set(dict(trace.initial_state)) == set(ownership.names)
    assert all(type(value) is int for _name, value in trace.initial_state)
    assert all(
        set(dict(record.state)) == set(ownership.names)
        and all(type(value) is int for _name, value in record.state)
        for record in trace.records)

