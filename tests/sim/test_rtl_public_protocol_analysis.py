"""Protocol goals through public scoring, exact queries and native witnesses."""

import json
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.core.audit import AuditedWitness
from behave.sim.core.outcome import AnalysisOutcome
from behave.sim.rtl.exact_product import global_schedule_for_stimuli

from .test_rtl_public_analysis_path import _BaselineOnly

pytestmark = pytest.mark.cosim


def _frame_monitor():
    events = []
    transitions = []
    for first, last in ((1, 1), (1, 0), (0, 1), (0, 0)):
        name = f"beat-{first}-{last}"
        condition = api.GoalCondition("and",
            api.GoalCondition("eq", api.GoalField("first"), first),
            api.GoalCondition("eq", api.GoalField("last"), last))
        events.append(api.TransactionEvent(name, "out", condition))
        for opened in (0, 1):
            goals = ("invalid-frame",) if first == opened else ()
            transitions.append(api.FiniteMonitorTransition(opened, name, 1 - last, goals))
    # Explicit fallback is required even when the finite fields exhaust the cases.
    events.append(api.TransactionEvent("other", "out"))
    transitions.extend(api.FiniteMonitorTransition(state, "other", state, ("invalid-frame",))
                       for state in (0, 1))
    machine = api.FiniteMonitor((0, 1), tuple(event.event for event in events), 0,
                               ("invalid-frame",), transitions)
    return api.TransactionMonitorSpec("framing", machine, events,
                                      order=("out",), violations=("invalid-frame",))


def _fixture(kind, faulty):
    framed = kind == "framed"
    tagged = kind in ("tagged", "reordered")
    names = ("data", "first", "last") if framed else (("id", "data") if tagged else ("data",))
    endpoints = []
    for direction in ("in", "out"):
        fields = [api.Field(name, api.FiniteType(2 if name == "data" else 1),
                            f"{direction}_{name}") for name in names]
        options = {"kind": api.Kind.TRANSACTION}
        if direction == "in":
            options.update(max_outstanding=1, response_deadline=2)
            if tagged:
                options["reset_policy"] = api.ResetPolicy.CLEARS
            if kind == "reordered":
                options.update(max_outstanding=2, response_deadline=3, reset_policy=api.ResetPolicy.CLEARS)
            if framed:
                options["framing"] = api.InputFraming((api.FrameLevel("first", "last", 2),))
        else:
            options["answers_per_transaction"] = 1
            if tagged:
                options.update(matching=api.Matching.IDENTIFIER, identifier="id")
        endpoints.append(api.Endpoint(direction, direction, fields,
            api.ready_valid(direction, f"{direction}_valid", f"{direction}_ready"), "clk", **options))
    contract = api.TaskContract(endpoints, [api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=1)],
        transaction_monitors=(_frame_monitor(),) if framed else ())
    ports = ", ".join(f"{direction} {'[1:0] ' if name == 'data' else ''}{prefix}_{name}"
        for direction, prefix in (("input", "in"), ("output", "out")) for name in names)
    if kind == "reordered":
        second_id = "id0" if faulty else "id1"
        body = f"""reg [1:0] phase; reg id0,id1; reg [1:0] data0,data1;
          assign in_ready = phase == 0 ? (in_data == 0 ? out_ready : 1'b1) : phase == 1;
          assign out_valid = phase == 0 ? (in_valid && in_data == 0) : phase >= 2;
          assign out_id = phase == 0 ? in_id : (phase == 2 ? {second_id} : id0);
          assign out_data = phase == 0 ? in_data : (phase == 2 ? data1 : data0);
          always @(posedge clk) begin
            if (rst) begin phase <= 0; id0 <= 0; id1 <= 0; data0 <= 0; data1 <= 0; end
            else if (phase == 0 && in_valid && in_ready && in_data != 0) begin
              id0 <= in_id; data0 <= in_data; phase <= 1; end
            else if (phase == 1 && in_valid) begin id1 <= in_id; data1 <= in_data; phase <= 2; end
            else if (phase >= 2 && out_ready) phase <= phase == 2 ? 3 : 0;
          end"""
    elif framed or tagged:
        assignments = []
        for name in names:
            value = "in_first && in_data != 2'd3" if faulty and name == "first" else f"in_{name}"
            if faulty and tagged and name == "id":
                value = "in_id ^ (in_data == 2'd3)"
            assignments.append(f"assign out_{name} = {value};")
        body = "assign in_ready = out_ready; assign out_valid = in_valid;" + "".join(assignments)
    else:
        valid = "pending && held != 2'd3" if faulty else "pending"
        body = f"""reg pending; reg [1:0] held;
          assign in_ready = !pending;
          assign out_valid = {valid};
          assign out_data = held;
          always @(posedge clk) begin
            if (rst) begin pending <= 0; held <= 0; end
            else if (!pending && in_valid) begin pending <= 1; held <= in_data; end
            else if (pending && out_ready) pending <= 0;
          end"""
    source = f"""module dut(input clk, rst, in_valid, out_ready,
      output in_ready, out_valid, {ports}); {body} endmodule"""
    arguments = ", ".join(f"in_{name}" for name in names)
    result = ", ".join(f"'out_{name}': in_{name}" for name in names)
    reference = f"class M:\n def reset(self): pass\n def process(self, {arguments}): return {{{result}}}\n"
    target = ("monitor:framing:invalid-frame" if framed else
              "monitor.output-without-transaction" if tagged else "monitor.response-deadline")
    return contract, source, reference, target


