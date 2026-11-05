"""A real simulator stop retains events, never a fabricated normal successor."""

import copy
import json
from dataclasses import replace

import pytest

from behave.sim import api
from behave.sim.core.record_value import thaw_record_value
from behave.sim.rtl.native_observation import VERSION, NativeObservation, read_native_evidence, read_native_prefix
from behave.sim.rtl.replay_interruption import interrupted_replay

from .test_rtl_native_events import _load, _plan
from .test_rtl_native_preparation import _catalogue


@pytest.mark.eda("g++")
@pytest.mark.parametrize("phase", ["startup", "control", "clock", "advance"])
@pytest.mark.parametrize("runtime", [False, True])
def test_prefix_keeps_exact_phase_and_no_unexecuted_suffix(tmp_path, phase, runtime):
    plan = _plan(count=3)
    stimulus = (api.Stimulus(replay_policy=api.ReplayPolicy(0, 0, 0)) if runtime else
                api.Stimulus(slots=[api.EventSlot()] * 3))
    library = _load(tmp_path, plan)
    observer = NativeObservation(plan, stimulus, library=library)
    emit = library.__behave_native_event_hit
    emit(2)
    if phase != "startup":
        observer.start_slots()
        emit(0)
        observer.sampled()
        emit(1)
        observer.clocked()
        observer.successor()
        observer.next_slot()
        emit(0)
        if phase in {"clock", "advance"}:
            observer.sampled()
            emit(2)
            if phase == "advance":
                observer.clocked()
                emit(1)
    image = observer.interrupt()
    assert not observer.active
    prefix = read_native_prefix(image, stimulus, _catalogue(plan), expected_plan=plan)
    assert prefix.phase == phase
    assert prefix.startup.goals == {"goal:2"}
    assert [h.goals for h in prefix.completed] == ([] if phase == "startup" else [{"goal:0", "goal:1"}])
    assert prefix.pending.goals == {
        "startup": {"goal:2"}, "control": {"goal:0"},
        "clock": {"goal:0", "goal:2"}, "advance": {"goal:0", "goal:1", "goal:2"},
    }[phase]
    failure = interrupted_replay(json.dumps(image).encode(), stimulus, _catalogue(plan),
                                 expected_plan=plan, execution={"test": "native collector"})
    assert failure.origin is api.FailureOrigin.VERIFIER
    assert set(failure.observed.catalogue) == (
        set() if phase == "startup" else
        {"goal:0", "goal:1", "goal:2"} if phase in {"clock", "advance"} else {"goal:0", "goal:1"})
    # Same collector/manifest, but a prefix is not admissible normal evidence.
    with pytest.raises(ValueError):
        read_native_evidence(image, stimulus, _catalogue(plan), expected_plan=plan)
    with pytest.raises(api.FailureRaised):
        observer.finish()


@pytest.mark.eda("g++")
def test_shutdown_evidence_retains_no_result_and_exact_mapping(tmp_path):
    plan = _plan(count=1)
    stimulus = api.Stimulus(slots=[api.EventSlot()])
    library = _load(tmp_path, plan)
    observer = NativeObservation(plan, stimulus, library=library)
    observer.start_slots()
    observer.sampled()
    library.__behave_native_event_hit(0)
    image = observer.interrupt()
    catalogue = _catalogue(plan)
    for mutation in (None, "unprepared", "other-plan", "other-run", "phase", "normal", "extra"):
        changed = copy.deepcopy(image)
        expected = plan
        if mutation == "unprepared":
            expected = None
        elif mutation == "other-plan":
            expected = replace(plan, build_sha256="f" * 64)
        elif mutation == "other-run":
            changed["stimulus"] = "other"
        elif mutation == "phase":
            changed["intervals"][-1]["phase"] = "control"
        elif mutation == "normal":
            changed["version"] = VERSION
        elif mutation == "extra":
            changed["intervals"].extend(changed["intervals"])
        failure = interrupted_replay(json.dumps(changed), stimulus, catalogue,
                                     expected_plan=expected, execution={})
        assert failure.origin is api.FailureOrigin.VERIFIER
        assert failure.disposition is api.FailureDisposition.NO_RESULT
        assert failure.reason.kind == ("rtl-native-run-interrupted" if mutation is None else
                                       "rtl-native-prefix-invalid")
        if mutation is None:
            assert thaw_record_value(failure.reason.detail)["source_evidence"] == image


