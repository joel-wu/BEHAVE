"""Public native baseline and local-query preparation have distinct lifetimes."""

import json

import pytest

from behave.sim import api
from behave.sim.engines.behavior.generation import BehaviorIsolatedGenerationProvider

from .test_baseline_public_sequence import REFERENCE, _contract

# Each field read owes a source-declared AttributeError goal that construction
# discharges and no run covers. Without one, a native baseline can cover all goals.
FIELDLESS = REFERENCE.replace("        self.n = 0\n", "        pass\n").replace(
    "        self.n += 1\n", "")


@pytest.mark.cosim
@pytest.mark.solver
def test_plain_runtime_search_binds_schedule_and_reset_state_without_caller_help(tmp_path):
    from .test_sampled_protocol import _contract as sampled_contract

    contract = sampled_contract()
    source = '''module M(input clk, rst, input [7:0] in_a, output reg [7:0] out_y);
      always @(posedge clk) if(rst) out_y<=0; else out_y<=in_a;
    endmodule'''
    behavior = b'''class M:
    def __init__(self):
        self.reset()
    def reset(self):
        self.previous = 0
    def process(self, in_a):
        y = self.previous
        self.previous = in_a
        return {"out_y": y}
'''
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": behavior}, config={"class": "M"})
    path = tmp_path / "candidate.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    # No analysis horizon or GlobalSchedule is supplied. Only actual native
    # replay may supply the local product's actions and reset-state carrier.
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=4),
        providers=handle.generation_providers(reference, workdir=tmp_path / "search")[1:],
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True),
        run_analysis=False))
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    assert result.evaluation.complete and result.analysis is None
    search, = result.generated.searches
    assert not search.telemetry["refusals"], search.telemetry
    assert search.telemetry["outcomes"]["local-unsat"] > 0, search.telemetry
    assert search.telemetry["outcomes"]["unsupported"] == 0
    assert "monitor.input-completion" in search.telemetry["baseline_coverage"]["remaining"]


def _handle(tmp_path, source, *, finite_domain=True):
    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source.encode()}, config={"class": "M"})
    path = tmp_path / "candidate.py"
    path.write_text(source)
    realization = api.realization_of_behavior_source(source, contract, file="candidate.py")
    task = api.admit(contract, realization, reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="candidate.py"),
                                  api.GenerationSpec(220, 128, 2, 4) if finite_domain else None,
                                  bounded_analysis=False)
    return handle, reference


@pytest.mark.isolation
@pytest.mark.solver
def test_behavior_terminal_search_hit_uses_native_failure_feedback(tmp_path):
    contract = _contract()
    source = REFERENCE.replace('        return {"y": a}',
        '        if a == 7:\n            return {"y": a >> -1}\n        return {"y": a}')
    path = tmp_path / "candidate.py"
    path.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_behavior_source(source, contract, file="candidate.py"),
                     reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="candidate.py"), bounded_analysis=False)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", baseline=api.BaselinePolicy(case_limit=1),
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance")[1:],
        search_policy=api.SearchPolicy(time_budget_s=60, max_probes=4),
        reference=reference, run_analysis=False))
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED
    assert result.decision.authoritative and not result.decision.passed
    search, = result.generated.searches
    before = set(search.telemetry["baseline_coverage"]["hits"])
    after = set(search.telemetry["coverage"]["hits"])
    observed = set()
    for run in result.evaluation.results:
        if run.trace is not None:
            observed.update(goal for row in run.trace.records for goal, hit in row.hits if hit is True)
        else:
            assert run.run_failure.origin is api.FailureOrigin.CANDIDATE
            observed.update(run.run_failure.observed.catalogue)
    safety = {goal for goal in observed if handle.frontend_catalogue[goal].cls == "safety"}
    assert safety and not safety & before
    assert safety <= after <= observed
    assert result.analysis is None


@pytest.mark.isolation
@pytest.mark.parametrize("runtime,finite_domain", [(True, True), (True, False), (False, True)],
                         ids=("runtime", "runtime-no-analysis-domain", "fixed"))
