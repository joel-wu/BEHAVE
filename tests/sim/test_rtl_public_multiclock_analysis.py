"""State, local resets and coincident edges through public exact Analysis."""

import json
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.core.audit import AuditedWitness
from behave.sim.core.outcome import AnalysisOutcome

from .test_rtl_public_analysis_path import _BaselineOnly

pytestmark = pytest.mark.cosim


REFERENCE = b'''class M:
 def reset(self):
  self.a = 0
  self.b = 0
 def reset_a(self): self.a = 0
 def reset_b(self): self.b = 0
 def process_a(self, a_x):
  self.a = self.a ^ a_x
  return {"a_y": self.a}
 def process_b(self, b_x):
  self.b = self.b ^ b_x
  return {"b_y": self.b}
'''


def _contract(b_edge):
    endpoints = []
    for domain in ("a", "b"):
        for direction, field, valid, ready in (
                (api.Direction.IN, "x", "iv", "ir"),
                (api.Direction.OUT, "y", "ov", "ordy")):
            suffix = "in" if direction is api.Direction.IN else "out"
            endpoints.append(api.Endpoint(f"{domain}_{suffix}", direction,
                [api.Field(field, api.FiniteType(1), f"{domain}_{field}")],
                api.ready_valid(direction, f"{domain}_{valid}", f"{domain}_{ready}"),
                clock=domain, kind=api.Kind.TRANSACTION))
    return api.TaskContract(endpoints, [
        api.ClockDomain(domain, f"{domain}_clk", f"{domain}_rst",
            edge="rising" if domain == "a" else b_edge,
            reset_hold_cycles=1, reset_restores=True)
        for domain in ("a", "b")], observation_goals=[
            api.TransactionGoal(f"{domain}-one", f"{domain}_out",
                api.GoalCondition("eq", api.GoalField("y"), 1))
            for domain in ("a", "b")])


def _source(b_edge, broken_reset):
    ports = []
    blocks = []
    for domain in ("a", "b"):
        ports.append(f"input {domain}_clk,{domain}_rst,{domain}_iv,{domain}_x,{domain}_ordy,"
                     f"output {domain}_ir,{domain}_ov,{domain}_y")
        edge = "negedge" if domain == "b" and b_edge == "falling" else "posedge"
        reset_value = "a_q" if domain == "a" and broken_reset else "1'b0"
        blocks.append(f'''
          reg {domain}_q = 1'b0;
          assign {domain}_ir = {domain}_ordy;
          assign {domain}_ov = {domain}_iv;
          assign {domain}_y = {domain}_q ^ {domain}_x;
          always @({edge} {domain}_clk) begin
            if ({domain}_rst) {domain}_q <= {reset_value};
            else if ({domain}_iv && {domain}_ordy)
              {domain}_q <= {domain}_q ^ {domain}_x;
          end
        ''')
    return "module M(" + ",".join(ports) + ");" + "".join(blocks) + "endmodule"


def _schedule(contract, staggered):
    timings = [api.ClockTiming(clock.name, 3 if staggered and clock.name == "b" else 2, edge=clock.edge,
        clock=clock.clock, reset=clock.reset) for clock in contract.clocks]
    if staggered:
        # Global instants 0, 2, 3, 4, 6, 8, 9, 10: independent edges as well
        # as a simultaneous transfer at 6. Resetting a must retain b's state.
        resets_at = {0: ("a", "b"), 3: ("a",), 6: ("b",)}
        offers_at = {1: ("a_in",), 2: ("b_in",), 4: ("a_in", "b_in")}
        count = 8
    else:
        resets_at = {0: ("a", "b"), 2: ("a",), 3: ("b",)}
        offers_at = {1: ("a_in", "b_in"), 2: ("b_in",), 3: ("a_in",)}
        count = 6
    slots = []
    for index, slot in enumerate(api.GlobalSchedule.periodic(timings, count).slots):
        resets = resets_at.get(index, ())
        slots.append(api.GlobalEventSlot(slot.time, event=api.EventSlot(
            ticks=slot.events, resets=resets, offers=offers_at.get(index, ()),
            controls={"a_rst": int("a" in resets), "b_rst": int("b" in resets),
                      "a_ordy": 1, "b_ordy": 1})))
    return api.GlobalSchedule(timings, slots)