@pytest.mark.eda("g++")
@pytest.mark.parametrize("complete", [False, True])
def test_clock_return_prefix_distinguishes_hits_from_a_completed_successor(tmp_path, complete):
    plan = _plan(count=2)
    stimulus = api.Stimulus(slots=[api.EventSlot()])
    library = _load(tmp_path, plan)
    observer = NativeObservation(plan, stimulus, library=library)
    observer.start_slots()
    observer.sampled()
    library.__behave_native_event_hit(0)
    observer.clocked()
    library.__behave_native_event_hit(1)
    if complete:
        assert set(goal for goal, hit in observer.successor() if hit) == {"goal:0", "goal:1"}
    image = observer.interrupt()
    prefix = read_native_prefix(image, stimulus, _catalogue(plan), expected_plan=plan)
    assert prefix.phase == ("between" if complete else "advance")
    assert [item.goals for item in prefix.completed] == ([{"goal:0", "goal:1"}] if complete else [])
    assert prefix.pending.goals == (set() if complete else {"goal:0", "goal:1"})
    assert image["completed_steps"] == int(complete)
    with pytest.raises(ValueError):
        read_native_evidence(image, stimulus, _catalogue(plan), expected_plan=plan)


@pytest.mark.cosim
@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("phase", ["control", "clock"])
def test_both_production_drivers_preserve_real_fatal_prefix(tmp_path, monkeypatch, atomic, phase):
    """Inject a test-owned plan: this tests runtime, not source-failure binding."""
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.observation_binding import ObservationBinding, PreparedObservation

    from .test_rtl_observation_binding import _prepared

    # No textual traceback matching: the collector survives the actual native
    # termination and cocotb unwinds the very same production replay coroutine.
    fatal = '$fatal(1, "intentional runtime transport test");'
    control_process = (f'always @(posedge rst) begin __behave_native_event_hit(1); {fatal} end'
                       if phase == "control" else '')
    clock_failure = f'if(q == 2) begin __behave_native_event_hit(1); {fatal} end' if phase == "clock" else ''
    text = f'''module M(input clk,rst,out_ready{', input bclk,brst' if atomic else ''},
                        output out_valid,output [7:0] y);
      import "DPI-C" context function void __behave_native_event_hit(input int unsigned id);
      reg [7:0] q=0;
      assign y=q; assign out_valid=0;
      always @(posedge clk) begin
        __behave_native_event_hit(0);
        {clock_failure}
        q=q+1;
        __behave_native_event_hit(2);
      end
      {control_process}
    endmodule'''
    source = tmp_path / "M.sv"
    source.write_text(text)
    plan = _plan(count=3)
    prepared = replace(_prepared(), source=text, collector=plan.collector_source(), plan=plan)
    clocks = [api.ClockDomain("a", "clk", "rst")]
    if atomic:
        clocks.append(api.ClockDomain("b", "bclk", "brst"))
    # Reset is the declared environment control for a drive-phase interruption.
    endpoint = api.Endpoint("out", api.Direction.OUT, [api.Field("y", api.FiniteType(8), "y")],
                            api.ready_valid("out", "out_valid", "out_ready"), clock="a",
                            kind=api.Kind.TRANSACTION)
    contract = api.TaskContract(clocks=clocks, endpoints=[endpoint])
    slots = [api.EventSlot(reset=(phase == "control" and i == 2),
                          controls={"rst": int(phase == "control" and i == 2), "out_ready": 1})
             for i in range(4)]
    schedule = None
    if atomic:
        slots = [api.EventSlot(ticks=("a", "b"), resets=("a", "b") if phase == "control" and i == 2 else (),
                              controls={"rst": int(phase == "control" and i == 2),
                                        "brst": int(phase == "control" and i == 2), "out_ready": 1})
                 for i in range(4)]
        schedule = GlobalSchedule(
            tuple(ClockTiming(c.name, 10, clock=c.clock, reset=c.reset) for c in clocks),
            tuple(GlobalEventSlot(i * 10, event=slot) for i, slot in enumerate(slots)))
    stimulus = api.Stimulus(slots=slots, clock_schedule=schedule)
    artifact = RTLArtifact(source, "M", tmp_path / "run")
    catalogue = _catalogue(plan)
    artifact._frontend_catalogue = catalogue
    artifact._observation = PreparedObservation(ObservationBinding(
        artifact.identity, contract.digest, catalogue.digest(), plan.identity,
        prepared.request_sha256), prepared)
    monkeypatch.setattr(artifact, "frontend_catalogue", lambda **_: catalogue)
    result, = artifact.traces_per_process(contract, [stimulus], workers=1)
    assert type(result) is api.RunFailure, result
    assert result.disposition is api.FailureDisposition.NO_RESULT
    if result.reason.kind != "rtl-native-run-interrupted":
        pytest.fail(json.dumps(result.as_dict(), indent=2))
    detail = thaw_record_value(result.reason.detail)
    prefix = read_native_prefix(detail["source_evidence"], stimulus, catalogue, expected_plan=plan)
    assert [h.goals for h in prefix.completed] == [{"goal:0", "goal:2"}] * 2
    assert prefix.phase == phase
    assert prefix.pending.goals == ({"goal:0", "goal:1"} if phase == "clock" else {"goal:1"})
