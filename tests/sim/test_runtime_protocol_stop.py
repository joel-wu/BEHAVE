"""A determined protocol counterexample ends an open-ended replay, not a timer."""

from itertools import islice

import pytest

from behave.sim import api
from behave.sim.core.driver import Driver
from behave.sim.core.monitor import monitor_for
from behave.sim.core.runtime_schedule import ReplayPolicy, validate_resolution
from behave.sim.run_assessment import RunAssessment, assess_run

from .test_runtime_schedule import _contract
from .test_trace_postprocessing import packet


def _request(depth=1):
    return api.Stimulus(queues={"in": [(7,)] * depth},
                        replay_policy=ReplayPolicy(0, 200, 2))


@pytest.mark.parametrize("second", [
    {"ir": 1, "ov": 0, "y": 7},
    {"ir": 1, "ov": 1, "y": 8},
])
def test_stalled_offer_violation_stops_at_its_observation(second):
    contract, request = _contract(), _request(16)
    driver = Driver(contract, request, artifact_identity="candidate")
    slots = driver.iter_slots()
    for signals in ({"ir": 1, "ov": 1, "y": 7}, second):
        index, _ = next(slots)
        driver.observe(index, driver.presentations(index), signals)
    assert next(slots, None) is None
    assert driver.protocol_stop.goal == "monitor.sink-stability"
    assert driver.protocol_stop.slot == 1
    trace = driver.trace()
    assert trace.completion is api.Completion.INCOMPLETE
    assert len(trace.records) == len(trace.stimulus.slots) == 2
    assert len(request.queues[0][1]) == 16 and request.slots == ()
    # No second history grows inside the live fact conversion.
    assert not driver._protocol_facts.slots
    report = monitor_for(contract).check(trace.stimulus, trace)
    assert report.violations[0].as_dict() == driver.protocol_stop.as_dict()
    validate_resolution(contract, request, trace.stimulus, trace.records, None,
                        protocol_stop=driver.protocol_stop.as_dict())

    def unexpected_comparison(*_):
        pytest.fail("a determined protocol failure must not wait for a reference")

    result = assess_run(None, request, trace, None, monitor=monitor_for(contract),
                        compare=unexpected_comparison)
    assert type(result) is RunAssessment
    failure = result.result.comparison_failure
    assert failure.origin is api.FailureOrigin.CANDIDATE
    assert failure.kind == "monitor.sink-stability"
    assert failure.reason.detail["slot"] == 1
    assert failure.determines_score


def test_stable_stall_transfer_and_long_compute_are_not_faults():
    request, contract = _request(), _contract()
    driver = Driver(contract, request)
    slots = driver.iter_slots()
    # No guessed latency limit: the response is silent for 600 slots.
    for index, _ in islice(slots, 600):
        driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 0, "y": 0})
    assert driver.protocol_stop is None
    index, _ = next(slots)
    driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 1, "y": 7})
    for index, _ in slots:
        driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 0, "y": 0})
    assert driver.protocol_stop is None
    assert driver.trace().completion is api.Completion.NORMAL
    assert len(driver.records) == 801


def test_stall_is_held_until_transfer_even_when_ready_rises():
    driver = Driver(_contract(), _request())
    slots = driver.iter_slots()
    for _ in range(2):
        index, _slot = next(slots)
        driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 1, "y": 7})
    for index, _slot in slots:
        driver.observe(index, driver.presentations(index), {"ir": 1, "ov": 0, "y": 7})
    assert driver.protocol_stop is None
    assert driver.trace().accepted_count(["out"]) == 1


def test_declared_response_deadline_stops_but_does_not_invent_a_wait_limit():
    from .test_api_path import contract as deadline_contract

    contract = deadline_contract(answers=1, deadline=4)
    request = api.Stimulus(queues={"in": [(7,)]}, replay_policy=ReplayPolicy(0, 200, 0))
    driver = Driver(contract, request)
    for index, _ in islice(driver.iter_slots(), 20):
        driver.observe(index, driver.presentations(index),
                       {"in_ready": 1, "out_valid": 0, "out_y": 0})
    assert driver.protocol_stop.goal == "monitor.response-deadline"
    assert driver.protocol_stop.slot == 5
    trace = driver.trace()
    assert monitor_for(contract).check(trace.stimulus, trace).violations[0].as_dict() \
        == driver.protocol_stop.as_dict()


def test_adapter_owned_apb_wait_limit_uses_the_same_runtime_stop():
    from .test_apb_protocol import _contract as apb_contract

    contract = apb_contract(wait_limit=2)
    request = api.Stimulus(queues={"in": [(7,)]}, replay_policy=ReplayPolicy(0, 200, 0))
    driver = Driver(contract, request)
    for index, _ in islice(driver.iter_slots(), 20):
        driver.observe(index, driver.presentations(index),
                       {"pready": 0, "prdata": 0, "pslverr": 0})
    assert driver.protocol_stop.goal == "monitor.apb.wait-limit"
    trace = driver.trace()
    assert monitor_for(contract).check(trace.stimulus, trace).violations[0].as_dict() \
        == driver.protocol_stop.as_dict()
    validate_resolution(contract, request, trace.stimulus, trace.records, None,
                        protocol_stop=driver.protocol_stop.as_dict())


def _stopped_packet():
    contract, request = _contract(), _request(16)
    driver = Driver(contract, request, artifact_identity="candidate")
    for index, _ in driver.iter_slots():
        driver.observe(index, driver.presentations(index),
                       {"ir": 1, "ov": int(index == 0), "y": 7})
    trace = driver.trace()
    plan, _, document = packet(contract, trace.stimulus, trace)
    wire = document["traces"][0]
    wire["resolved_stimulus"] = trace.stimulus.as_dict()
    wire["protocol_stop"] = driver.protocol_stop.as_dict()
    return plan, request, wire


