"""Timed search ends normally; independent public scoring still runs to completion."""

import json

import pytest

from behave.sim import api
from behave.sim.search_policy import active_search

from .test_baseline_public_sequence import _contract

SOURCE = """class M:
    def reset(self):
        pass
    def process(self, a):
        if a == 7:
            return {"y": (a + 1) & 255}
        return {"y": (a + 1) & 255}
"""
RTL = """module M(input wire clk, rst, in_valid, out_ready,
    input wire [7:0] a, output wire in_ready, out_valid, output wire [7:0] y);
    assign in_ready = out_ready;
    assign out_valid = in_valid;
    assign y = a + 8'd1;
endmodule
"""


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("depth", [10, 20])
def test_public_search_reaches_deep_state_without_selecting_a_goal(tmp_path, depth, record_property):
    source = f'''class M:
    def reset(self):
        self.state = 0
    def process(self, a):
        y = 0
        if a == self.state + 37 and self.state < {depth}:
            self.state += 1
        else:
            self.state = 0
        if self.state == {depth}:
            y = 1
        return {{"y": y}}
'''
    _check_stateful_search(source, depth, tmp_path, record_property)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("source", [
    pytest.param('''class M:
    def reset(self):
        self.left = 0
        self.right = 0
    def process(self, a):
        y = 0
        if a < 64:
            self.left = a & 63
        elif a < 128:
            self.right = a & 63
        elif a == 193 and self.left == 37 and self.right == 53:
            y = 1
        return {"y": y}
''', id="independent-configuration"),
    pytest.param('''class M:
    def reset(self):
        self.mem = [0] * 4
    def process(self, a):
        y = 0
        if a < 128:
            self.mem[(a >> 4) & 3] = a & 15
        elif a == 201 and self.mem[1] == 7 and self.mem[3] == 11:
            y = 1
        return {"y": y}
''', id="indexed-memory-setup"),
    pytest.param('''class M:
    def reset(self):
        self.links = [0] * 4
    def process(self, a):
        y = 0
        if a < 64:
            self.links[(a >> 4) & 3] = a & 15
        elif a < 128:
            self.links = [0] * 4
        elif a < 192:
            node = a & 3
            seen = 0
            reached = 0
            for i in range(4):
                if (seen >> node) & 1:
                    break
                seen |= 1 << node
                reached += 1
                row = self.links[node]
                if (row & 4) == 0:
                    break
                node = row & 3
            if reached >= 3:
                y = 1
        return {"y": y}
''', id="linked-state-traversal"),
])
def test_public_search_solves_state_dependencies_without_handpicked_inputs(
        tmp_path, source, record_property):
    _check_stateful_search(source, 6, tmp_path, record_property)