def _check_protocol(tmp_path, kind, faulty, *, stalled=False, fixed_schedule=True):
    contract, source, reference_source, target = _fixture(kind, faulty)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": reference_source.encode()}, config={"class": "M"})
    path = tmp_path / "dut.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="dut", contract=contract),
                     reference=reference)
    generation = api.GenerationSpec(horizon=7, queue_bound=2, reset_hold_cycles=1, drain=4, stall_every=0)
    baseline = api.build_baseline(contract, generation,
        policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1 if kind in ("tagged", "reordered") else 2))
    schedule = global_schedule_for_stimuli(contract, baseline.stimuli)
    if stalled:
        # Keep the same clock/reset and releases, but withhold output readiness.
        # Input framing advances on acceptance, not on a presented/stalled beat.
        slots = []
        for index, slot in enumerate(schedule.slots):
            event = slot.event
            slots.append(api.GlobalEventSlot(slot.time, event=api.EventSlot(
                offers=event.offers, ticks=event.ticks, resets=event.resets,
                reset=event.reset, controls={**dict(event.controls), "out_ready": int(index not in (1, 2))})))
        schedule = api.GlobalSchedule(schedule.timings, slots)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "dut", tmp_path / "rtl"), generation,
                             analysis_clock_schedule=schedule if fixed_schedule else None)
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command",
        baseline=baseline, providers=(_BaselineOnly(),), probe_budget=0, reference=reference,
        run_analysis=True, analysis_wall_timeout_seconds=180))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.required_complete and result.evaluation.passed, result.evaluation.as_dict()
    image = result.analysis.as_dict()
    assert AnalysisOutcome.from_dict(image).as_dict() == image
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, image
    assert set(image["per_goal"]) == set(result.evaluation.catalogue)
    assert all(goal["status"] in {"reached", "bounded-unreachable"}
               for goal in image["per_goal"].values()), image
    assert target not in result.evaluation.hits
    hit = image["per_goal"][target]
    assert hit["status"] == ("reached" if faulty else "bounded-unreachable"), image
    if faulty:
        row, = (row for row in image["evidence"]["witnesses"] if row["witness_ref"] == hit["support_ref"])
        assert row["origin"] in {"feasibility-query", "bounded-query"}
        witness = AuditedWitness.from_record(row["trace"])
        assert target in witness.hits
        if kind == "reordered":
            values = witness.inputs[0]
            assert values["queue.in.length"] == 2
            assert values["queue.in.0.id"] != values["queue.in.1.id"]
        if kind == "framed":
            frame = contract.inputs()[0].framing
            state = frame.initial
            values = witness.inputs[0]
            for index in range(values["queue.in.length"]):
                state, legal = frame.step(state, {
                    name: values[f"queue.in.{index}.{name}"] for name in ("first", "last")})
                assert legal  # No illegal environment is used to manufacture a DUT fault.
            assert any(payload["data"] == 3 and payload["first"] == 0
                       for batch in witness.outputs for endpoints in batch
                       for name, payload in endpoints.items() if name == "out")
    assert all(set(row["passed_audits"]) >= {"step_audit", "source_audit"}
               for row in image["evidence"]["queries"] if row["answer"] == "sat")
    record = json.loads(Path(result.record_path).read_text())
    assert record["authority"]["passed"] is True
    assert record["authority"]["evaluation_fingerprint"] == image["concrete"]
    assert record["report"]["analysis"] == image


