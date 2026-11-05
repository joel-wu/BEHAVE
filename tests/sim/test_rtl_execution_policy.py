"""Independent phase expectations, not source-vs-SMT self-agreement."""

import pytest

from behave.sim import api
from behave.sim.core.driver import check_channel_shape
from behave.sim.core.hit_vector import HIT_VECTOR_ENCODING
from behave.sim.core.stimulus import AuditedTrace, SlotRecord, trace_domain
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.execution_policy import (
    execution_adapter_version,
    execution_policy,
)


def _contract():
    return api.TaskContract(
        endpoints=[api.Endpoint(
            "out", api.Direction.OUT, [api.Field("y", api.FiniteType(8), "y")],
            api.ready_valid("out", "out_valid", "out_ready"),
            clock="clk", kind=api.Kind.TRANSACTION)],
        clocks=[api.ClockDomain("clk", "clk", "rst")])


def test_policy_is_closed_and_its_image_cannot_mutate_the_profile():
    policy = execution_policy()
    assert policy["version"] == "rtl-scheduled-execution/11"
    assert policy["runtime_stop"] == "first-determined-protocol-violation/1"
    assert policy["memory_successors"] == "complete-snapshot-or-memory-successor-writes/1"
    assert policy["assertions"] == "enabled-original-actions"
    assert policy["structural_sample"] == "none-interface-only"
    assert policy["phases"] == [
        "drive-controls-and-payloads", "settle-control-events",
        "sample-transfer-boundary", "commit-active-clock-batch",
        "settle-clock-events", "return-clock-to-inactive", "settle-clock-return-events", "record-successor"]
    assert policy["horizon_unit"] == "scheduled-step"
    assert policy["initial_state"] == "before-first-drive"
    assert policy["clock_timebase"] == "declared-domain-active-edges"
    assert policy["clock_return"] == "inactive-edge-settles-before-successor"
    original = execution_adapter_version("protocol/1")
    policy["phases"].reverse()
    assert execution_adapter_version("protocol/1") == original
    assert execution_adapter_version("protocol/2") != original
    assert trace_domain("contract", adapter_version=original) != trace_domain(
        "contract", adapter_version="protocol/1")


@pytest.mark.parametrize("change", [None, "missing", "order", "extra", "old-version"])
def test_trace_decoder_requires_the_actual_execution_policy(change):
    contract = _contract()
    stimulus = api.Stimulus({}, [api.EventSlot(controls={"rst": 0, "out_ready": 1})])
    document = {
        "execution_policy": execution_policy(),
        "records": [SlotRecord(outputs={"out_valid": 0, "y": 0}).as_dict()],
        "structural_refusals": {}, "hit_encoding": HIT_VECTOR_ENCODING,
        "hit_catalogue": [],
    }
    # Production uses the catalogue-bound compact wire representation.
    from behave.sim.core.hit_vector import HitVector
    document["records"][0]["hits"] = HitVector.from_names(()).as_compact_dict()
    if change == "missing":
        del document["execution_policy"]
    elif change == "order":
        document["execution_policy"]["phases"].reverse()
    elif change == "extra":
        document["execution_policy"]["simultaneous_reset_clock"] = True
    elif change == "old-version":
        document["execution_policy"]["version"] = "rtl-scheduled-execution/4"
    artifact = RTLArtifact("unused.sv", "M", "/tmp/unused-phase-test")
    result = artifact._trace_of(contract, stimulus, document, check_channel_shape, "rtl")
    if change is None:
        assert type(result) is AuditedTrace
        assert result.domain == trace_domain(
            contract.digest, horizon=1, adapter_version=execution_adapter_version(""))
    else:
        assert type(result) is api.Refusal
        assert result.kind == "rtl-artifact-execution-policy-mismatch"


def test_exact_trace_consumes_register_iterator_once(monkeypatch, tmp_path):
    artifact = RTLArtifact("unused.sv", "M", tmp_path)
    asked = []
    expected = object()

    def record(_contract, _stimuli, **kwargs):
        asked.append(kwargs)
        return [expected]

    monkeypatch.setattr(artifact, "traces_per_process", record)
    assert artifact.exact_trace(
        _contract(), api.Stimulus({}, []), observe_names=iter(("y", "q")),
        state_names=iter(("q", "seen")), state_widths={"q": 8, "seen": 8},
    ) is expected
    assert asked[0]["observe_names"] == ("y", "q", "seen")
    assert asked[0]["state_names"] == ("q", "seen")
    from behave.sim.rtl.replay_failure import ReplayPurpose

    assert asked[0]["_purpose"] is ReplayPurpose.ANALYSIS