def _check_stateful_search(source, depth, tmp_path, record_property):
    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source.encode()}, config={"class": "M"})
    path = tmp_path / "candidate.py"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_behavior_source(
        source, contract, file=path.name, model="M"), reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(
        path, file=path.name, model="M", timeout_s=60),
        api.GenerationSpec(2 * depth + 4, depth, 2, 2), bounded_analysis=False)
    providers = handle.generation_providers(reference, workdir=tmp_path / "guidance")
    # Exercise the public worklist and prefix ladder, not a hand-picked local
    # goal/seed/prefix. One direction suffices to check stateful generation.
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference, providers=providers[:1],
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=depth),
        search_policy=api.SearchPolicy(time_budget_s=60), run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed, result.decision
    assert result.evaluation.complete
    telemetry = result.generated.searches[0].as_dict()["telemetry"]
    assert not telemetry.get('failure'), telemetry.get('failure')
    line, = (i for i, text in enumerate(source.splitlines(), 1) if text.strip() == "y = 1")
    goals = {goal for goal in telemetry["coverage"]["declared"]
             if goal.startswith(f"location:behavior.py:{line}:")}
    assert len(goals) == 1, telemetry["coverage"]["declared"]
    assert not goals.intersection(telemetry["baseline_coverage"]["hits"])
    assert goals <= set(telemetry["coverage"]["hits"]), telemetry
    assert result.generated.searches[0].generated
    record_property("search_budget", json.dumps(result.generated.search_summary))
    from behave.sim.core.catalogue import COVERAGE, SAFETY

    catalogue = providers[0].prepared_provider().catalogue_manifest()
    record_property("coverage", json.dumps({
        "classes": {cls: {
            "baseline": sum(catalogue[g].cls == cls for g in telemetry["baseline_coverage"]["hits"]),
            "after_search": sum(catalogue[g].cls == cls for g in telemetry["coverage"]["hits"]),
            "declared": sum(goal.cls == cls for goal in catalogue.values()),
        } for cls in (COVERAGE, SAFETY)},
        "generated": len(result.generated.searches[0].generated),
        "outcomes": telemetry["outcomes"],
    }))


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ["behavior", pytest.param("rtl", marks=pytest.mark.eda)])
@pytest.mark.parametrize("seconds", [0.001, 2.0, 60])
def test_real_public_command_survives_search_deadline(tmp_path, kind, seconds, record_property):
    c = _contract()
    spec = api.GenerationSpec(8, 2, 2, 2)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                                   {"behavior.py": SOURCE.encode()}, config={"class": "M"})
    if kind == "behavior":
        path = tmp_path / "candidate.py"
        path.write_text(SOURCE)
        realization = api.realization_of_behavior_source(SOURCE, c, file=path.name, model="M")
        task = api.admit(c, realization, reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(
            path, file=path.name, model="M", timeout_s=60), spec, bounded_analysis=False)
    else:
        path = tmp_path / "candidate.sv"
        path.write_text(RTL)
        realization = api.realization_of_rtl_source(RTL, "budget-test", top="M", contract=c)
        task = api.admit(c, realization, reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(
            path, "M", tmp_path / "rtl", timeout_s=120), spec)
    providers = handle.generation_providers(reference, workdir=tmp_path / "guidance")
    assert all(p._bound_image is None for p in providers)  # No eager layout/solver job.
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference, providers=providers,
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        search_policy=api.SearchPolicy(time_budget_s=seconds,
                                      max_probes=None if seconds == 2.0 else 1)))
    assert active_search() is None
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed, result.decision
    assert result.evaluation.complete
    suite = api.build_replay_suite(c, result.generated.stimuli, baseline=result.generated.baseline)
    assert tuple(row.stimulus for row in result.evaluation.results) == suite.stimuli
    reports = [s.as_dict()["telemetry"]["search_budget"] for s in result.generated.searches]
    record_property("search_budget", json.dumps(result.generated.search_summary))
    assert len(reports) == 2
    if seconds < 1:
        assert result.generated.stimuli == result.generated.baseline
        assert all(r["stop_reason"] == "time-budget-exhausted" for r in reports)
    elif seconds == 60:
        # This is a shared ceiling, not a requirement to query a solver after
        # seed replay/cheap proofs have already emptied both search queues.
        probes = sum(r["probes_started"] for r in reports)
        assert probes <= 1, reports
        if probes == 0:
            assert all(r["stop_reason"] in ("queue-empty", "goals-covered") for r in reports), reports
    assert all(r["total_elapsed_s"] < seconds + 15 for r in reports), reports


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ["behavior", pytest.param("rtl", marks=pytest.mark.eda)])
@pytest.mark.parametrize("debug", [False, True])
def test_real_public_baseline_survives_unavailable_search(tmp_path, monkeypatch, kind, debug):
    from behave.sim.engines.behavior.generation import BehaviorIsolatedGenerationProvider
    from behave.sim.engines.rtl.generation import RTLGenerationProvider

    c = _contract()
    spec = api.GenerationSpec(8, 2, 2, 2)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": SOURCE.encode()}, config={"class": "M"})
    path = tmp_path / ("candidate.py" if kind == "behavior" else "candidate.sv")
    path.write_text(SOURCE if kind == "behavior" else RTL)
    if kind == "behavior":
        task = api.admit(c, api.realization_of_behavior_source(SOURCE, c, file=path.name, model="M"),
                         reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=path.name, model="M"),
                                      spec, bounded_analysis=False)
    else:
        task = api.admit(c, api.realization_of_rtl_source(RTL, top="M", contract=c), reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"), spec)

    def unavailable(*_args, **_kwargs):
        raise api.Refused(api.Refusal("test-layout-unavailable", "lowering is unavailable"))

    monkeypatch.setattr(api, "prepare_behavior_generation", unavailable)
    monkeypatch.setattr(BehaviorIsolatedGenerationProvider, "from_behavior_artifact", unavailable)
    monkeypatch.setattr(RTLGenerationProvider, "from_artifact", unavailable)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance"),
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        search_policy=api.SearchPolicy(debug=debug), run_analysis=False))
    if debug:
        assert result.status is api.CommandStatus.REFUSED, result.as_dict()
        assert "test-layout-unavailable" in str(result.failure.as_dict())
        assert result.evaluation is None
    else:
        assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
        assert result.decision.authoritative and result.decision.passed
        assert result.generated.stimuli == result.generated.baseline
        # The declared source survives; unavailable lowering is now discovered
        # in the first needed Select, after baseline feedback, not at declaration.
        # Runtime factory refusal is scoped to the recorded query domain.
        # Other domains/directions still drain their worklists normally; the
        # failure evidence is retained separately, not promoted to UNSAT or
        # erased by a blanket direction-level abort.
        assert result.generated.search_summary["stop_reason"] == "queue-empty"
        assert result.generated.search_summary["probes_completed"] == 0
        reference_search = result.generated.searches[0]
        assert reference_search.telemetry["baseline_coverage"]["remaining"]
        assert "test-layout-unavailable" in dict(reference_search.telemetry["refusals"])
        assert reference_search.telemetry["provider_refusal"] is None
