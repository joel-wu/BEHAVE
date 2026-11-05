"""Drive/clock/drain histories on the real runner.

Test-owned replay plans isolate scheduling below the public scoring layer.
They do not monkeypatch scheduling or event hits.
"""

import json
import pickle
import re
import xml.etree.ElementTree as ET

import pytest

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.harness import generate_makefile, render_template
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.execution_policy import execution_policy
from behave.sim.rtl.native_observation import _events
from behave.sim.rtl.native_preparation import PreparedNativeEvents, prepare_source_observer
from behave.sim.rtl.replay_plan import ATOMIC_DRIVER_PLAN_VERSION, DRIVER_PLAN_VERSION, ReplayPlan
from behave.sim.rtl.tool_isolation import run_verilator_workspace
from behave.sim.rtl.tool_protocol import command_result

pytestmark = [pytest.mark.cosim, pytest.mark.eda("slang")]


def _prepare_observer(root, source):
    """Use the public source catalogue, not the retired action-count fixture."""
    path = root / "original.sv"
    path.write_text(source)
    catalogue = RTLArtifact(path, "M", root / "catalogue").frontend_catalogue()
    prepared = prepare_source_observer(source, top="M", execution_sha256="2"*64,
                                      scope_root="", callback_limit=100, catalogue=catalogue)
    assert type(prepared) is PreparedNativeEvents, prepared
    return prepared, catalogue


def _meaning(family, text, outcome="entry"):
    return family, re.sub(r"\s+", "", text), outcome


def _interval_meanings(source, prepared, catalogue, intervals):
    # Bind coordinates to source labels; expectations below specify independently
    # which statements/arms execute. No event count or compiler ordinal is guessed.
    meanings = {g.id: _meaning(g.family, source[g.where("start"):g.where("end")], g.event)
                for g in catalogue.values()}
    return [{meanings[g] for g in prepared.plan.bindings.decode(
        _events(row["events"], prepared.plan.bindings.event_count)).goals} for row in intervals]


def _run_driver(root, source, contract, stimulus, prepared, *, native, state_widths=None):
    root.mkdir()
    (root / "rtl.sv").write_text(prepared.source if native else source)
    (root / "collector.cpp").write_text(prepared.collector)
    (root / "driver_tb.py").write_text(render_template("driver_tb.py.tmpl", period_ns=10))
    atomic = len(contract.clocks) > 1
    pins = sorted({field.port for endpoint in contract.endpoints for field in endpoint.fields}
                  | {pin for clock in contract.clocks for pin in (clock.clock, clock.reset)}
                  | {pin for endpoint in contract.endpoints
                     for pin in (*endpoint.adapter.environment_controls, *endpoint.adapter.artifact_controls)})
    plan = {
        "version": ATOMIC_DRIVER_PLAN_VERSION if atomic else DRIVER_PLAN_VERSION,
        "contract": contract, "stimuli": [stimulus],
        "watch": pins, "record_names": pins,
        "initial_state": None, "execution_policy": execution_policy(native_events=native),
    }
    if atomic:
        plan.update(state_fields=[[name, width] for name, width in (state_widths or {}).items()],
                    artifact="test-owned")
    else:
        plan["state_names"] = list(state_widths or {})
    if native:
        plan["native_events"] = prepared.plan.as_dict()
    ReplayPlan.from_dict(plan)
    (root / "plan.pickle").write_bytes(pickle.dumps(plan))
    generate_makefile(root, "M", tb_module="driver_tb", coverage=False, native_events=True)
    built, replayed = run_verilator_workspace(
        root, top_module="M", test_module="driver_tb", role="native-boundary-oracle",
        outputs=("driven.json", "native_prefix.json", "results.xml"),
        compile_timeout_s=90, simulate_timeout_s=30)
    assert all(row["returncode"] == 0 for row in built.commands), built.as_dict()
    (root / "execution.json").write_text(json.dumps(replayed.as_dict(), indent=2))
    assert command_result(replayed, "native-boundary-oracle:simulate")["returncode"] == 0, replayed.as_dict()
    cases = list(ET.fromstring((root / "results.xml").read_bytes()).iter("testcase"))
    assert len(cases) == 1 and cases[0].find("failure") is None, replayed.as_dict()
    from behave.sim.rtl.trace_document import read_trace_document

    traces, failure = read_trace_document((root / "driven.json").read_bytes(), max_traces=1)
    assert failure is None
    return traces[0]