@pytest.mark.parametrize("origin", ["candidate", "verifier", "infrastructure"])
def test_exact_trace_preserves_attributed_failure(monkeypatch, tmp_path, origin):
    from behave.sim.core.failure import RunFailure
    from behave.sim.core.refusal import Refusal, Refused

    failure = getattr(RunFailure, origin)(
        "native-run", Refusal("failed", "source stopped", event="assertion"))
    artifact = RTLArtifact("unused.sv", "M", tmp_path)
    monkeypatch.setattr(artifact, "traces_per_process", lambda *_args, **_kwargs: [failure])
    with pytest.raises(Refused) as raised:
        artifact.exact_trace(_contract(), api.Stimulus({}, []), observe_names=(), state_names=())
    image = raised.value.refusal.as_dict()
    assert image['kind'] == failure.reason.kind
    assert image['detail']['failure'] == failure.as_dict()
    assert image['detail']['event'] == 'assertion'


@pytest.mark.cosim
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("active_low", [False, True])
def test_control_settle_and_clock_commit_are_distinct_boundaries(
        tmp_path, asynchronous, active_low):
    condition = "!rst" if active_low else "rst"
    reset_edge = "negedge" if active_low else "posedge"
    sensitivity = f" or {reset_edge} rst" if asynchronous else ""
    design = tmp_path / "mixed.sv"
    design.write_text(f"""module M(input clk, input rst, input out_ready,
        output out_valid, output [7:0] y);
        reg [7:0] q = 5;
        reg [7:0] seen = 1;
        assign y = q;
        assign out_valid = 0;
        always @(posedge clk{sensitivity})
            if ({condition}) q <= 0; else q <= q + 1;
        always @(posedge clk) seen <= q;
    endmodule""")
    base = _contract()
    contract = api.TaskContract(endpoints=base.endpoints, clocks=[
        api.ClockDomain("clk", "clk", "rst", reset_active_low=active_low)])
    asserted = int(not active_low)
    stimulus = api.Stimulus({}, [api.EventSlot(
        reset=reset, controls={"rst": asserted if reset else 1 - asserted,
                               "out_ready": 1}) for reset in (True, True, False)])
    artifact = RTLArtifact(design, "M", tmp_path / "run")
    trace = artifact.traces_per_process(
        contract, [stimulus], workers=1, observe_names=("q", "seen"),
        state_names=("q", "seen"), state_widths={"q": 8, "seen": 8})[0]
    assert type(trace) is AuditedTrace, trace.as_dict()
    assert dict(trace.initial_state) == {"q": 5, "seen": 1}
    # Literal expectations follow the two ordered events, independently of SMT:
    # async assertion has already changed q when the clock reads it; sync has not.
    before = [(0, 1), (0, 0), (0, 0)] if asynchronous else [
        (5, 1), (0, 5), (0, 0)]
    after = [(0, 0), (0, 0), (1, 0)] if asynchronous else [
        (0, 5), (0, 0), (1, 0)]
    assert [(dict(r.outputs)["q"], dict(r.outputs)["seen"]) for r in trace.records] == before
    assert [(dict(r.state)["q"], dict(r.state)["seen"]) for r in trace.records] == after
    assert len(trace.records) == 3  # N counts scheduled steps, not sub-events.


@pytest.mark.cosim
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("release_on_clock", [False, True])
def test_reset_pulse_can_change_state_without_a_clock_in_that_domain(
        tmp_path, asynchronous, release_on_clock):
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule

    sensitivity = " or posedge a_rst" if asynchronous else ""
    design = tmp_path / "pulse.sv"
    design.write_text(f"""module M(input a_clk, a_rst, b_clk, b_rst,
        input a_ready, b_ready, output a_valid, b_valid,
        output [7:0] a_y, b_y);
        reg [7:0] q = 5;
        reg [7:0] seen = 1;
        reg [7:0] b_count = 0;
        assign a_y = q; assign b_y = b_count;
        assign a_valid = 0; assign b_valid = 0;
        always @(posedge a_clk{sensitivity})
            if (a_rst) q <= 0; else q <= q + 1;
        always @(posedge a_clk) seen <= q;
        always @(posedge b_clk) b_count <= b_count + 1;
    endmodule""")
    contract = api.TaskContract(endpoints=[api.Endpoint(
        name, api.Direction.OUT, [api.Field("y", api.FiniteType(8), f"{name}_y")],
        api.ready_valid(api.Direction.OUT, f"{name}_valid", f"{name}_ready"),
        clock=name, kind=api.Kind.TRANSACTION) for name in ("a", "b")],
        clocks=[api.ClockDomain(name, f"{name}_clk", f"{name}_rst")
                for name in ("a", "b")])
    def slot(time, ticks=(), resets=()):
        return GlobalEventSlot(time, event=api.EventSlot(
            ticks=ticks, resets=resets, controls={
                "a_ready": 1, "b_ready": 1,
                "a_rst": int("a" in resets), "b_rst": int("b" in resets)}))

    b_first = 2 if release_on_clock else 4
    slots = [slot(1, resets=("a",)), slot(2, ticks=("b",))] if release_on_clock else [
        slot(1, resets=("a",)), slot(2), slot(4, ticks=("b",))]
    slots.append(slot(10, ticks=("a",)))
    schedule = GlobalSchedule([
        ClockTiming("a", 10, phase=10, clock="a_clk", reset="a_rst"),
        ClockTiming("b", 20, phase=b_first, clock="b_clk", reset="b_rst")], slots)
    stimulus = api.Stimulus({}, [slot.event for slot in schedule.slots],
                            clock_schedule=schedule)
    assert stimulus.faults_against(contract) == []
    trace = RTLArtifact(design, "M", tmp_path / "run").traces_per_process(
        contract, [stimulus], workers=1, observe_names=("q", "seen"),
        state_names=("q", "seen", "b_count"),
        state_widths={"q": 8, "seen": 8, "b_count": 8})[0]
    assert type(trace) is AuditedTrace, trace.as_dict()
    assert dict(trace.initial_state) == {"q": 5, "seen": 1, "b_count": 0}
    q_before_clock = 0 if asynchronous else 5
    pulse_state = {"q": q_before_clock, "seen": 1, "b_count": 0}
    expected = [pulse_state] * (1 if release_on_clock else 2) + [
        {"q": q_before_clock, "seen": 1, "b_count": 1},
        {"q": q_before_clock + 1, "seen": q_before_clock, "b_count": 1},
    ]
    assert [dict(r.state) for r in trace.records] == expected


