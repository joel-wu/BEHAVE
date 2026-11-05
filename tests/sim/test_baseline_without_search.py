"""Disabling search must not require a dummy search capability to score."""

import pytest

from behave.sim import api, evaluation
from behave.sim.generation import generate_independently

from .test_api_path import contract
from .test_command_handler import _request
from .test_generation_scheduler import _spec, _stimulus


@pytest.mark.parametrize("options", [
    {"search_policy": api.SearchPolicy(time_budget_s=0)},
    {"search_policy": api.SearchPolicy(max_probes=0)},
    {"search_policy": api.SearchPolicy(time_budget_s=None, max_probes=0, debug=True)},
    {"probe_budget": 0},
])
def test_disabled_search_preserves_baseline_without_a_provider(tmp_path, monkeypatch, options):
    calls = []
    actual = evaluation.run

    def replay(*args, **kwargs):
        calls.append("replay")
        return actual(*args, **kwargs)

    monkeypatch.setattr(evaluation, "run", replay)
    request = _request(tmp_path, providers=(), run_analysis=False,
                       **dict({"probe_budget": None}, **options))
    result = api.handle_command(request)
    assert result.decision.authoritative and result.decision.passed, result.as_dict()
    assert calls == ["replay"]  # Finalization reuses the completed baseline.
    assert result.generated.searches == ()
    assert result.generated.stimuli == request.baseline.stimuli
    assert result.generated.provenance == {}
    assert result.generated.baseline_policy == request.baseline.policy.identity
    assert result.generated.search_summary == {
        "version": "search-budget-receipt/1", "policy": None,
        "elapsed_s": 0.0, "stop_reason": "search-disabled",
        "probes_started": 0, "probes_completed": 0, "probes_cancelled": 0,
        "directions": [],
    }


@pytest.mark.parametrize("options", [
    {}, {"search_policy": api.SearchPolicy(time_budget_s=1)},
    {"search_policy": api.SearchPolicy(time_budget_s=None, max_probes=1)},
    {"probe_budget": 1},
])
def test_enabled_search_requires_providers_before_execution(tmp_path, options):
    options = dict({"probe_budget": None}, **options)
    with pytest.raises(api.Refused, match="generation-providers-empty"):
        _request(tmp_path, providers=(), run_analysis=False, **options)
    assert not (tmp_path / "invocation").exists()
    seed = _stimulus([0])
    with pytest.raises(api.Refused, match="generation-providers-empty"):
        generate_independently((seed,), (), contract=contract(),
                               generation_spec=_spec(seed), **options)


def test_disabled_search_still_validates_baseline():
    seed = _stimulus([0])
    with pytest.raises(api.Refused, match="generation-baseline-duplicate"):
        generate_independently((seed, seed), (), contract=contract(),
                               generation_spec=_spec(seed), probe_budget=0)


@pytest.mark.parametrize("options", [
    {"search_policy": api.SearchPolicy(time_budget_s=0)},
    {"search_policy": api.SearchPolicy(max_probes=0)},
    {"probe_budget": 0},
])
@pytest.mark.parametrize("analysis", [False, True])
@pytest.mark.parametrize("capture", [False, True])
def test_query_capture_is_only_retained_for_a_query_consumer(tmp_path, monkeypatch, options, analysis, capture):
    seen = []
    actual = evaluation.ReplayExecution.evaluate

    def evaluate(execution, *args, **kwargs):
        seen.append(execution.capture_query_boundaries)
        return actual(execution, *args, **kwargs)

    monkeypatch.setattr(evaluation.ReplayExecution, "evaluate", evaluate)
    request = _request(tmp_path, run_analysis=analysis, capture_analysis=capture,
                       **dict({"probe_budget": None}, **options))
    result = api.handle_command(request)
    assert result.decision.authoritative and result.decision.passed
    assert seen and all(value == ('history' if analysis or capture else False) for value in seen)
    assert request.as_dict()['analysis']['capture_history'] is (analysis or capture)
    if not analysis:
        assert result.analysis is None


@pytest.mark.parametrize("analysis", [False, True])
@pytest.mark.parametrize("capture", [False, True])
def test_completed_search_releases_only_query_capture_not_replay(tmp_path, monkeypatch, analysis, capture):
    from behave.sim import command

    flags = []
    generate = command.generation_service.generate_stimuli
    finalize = command.evaluation_service.evaluate_generated

    def generation(*args, **kwargs):
        flags.append(("search", kwargs["_execution"].capture_query_boundaries))
        assert kwargs['_execution'].query_boundary_phases == (None if analysis or capture else frozenset({'isolated'}))
        return generate(*args, **kwargs)

    def concrete(*args, **kwargs):
        flags.append(("final", kwargs["_execution"].capture_query_boundaries))
        return finalize(*args, **kwargs)

    monkeypatch.setattr(command.generation_service, "generate_stimuli", generation)
    monkeypatch.setattr(command.evaluation_service, "evaluate_generated", concrete)
    result = api.handle_command(_request(tmp_path, probe_budget=1, run_analysis=analysis,
                                         capture_analysis=capture))
    assert result.decision.authoritative and result.decision.passed, result.as_dict()
    assert flags == [("search", 'history' if analysis or capture else True),
                     ("final", 'history' if analysis or capture else False)]


@pytest.mark.parametrize('capture', [None, 1, 'history'])
def test_history_capture_is_an_explicit_boolean(tmp_path, capture):
    with pytest.raises(TypeError, match='capture_analysis'):
        _request(tmp_path, run_analysis=False, capture_analysis=capture)


def test_disabled_search_does_not_bypass_required_observation(tmp_path, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise api.Refused(api.Refusal("test-observation-unavailable", "cannot observe"))

    monkeypatch.setattr(evaluation, "run", unavailable)
    result = api.handle_command(_request(
        tmp_path, providers=(), probe_budget=0, run_analysis=False))
    assert not result.decision.authoritative, result.as_dict()
    assert result.decision.passed is None


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ["behavior", pytest.param("rtl", marks=pytest.mark.eda)])
@pytest.mark.parametrize("wrong", [False, True])
def test_public_score_without_search_providers(tmp_path, kind, wrong):
    from .test_baseline_public_sequence import _contract
    from .test_search_budget_production import RTL, SOURCE

    task_contract = _contract()
    spec = api.GenerationSpec(8, 2, 2, 2)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": SOURCE.encode()}, config={"class": "M"})
    source = SOURCE if kind == "behavior" else RTL
    if wrong:
        source = source.replace("a + 1", "a + 2").replace("a + 8'd1", "a + 8'd2")
    path = tmp_path / ("candidate.py" if kind == "behavior" else "candidate.sv")
    path.write_text(source)
    if kind == "behavior":
        task = api.admit(task_contract, api.realization_of_behavior_source(
            source, task_contract, file=path.name, model="M"), reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(
            path, file=path.name, model="M"), spec, bounded_analysis=False)
    else:
        task = api.admit(task_contract, api.realization_of_rtl_source(
            source, top="M", contract=task_contract), reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"), spec)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        search_policy=api.SearchPolicy(time_budget_s=0)))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed is not wrong
    assert result.evaluation.complete or (wrong and result.evaluation.failure_witness is not None)
    assert result.generated.searches == ()
    assert result.generated.stimuli == result.generated.baseline
    assert result.generated.search_summary["stop_reason"] == "search-disabled"
    assert not (tmp_path / "guidance").exists()