@pytest.mark.parametrize(("kind", "faulty"), (
    ("framed", False), ("framed", True), ("ordered", False), ("ordered", True),
    ("tagged", False), ("tagged", True), ("reordered", False), ("reordered", True)))
def test_public_protocol_analysis_preserves_domain_and_score(tmp_path, kind, faulty):
    _check_protocol(tmp_path, kind, faulty)


@pytest.mark.parametrize("kind", ("framed", "ordered", "tagged"))
@pytest.mark.parametrize("faulty", (False, True))
def test_public_protocol_analysis_preserves_backpressure_semantics(tmp_path, kind, faulty):
    _check_protocol(tmp_path, kind, faulty, stalled=True)


@pytest.mark.parametrize("faulty", (False, True))
def test_public_identifier_analysis_with_symbolic_single_clock_schedule(tmp_path, faulty):
    _check_protocol(tmp_path, "tagged", faulty, fixed_schedule=False)


@pytest.mark.parametrize(('policy', 'pending_at_reset'), (
    ('clears', True), ('keeps', True), (None, True), (None, False)))
def test_public_analysis_honors_mid_traffic_reset_policy(tmp_path, policy, pending_at_reset):
    base, source, reference_source, target = _fixture('ordered', False)
    contract = api.TaskContract([
        api.Endpoint.from_dict({**base.inputs()[0].as_dict(), 'reset_policy': policy}),
        *base.outputs()], base.clocks)
    path = tmp_path / 'dut.sv'
    path.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': reference_source.encode()}, config={'class': 'M'})
    task = api.admit(contract, api.realization_of_rtl_source(source, top='dut', contract=contract),
                     reference=reference)
    generation = api.GenerationSpec(horizon=7, queue_bound=1, reset_hold_cycles=1, drain=4, stall_every=0)
    baseline = api.build_baseline(contract, generation,
        policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1))
    slots = []
    for i in range(7):
        reset = i in (0, 2)
        slots.append(api.GlobalEventSlot(i * 2, event=api.EventSlot(
            ticks=('clk',), resets=('clk',) if reset else (),
            offers=('in',) if i == (1 if pending_at_reset else 3) else (),
            controls={'rst': int(reset), 'out_ready': 1})))
    schedule = api.GlobalSchedule([api.ClockTiming('clk', 2, clock='clk', reset='rst')], slots)
    handle = api.prepare_rtl(task, api.RTLConfig(path, 'dut', tmp_path / 'rtl'), generation,
                             analysis_clock_schedule=schedule)
    result = api.handle_command(handle.command_request(run_directory=tmp_path / 'command',
        baseline=baseline, providers=(_BaselineOnly(),), probe_budget=0, reference=reference,
        run_analysis=True, analysis_wall_timeout_seconds=180))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.required_complete and result.evaluation.passed
    image = result.analysis.as_dict()
    assert AnalysisOutcome.from_dict(image).as_dict() == image
    if policy is None and pending_at_reset:
        assert result.analysis.verdict is api.Verdict.REFUSED, image
        assert not image.get('per_goal'), image
        assert 'monitor.reset-policy-unresolved' in str(image['evidence']['refusal']), image
    else:
        assert result.analysis.verdict is api.Verdict.CLASSIFIED, image
        assert set(image['per_goal']) == set(result.evaluation.catalogue)
        assert all(goal['status'] in {'reached', 'bounded-unreachable'}
                   for goal in image['per_goal'].values()), image
        hit = image['per_goal'][target]
        assert hit['status'] == ('reached' if policy == 'keeps' else 'bounded-unreachable'), image
        if policy == 'keeps':
            row, = (row for row in image['evidence']['witnesses'] if row['witness_ref'] == hit['support_ref'])
            witness = AuditedWitness.from_record(row['trace'])
            assert target in witness.hits
        assert all(set(row['passed_audits']) >= {'step_audit', 'source_audit'}
                   for row in image['evidence']['queries'] if row['answer'] == 'sat')
    record = json.loads(Path(result.record_path).read_text())
    assert record['authority']['passed'] is True
    assert record['authority']['evaluation_fingerprint'] == image['concrete']