@pytest.mark.cosim
@pytest.mark.parametrize("active_low", [False, True])
@pytest.mark.parametrize("faulty", [False, True])
def test_source_reset_goal_requires_an_uninterrupted_hold(tmp_path, active_low, faulty):
    """Production replay, not a formula-generated record, supplies the state."""
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
    from behave.sim.core.monitor import MonitorProduct

    condition = "!a_rst" if active_low else "a_rst"
    update = "q <= q + 1;" if faulty else f"if ({condition}) q <= 0; else q <= q + 1;"
    design = tmp_path / "hold.sv"
    design.write_text(f"""module M(input a_clk, a_rst, b_clk, b_rst,
        input a_ready, b_ready, output a_valid, b_valid,
        output [7:0] a_y, b_y);
        reg [7:0] q = 0;
        reg [7:0] b_count = 0;
        assign a_y = q; assign b_y = b_count;
        assign a_valid = 0; assign b_valid = 0;
        always @(posedge a_clk) {update}
        always @(posedge b_clk) b_count <= b_count + 1;
    endmodule""")
    contract = api.TaskContract(endpoints=[api.Endpoint(
        name, api.Direction.OUT, [api.Field("y", api.FiniteType(8), f"{name}_y")],
        api.ready_valid(api.Direction.OUT, f"{name}_valid", f"{name}_ready"),
        clock=name, kind=api.Kind.TRANSACTION) for name in ("a", "b")], clocks=[
        api.ClockDomain("a", "a_clk", "a_rst", reset_active_low=active_low,
                        reset_hold_cycles=2, reset_restores=True),
        api.ClockDomain("b", "b_clk", "b_rst")])
    slots = []
    for time in range(21):
        asserted = time <= 4 or time == 12 or time >= 15
        slots.append(GlobalEventSlot(time, event=api.EventSlot(
            ticks=tuple(name for name, period in (("a", 4), ("b", 5))
                        if time % period == 0),
            resets=("a",) if asserted else (), controls={
                "a_rst": int(asserted != active_low), "b_rst": 0,
                "a_ready": 1, "b_ready": 1})))
    schedule = GlobalSchedule([
        ClockTiming("a", 4, clock="a_clk", reset="a_rst", reset_active_low=active_low),
        ClockTiming("b", 5, clock="b_clk", reset="b_rst")], slots)
    stimulus = api.Stimulus({}, [slot.event for slot in slots], clock_schedule=schedule)
    trace = RTLArtifact(design, "M", tmp_path / "run").traces_per_process(
        contract, [stimulus], workers=1, state_names=("q", "b_count"),
        state_widths={"q": 8, "b_count": 8})[0]
    assert type(trace) is AuditedTrace, trace.as_dict()
    assert [dict(trace.records[i].state)["q"] for i in (4, 8, 12, 16, 20)] == (
        [2, 3, 4, 5, 6] if faulty else [0, 1, 0, 0, 0])
    report = MonitorProduct(contract).check(
        stimulus, trace, reset_state_fields={"a": ("q",)})
    scenario = "monitor.reset-restoration.a.scenario"
    violation = "monitor.reset-restoration.a.violation"
    assert [(hit.goal, hit.slot) for hit in report.hits] == (
        [(scenario, 20), (violation, 20)] if faulty else [(scenario, 20)])
    assert not report.unresolved
