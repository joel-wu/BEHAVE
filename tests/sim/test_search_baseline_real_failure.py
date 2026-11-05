"""Real lowering refusal after public baseline, without a mocked provider."""

import json
from pathlib import Path
import time

import pytest

from behave.sim import api
from behave.sim.search_policy import active_search

from .test_search_budget_production import RTL


SOURCE = '''class M:
    def reset(self):
        pass
    def add(self, a, *unused):
        return (a + 1) & 255
    def process(self, a):
        if a == 7:
            return {"y": self.add(a=a)}
        return {"y": self.add(a=a)}
'''


@pytest.mark.isolation
@pytest.mark.parametrize("kind,debug,width", [
    ("behavior", False, 8), ("behavior", True, 8),
    pytest.param("rtl", False, 8, marks=pytest.mark.eda),
    pytest.param("rtl", True, 8, marks=pytest.mark.eda),
    pytest.param("rtl", False, 32, marks=pytest.mark.eda, id="rtl-full-default-baseline"),
])
def test_real_unsupported_lowering_preserves_baseline_or_stops_for_debug(
        tmp_path, kind, debug, width):
    started = time.monotonic()
    integer = api.FiniteType(width)
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", integer, "a")],
                     api.ready_valid("in", "in_valid", "in_ready"), clock="clk",
                     kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", integer, "y")],
                     api.ready_valid("out", "out_valid", "out_ready"), clock="clk",
                     kind=api.Kind.TRANSACTION, answers_per_transaction=1)],
        [api.ClockDomain("clk", "clk", "rst")])
    rare = 7 if width == 8 else 305419896
    reference_source = SOURCE.replace("a == 7", f"a == {rare}").replace("255", str((1 << width) - 1))
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": reference_source.encode()}, config={"class": "M"})
    source = (reference_source if kind == "behavior" else
              RTL.replace("[7:0]", f"[{width-1}:0]").replace("8'd1", f"{width}'d1"))
    path = tmp_path / ("candidate.py" if kind == "behavior" else "candidate.sv")
    path.write_text(source)
    spec = api.GenerationSpec(8, 2, 2, 2)
    if kind == "behavior":
        task = api.admit(contract, api.realization_of_behavior_source(
            source, contract, file=path.name, model="M"), reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(
            path, file=path.name, model="M"), spec, bounded_analysis=False)
    else:
        task = api.admit(contract, api.realization_of_rtl_source(
            source, top="M", contract=contract), reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"), spec)
    baseline = api.build_baseline(contract, policy=(api.BaselinePolicy(
        case_limit=3, max_queue_depth=1) if width == 8 else api.BaselinePolicy()))
    if width == 32:
        assert baseline.policy.case_limit == 50
        assert all(seed.depth("in") == 100 for seed in baseline.stimuli)
    assert all(value != rare for seed in baseline.stimuli
               for _, queue in seed.queues for value, in queue)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference, baseline=baseline,
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance"),
        search_policy=api.SearchPolicy(time_budget_s=60, max_probes=1, debug=debug),
        run_analysis=False))
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    (tmp_path / "timing.json").write_text(json.dumps({"e2e_seconds": time.monotonic() - started}))
    saved = json.loads(Path(result.record_path).read_text())
    assert active_search() is None
    assert saved["progress"]["baseline"]["complete"] is True
    if debug:
        assert result.status is api.CommandStatus.REFUSED, result.as_dict()
        assert not result.decision.authoritative
        assert result.decision.passed is None
        assert result.evaluation is None
        assert result.failure.kind == "generation-debug-stop"
        # Failure must actually come from the source lowering, not an injected
        # exception or a deadline that happened before it could be attempted.
        assert "call-signature" in str(result.failure.as_dict()), result.as_dict()
        assert saved["progress"]["baseline_evaluation"]["passed"] is True
    else:
        assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
        assert result.decision.authoritative and result.decision.passed
        assert result.evaluation.complete
        assert result.generated.stimuli == result.generated.baseline
        first = result.generated.searches[0]
        assert first.telemetry["baseline_coverage"]["remaining"]
        assert "call-signature" in dict(first.telemetry["refusals"]), first.as_dict()
        assert first.telemetry["probes"] == 0
        # This lowering refusal belongs to a recorded query domain, not the
        # entire provider or the other search direction. Its diagnostic stays
        # visible even when the scheduler eventually empties its queue or uses
        # the shared Probe cap in the independently available RTL direction.
        assert first.telemetry["provider_refusal"] is None
        assert first.telemetry["termination"] == "queue-empty"
        assert result.generated.search_summary["probes_completed"] <= 1
        suite = api.build_replay_suite(contract, baseline.stimuli)
        # Required baseline includes its continuous workload, completed before
        # Search and reused at final scoring just like the isolated sequences.
        assert result.evaluation.execution_cost["reuse"]["completed_runs"] == len(suite.stimuli)
        assert tuple(run.stimulus for run in result.evaluation.results) == suite.stimuli