@pytest.mark.parametrize("mode", ["settled", "single", "atomic"])
def test_comb_runner_keeps_startup_repeated_inputs_and_final_drain(tmp_path, mode):
    clocks = [] if mode == "settled" else [api.ClockDomain("a", "clk", "rst")]
    if mode == "atomic":
        clocks.append(api.ClockDomain("b", "bclk", "brst"))
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", api.FiniteType(8), "in_a")],
                     api.settled("in") if not clocks else api.sampled_edge("in", valid="in_valid"),
                     "a" if clocks else None, kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(8), "out_y")],
                     api.settled("out") if not clocks else api.sampled_edge("out", valid="out_valid"),
                     "a" if clocks else None, kind=api.Kind.TRANSACTION)], clocks)
    ports = "".join(f", input {clock.clock}, {clock.reset}" for clock in clocks)
    if clocks:
        ports += ", input in_valid, output out_valid"
    source = f"""module M(input [7:0] in_a, output reg [7:0] out_y{ports});
        {'assign out_valid=in_valid;' if clocks else ''}
        always_comb if(in_a[0]) out_y=in_a+1; else out_y=in_a;
    endmodule"""
    values = (0, 0, 1, 1, 2)
    # The final two empty slots check actual clock progress with stable zero
    # payloads. Clockless requests have only their explicit input observations.
    count = len(values) + (2 if clocks else 0)
    slots = [api.EventSlot(offers=("in",) if i < len(values) else (),
                          controls={clock.reset: 0 for clock in clocks},
                          ticks=tuple(clock.name for clock in clocks) if mode == "atomic" else ())
             for i in range(count)]
    schedule = None
    if mode == "atomic":
        schedule = GlobalSchedule(
            tuple(ClockTiming(clock.name, 10, clock=clock.clock, reset=clock.reset) for clock in clocks),
            tuple(GlobalEventSlot(i*10, event=slot) for i, slot in enumerate(slots)))
    stimulus = api.Stimulus({"in": [(value,) for value in values]}, slots, clock_schedule=schedule)
    prepared, catalogue = _prepare_observer(tmp_path, source)
    plain = _run_driver(tmp_path / "plain", source, contract, stimulus, prepared, native=False)
    native = _run_driver(tmp_path / "native", source, contract, stimulus, prepared, native=True)
    expected_outputs = [0, 0, 2, 2, 2] + ([0, 0] if clocks else [])
    assert [dict(row["outputs"])["out_y"] for row in plain["records"]] == expected_outputs
    assert [dict(row["outputs"])["out_y"] for row in native["records"]] == expected_outputs
    intervals = native["source_evidence"]["intervals"]
    outer = "if(in_a[0]) out_y=in_a+1; else out_y=in_a;"
    def branch(taken):
        return {_meaning("location", outer), _meaning("control-choice", outer, "true" if taken else "false"),
                _meaning("location", "out_y=in_a+1;" if taken else "out_y=in_a;")}
    valid = {_meaning("location", "out_valid=in_valid")} if clocks else set()
    meanings = _interval_meanings(source, prepared, catalogue, intervals)
    assert intervals[0]["phase"] == "startup" and meanings[0] == branch(False) | valid
    control = [valid, set(), branch(True), set(), branch(False)]
    if clocks:
        control += [branch(False) | valid, set()]
    assert [hits for row, hits in zip(intervals, meanings) if row["phase"] == "control"] == control
    assert all(row["callbacks"] == 0 for row in intervals if row["phase"] in {"clock", "advance"})
    assert len(intervals) == 1 + 3*count