@pytest.mark.parametrize("corruption", [None, "missing", "slot", "goal", "pins"])
def test_decoder_rechecks_prefix_stop_against_actual_protocol_samples(corruption):
    plan, request, wire = _stopped_packet()
    if corruption == "missing":
        del wire["protocol_stop"]
    elif corruption in {"slot", "goal"}:
        wire["protocol_stop"][corruption] = 0 if corruption == "slot" else "invented"
    elif corruption == "pins":
        # A stable offer must not be truncated merely because the packet says so.
        wire["records"][1]["outputs"] = [["ir", 1], ["ov", 1], ["y", 7]]
    from behave.sim.core.driver import check_channel_shape

    got = plan.decoder.decode(plan.contract, request, wire, check_channel_shape, "candidate")
    if corruption is None:
        assert type(got) is api.AuditedTrace
        assert got.completion is api.Completion.INCOMPLETE
        assert len(got.records) == 2
    else:
        assert isinstance(got, api.Refusal)


def test_explicit_fixed_schedule_remains_an_exact_replay_not_a_shorter_domain():
    contract = _contract()
    seed = api.reset_then(contract, [(7,)], drain=6, controls={"orr": 0})
    driver = Driver(contract, seed)
    for index, slot in driver.iter_slots():
        driver.observe(index, driver.presentations(index),
                       {"ir": 1, "ov": int(not slot.reset and index % 2 == 0), "y": 7})
    assert len(driver.records) == len(seed.slots)
    assert driver.protocol_stop is None
    assert monitor_for(contract).check(seed, driver.trace()).violations


def test_atomic_monitor_preserves_stall_across_inactive_domain_edges():
    from dataclasses import replace

    from behave.sim.core.atomic import AtomicDriver
    from behave.sim.core.clock_schedule import ClockTiming

    from .test_runtime_multiclock_schedule import contract as multiclock_contract

    contract = multiclock_contract()
    policy = replace(api.ReplayPolicy.for_contract(contract), reset_hold_cycles=0,
                     stall_every=2, clock_timings=(ClockTiming("write", 2, clock="wc", reset="wr"),
                                                  ClockTiming("read", 3, clock="rc", reset="rr")))
    request = api.Stimulus(queues={"in": [(7,)] * 100}, replay_policy=policy)
    driver = AtomicDriver(contract, request, expected_outputs={"out": 100})
    held_at, inactive = None, 0
    for index, slot in islice(driver.iter_slots(), 30):
        drive = driver.drive(index)
        active = "read" in slot.ticks
        offer = active and held_at is None and drive.signals["orr"] == 0
        if offer:
            held_at = index
        if held_at is not None and not active:
            inactive += 1
        driver.observe(index, drive, {"ir": 1, "ov": int(offer), "y": 7})
    assert held_at is not None and inactive
    assert driver.protocol_stop.slot > held_at
    assert "read" in driver.completed_stimulus().slots[-1].ticks
    trace = driver.trace()
    assert trace.completion is api.Completion.INCOMPLETE
    report = monitor_for(contract).check(trace.stimulus, trace)
    assert report.violations[0].as_dict() == driver.protocol_stop.as_dict()
    assert not report.assumption_violations
    validate_resolution(contract, request, trace.stimulus, trace.records, {"out": 100},
                        protocol_stop=driver.protocol_stop.as_dict())


@pytest.mark.cosim
@pytest.mark.parametrize("fault", ["drop", "change", "none"])
def test_public_native_replay_returns_protocol_witness_without_waiting_for_missing_outputs(
        tmp_path, fault, record_property):
    import json
    import time

    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b'class M:\n    def reset(self): pass\n'
                        b'    def process(self, a): return {"y": a}\n'}, config={"class": "M"})
    wrong = {"drop": "if(ov && !orr) ov<=0;", "change": "if(ov && !orr) y<=y+1;",
             "none": ""}[fault]
    source = f'''module M(input clk,rst,iv,input [7:0] a,output ir,
                       output reg ov, input orr,output reg [7:0] y);
      assign ir=!ov || orr;
      always @(posedge clk) begin
        if(rst) begin ov<=0; y<=0; end
        else begin
          if(orr) ov<=0;
          if(iv && ir) begin ov<=1; y<=a; end
          {wrong}
        end
      end
    endmodule'''
    path = tmp_path / "rtl.sv"
    path.write_text(source)
    began = time.monotonic()
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "artifact"))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=16),
        search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False))
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.decision is not None and result.decision.authoritative, result.as_dict()
    assert result.decision.passed is (fault == "none"), result.as_dict()
    traces = [run.trace for run in result.evaluation.results if run.trace is not None]
    assert traces and all(trace.source_evidence is not None for trace in traces)
    if fault != "none":
        failures = [run.comparison_failure for run in result.evaluation.results
                    if run.comparison_failure is not None]
        assert failures and all(item.kind == "monitor.sink-stability" for item in failures)
        assert all(len(trace.records) < 30 for trace in traces)
        assert all(trace.completion is api.Completion.INCOMPLETE for trace in traces)
        assert not result.evaluation.no_result_failures
    else:
        assert result.evaluation.complete
        assert all(trace.completion is api.Completion.NORMAL for trace in traces)
    record_property("runtime_protocol_stop", json.dumps({"fault": fault,
        "seconds": time.monotonic() - began, "slots": [len(trace.records) for trace in traces]}))