@pytest.mark.parametrize(("b_edge", "broken_reset", "staggered"), (
    ("rising", False, False), ("rising", True, False), ("falling", False, False),
    ("rising", True, True), ("falling", False, True)))
def test_stateful_multiclock_analysis_preserves_native_reset_evidence(
        tmp_path, b_edge, broken_reset, staggered):
    contract = _contract(b_edge)
    source = _source(b_edge, broken_reset)
    path = tmp_path / "stateful.sv"
    path.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
        reference=reference)
    schedule = _schedule(contract, staggered)
    generation = api.GenerationSpec(horizon=len(schedule.slots), queue_bound=2, reset_hold_cycles=1, drain=2)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"), generation,
        analysis_clock_schedule=schedule)
    baseline = api.build_baseline(contract, generation, realization=task.realization,
        policy=api.BaselinePolicy(case_limit=1, max_queue_depth=2))
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command",
        baseline=baseline, providers=(_BaselineOnly(),), probe_budget=0, reference=reference,
        run_analysis=True, analysis_wall_timeout_seconds=180))

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.required_complete and result.evaluation.passed
    image = result.analysis.as_dict()
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, image
    assert AnalysisOutcome.from_dict(image).as_dict() == image
    assert set(image["per_goal"]) == set(result.evaluation.catalogue)
    for domain in ("a", "b"):
        goals = image["per_goal"]
        assert goals[f"monitor.reset-restoration.{domain}.scenario"]["status"] == "reached"
        violation = f"monitor.reset-restoration.{domain}.violation"
        assert goals[violation]["status"] == (
            "reached" if domain == "a" and broken_reset else "bounded-unreachable")
        hit = goals[f"observation:{domain}-one"]
        assert hit["status"] == "reached"
        row, = (row for row in image["evidence"]["witnesses"] if row["witness_ref"] == hit["support_ref"])
        witness = AuditedWitness.from_record(row["trace"])
        assert f"observation:{domain}-one" in witness.hits
        assert any(payload["y"] == 1 for batch in witness.outputs for endpoints in batch
                   for name, payload in endpoints.items() if name == f"{domain}_out")
    if broken_reset:
        # Analysis finds a real reset failure outside the all-zero baseline, but
        # it is not allowed to rewrite the earlier concrete score.
        violation = "monitor.reset-restoration.a.violation"
        assert violation not in result.evaluation.hits
        hit = image["per_goal"][violation]
        row, = (row for row in image["evidence"]["witnesses"] if row["witness_ref"] == hit["support_ref"])
        assert row["origin"] in {"feasibility-query", "bounded-query"}
        if row["origin"] == "feasibility-query":
            assert image["evidence"]["feasibility"]["witness_ref"] == hit["support_ref"]
            assert image["evidence"]["feasibility"]["replayed"] is True
        assert violation in AuditedWitness.from_record(row["trace"]).hits
    assert all(set(query["passed_audits"]) >= {"step_audit", "source_audit"}
               for query in image["evidence"]["queries"] if query["answer"] == "sat")
    for query in image["evidence"]["queries"]:
        if query["solver_calls"]:
            timing = query["telemetry"]["timings_ns"]
            assert timing["solver_load_ns"] >= 0 and timing["solve_ns"] >= 0
            assert timing["total_ns"] >= timing["solver_load_ns"] + timing["solve_ns"]
        if query["answer"] == "sat":
            # A native-proposal SAT has no separately timed symbolic decode;
            # StageTimings leaves an unmeasured stage absent rather than zero.
            timing = query["telemetry"]["timings_ns"]
            assert timing.get("decode_ns") is None or 0 <= timing["decode_ns"] <= timing["total_ns"]
    record = json.loads(Path(result.record_path).read_text())
    assert record["authority"]["passed"] is True
    assert record["authority"]["evaluation_fingerprint"] == image["concrete"]
    assert record["report"]["analysis"] == image