@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("active_edge", ["rising", "falling"])
def test_clock_return_is_part_of_the_observed_cycle(tmp_path, atomic, active_edge):
    """Both physical edges execute in plain and observed replay, including the last."""
    clocks = [api.ClockDomain("a", "clk", "rst", edge=active_edge)]
    if atomic:
        clocks.append(api.ClockDomain("b", "bclk", "brst", edge=active_edge))
    contract = api.TaskContract([api.Endpoint(
        "out", "out", [api.Field("y", api.FiniteType(8), "y")],
        api.ready_valid("out", "ov", "orr"), "a", kind=api.Kind.TRANSACTION)], clocks)
    returned_edge = "negedge" if active_edge == "rising" else "posedge"
    extra = ", input bclk, brst" if atomic else ""
    source = f"""module M(input clk,rst,orr,output ov,output [7:0] y{extra});
      reg [7:0] q=0;
      assign ov=0;
      assign y=q;
      always @({returned_edge} clk) if(rst) q<=0; else q<=q+1;
    endmodule"""
    slots = [api.EventSlot(reset=i == 0, controls={"rst": int(i == 0), "orr": 1})
             for i in range(3)]
    schedule = None
    if atomic:
        slots = [api.EventSlot(ticks=("a", "b"), resets=("a", "b") if i == 0 else (),
                              controls={"rst": int(i == 0), "brst": int(i == 0), "orr": 1})
                 for i in range(3)]
        timings = tuple(ClockTiming(c.name, 10, clock=c.clock, reset=c.reset, edge=c.edge)
                        for c in clocks)
        schedule = GlobalSchedule(timings, tuple(
            GlobalEventSlot(i * 10, event=slot) for i, slot in enumerate(slots)))
    stimulus = api.Stimulus({}, slots, clock_schedule=schedule)
    prepared, catalogue = _prepare_observer(tmp_path, source)
    plain = _run_driver(tmp_path / "plain", source, contract, stimulus, prepared,
                        native=False, state_widths={"q": 8})
    observed = _run_driver(tmp_path / "native", source, contract, stimulus, prepared,
                           native=True, state_widths={"q": 8})
    # A falling-active clock is initialized from 0 to its inactive level 1.
    # That real startup posedge executes before the first reset slot. Reset
    # then clears q on the first return edge, in both plain and observed RTL.
    assert [dict(row["outputs"])["y"] for row in plain["records"]] == [
        int(active_edge == "falling"), 0, 1]
    assert [row["outputs"] for row in observed["records"]] == [row["outputs"] for row in plain["records"]]
    assert [dict(row["state"])["q"] for row in plain["records"]] == [0, 1, 2]
    assert [row["state"] for row in observed["records"]] == [row["state"] for row in plain["records"]]
    evidence = observed["source_evidence"]
    meanings = _interval_meanings(source, prepared, catalogue, evidence["intervals"])
    returned = [hits for row, hits in zip(evidence["intervals"], meanings) if row["phase"] == "advance"]
    assert len(returned) == 3
    assert _meaning("location", "q<=0;") in returned[0]
    assert all(_meaning("location", "q<=q+1;") in hits for hits in returned[1:])
    from behave.sim.rtl.native_observation import read_native_evidence

    steps = read_native_evidence(evidence, stimulus, catalogue, expected_plan=prepared.plan)
    increment = {g.id for g in catalogue.values() if g.family == "location" and
                 re.sub(r"\s+", "", source[g.where("start"):g.where("end")]) == "q<=q+1;"}
    assert increment and all(increment <= hit.goals for hit in steps[1:])


@pytest.mark.parametrize("process", ["always_comb", "always @*", "always @(*)"])
@pytest.mark.parametrize("constant", [False, True])
def test_implicit_startup_preserves_original_initial_evaluation(tmp_path, process, constant):
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", api.FiniteType(8), "in_a")],
                     api.settled("in"), None, kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(8), "out_y")],
                     api.settled("out"), None, kind=api.Kind.TRANSACTION)], [])
    expression = "8'hff" if constant else "~in_a"
    source = f"module M(input [7:0] in_a, output reg [7:0] out_y); {process} out_y={expression}; endmodule"
    stimulus = api.Stimulus({"in": [(0,), (0,), (1,)]}, [api.EventSlot(offers=("in",))] * 3)
    prepared, catalogue = _prepare_observer(tmp_path, source)
    # Empty @* has no triggering signal and never executes. always_comb must
    # still execute once. The baseline must distinguish these source forms.
    wakes = process == "always_comb" or not constant
    outputs = [255, 255, 255 if constant else 254] if wakes else [0, 0, 0]
    for native in (False, True):
        trace = _run_driver(tmp_path / ("native" if native else "plain"), source,
                            contract, stimulus, prepared, native=native)
        assert [dict(row["outputs"])["out_y"] for row in trace["records"]] == outputs
        if native:
            intervals = trace["source_evidence"]["intervals"]
            assert intervals[0]["phase"] == "startup"
            entered = {_meaning("location", f"out_y={expression};")}
            meanings = _interval_meanings(source, prepared, catalogue, intervals)
            assert meanings[0] == (entered if wakes else set())
            # Both the process activation and its statement-entry callback run.
            assert intervals[0]["callbacks"] == 2 * int(wakes)
            expected = [set(), set(), set() if constant else entered]
            assert [hits for row, hits in zip(intervals, meanings) if row["phase"] == "control"] == expected
            assert all(row["callbacks"] == 0 for row in intervals if row["phase"] in {"clock", "advance"})