def test_default_public_baseline_covers_both_directions_without_query_preparation(
        tmp_path, monkeypatch, runtime, finite_domain):
    handle, reference = _handle(tmp_path, FIELDLESS, finite_domain=finite_domain)
    baseline = api.BaselinePolicy()
    if not runtime:
        baseline = api.build_baseline(_contract(), api.GenerationSpec(220, 128, 2, 4),
                                      policy=baseline)

    def no_query(*args, **kwargs):
        pytest.fail("a fully covered native baseline must not build an IR layout or solver")

    monkeypatch.setattr(BehaviorIsolatedGenerationProvider, "_run_context_job", no_query)
    request = handle.command_request(
        run_directory=tmp_path / "command", baseline=baseline,
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance"),
        search_policy=api.SearchPolicy(debug=True), reference=reference, run_analysis=False)
    assert request.baseline.policy.case_limit == 50
    assert all(seed.depth("in") == 100 for seed in request.baseline.stimuli)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    assert len(result.generated.searches) == 2
    for search in result.generated.searches:
        assert search.telemetry["termination"] == "goals-covered"
        assert search.telemetry["probes"] == search.telemetry["seeds_selected"] == 0
        assert search.telemetry["baseline_coverage"]["remaining"] == ()
    assert len(result.evaluation.results) == len(request.baseline.stimuli) + int(runtime)
    if runtime:
        assert result.evaluation.results[-1].trace.accepted_count(["in"]) == (
            100 * len(request.baseline.stimuli))
    else:
        assert all(run.trace.stimulus in request.baseline.stimuli
                   for run in result.evaluation.results)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("finite_domain", (True, False))
def test_public_search_constructs_only_residual_targets_and_replays_new_hit(
        tmp_path, monkeypatch, finite_domain):
    source = FIELDLESS.replace('        return {"y": a}',
        '        if a == 7:\n            return {"y": a}\n        return {"y": a}')
    handle, reference = _handle(tmp_path, source, finite_domain=finite_domain)
    selections = []
    original = BehaviorIsolatedGenerationProvider._select_from_trace

    def select(provider, seed, trace, targets):
        selections.append((provider.catalogue_manifest().digest(),
                           provider.targetable_goals, tuple(targets)))
        return original(provider, seed, trace, targets)

    monkeypatch.setattr(BehaviorIsolatedGenerationProvider, "_select_from_trace", select)
    request = handle.command_request(
        run_directory=tmp_path / "command",
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100),
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance"),
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True),
        reference=reference, run_analysis=False)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    baseline_hits = {}
    assert len(result.generated.searches) == 2 and selections
    for search in result.generated.searches:
        baseline = search.telemetry["baseline_coverage"]
        final = search.telemetry["coverage"]
        baseline_hits[baseline["catalogue"]] = set(baseline["hits"])
        assert baseline["remaining"]
        assert search.telemetry["probes"] > 0
        assert set(final["hits"]) - set(baseline["hits"])
        assert search.telemetry["termination"] == "goals-covered"
        assert final["remaining"] == ()
    for catalogue, full, targets in selections:
        assert set(targets) < set(full)
        assert not set(targets) & baseline_hits[catalogue]
    assert all(seed.depth("in") == 100 for seed in request.baseline.stimuli)
    assert len(result.evaluation.results) > len(request.baseline.stimuli)
    assert result.analysis is None


@pytest.mark.isolation
@pytest.mark.parametrize("debug", (False, True))
@pytest.mark.parametrize("runtime", (True, False), ids=("runtime", "fixed"))
def test_public_query_preparation_failure_preserves_baseline_and_uncovered_goals(
        tmp_path, monkeypatch, debug, runtime):
    source = REFERENCE.replace('        return {"y": a}',
        '        if a > 255:\n            return {"y": 0}\n        return {"y": a}')
    handle, reference = _handle(tmp_path, source)
    baseline = api.BaselinePolicy(case_limit=1, max_queue_depth=2)
    if not runtime:
        baseline = api.build_baseline(_contract(), api.GenerationSpec(220, 128, 2, 4),
                                      policy=baseline)
    queries = []

    def unavailable(provider, job, *_args, **_kwargs):
        queries.append(job)
        raise api.Refused(api.Refusal("test-query-preparation", "injected local-query failure"))

    monkeypatch.setattr(BehaviorIsolatedGenerationProvider, "_run_context_job", unavailable)
    request = handle.command_request(
        run_directory=tmp_path / "command",
        baseline=baseline,
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance"),
        search_policy=api.SearchPolicy(debug=debug), reference=reference, run_analysis=False)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert queries and set(queries) == {"behavior_generation_layout"}
    if debug:
        assert result.status is api.CommandStatus.REFUSED, result.as_dict()
        assert result.failure.kind == "generation-debug-stop"
        assert result.decision is None or not result.decision.authoritative
    else:
        assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
        assert result.decision.authoritative and result.decision.passed
        assert len(queries) == 2  # One failed preparation per direction, not per seed.
        for search in result.generated.searches:
            assert search.telemetry["probes"] == 0
            assert search.telemetry["baseline_coverage"]["remaining"]
            assert "test-query-preparation" in dict(search.telemetry["refusals"])
            assert search.telemetry["termination"] != "goals-covered"
