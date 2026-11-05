"""Optional search failures cannot erase replay evidence or invent a score."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from behave.sim import api, evaluation
from behave.sim.deferred_generation import deferred_provider
from behave.sim.generation import generate_independently
from behave.sim.generation_protocol import Outcome
from behave.sim.search_policy import active_search, search_cleanup

from .test_api_path import Doubling, contract
from .test_command_handler import _BaselineOnly, _request as command_request
from .test_generation_scheduler import _Provider, _replace, _request, _spec, _stimulus


class BrokenSearch(_Provider):
    def __init__(self, stage):
        super().__init__({((0,),): [_request("first"), _request("second", rank=1)]},
                         {"first": lambda seed: _replace(seed, 0, 1)})
        self.stage, self.closed = stage, False

    @contextmanager
    def search_scope(self, *, probe_budget):
        try:
            if self.stage == "enter":
                raise RuntimeError("broken enter")
            yield
        finally:
            self.closed = True
            if self.stage == "cleanup":
                raise RuntimeError("broken cleanup")

    def select(self, seed, *, targets=None):
        if self.stage == "select":
            raise RuntimeError("broken select")
        return super().select(seed)

    def probe(self, seed, request):
        if request.occurrence == "second" and self.stage == "probe":
            raise RuntimeError("broken probe")
        return super().probe(seed, request)


@pytest.mark.parametrize("debug", (False, True))
@pytest.mark.parametrize("stage", ("preparation", "enter", "select", "probe", "cleanup"))
def test_search_failure_keeps_pool_or_debug_stops(stage, debug):
    provider = BrokenSearch(stage)
    cleaned = []

    def prepare(**_kwargs):
        search_cleanup(cleaned.append, "prepared resources")
        raise RuntimeError("broken preparation")

    recipe = (deferred_provider(provider.name, provider.policy, {"source": "test"}, prepare)
              if stage == "preparation" else provider)
    seed = _stimulus([0])

    def run():
        return generate_independently((seed,), (recipe,), contract=contract(),
            generation_spec=_spec(seed), search_policy=api.SearchPolicy(debug=debug, max_probes=2))

    if debug:
        with pytest.raises(api.Refused) as caught:
            run()
        assert "broken " + stage in str(caught.value.refusal.as_dict())
    else:
        result = run()
        assert seed in result.stimuli
        if stage in ("probe", "cleanup"):
            assert _replace(seed, 0, 1).identity_bytes in {s.identity_bytes for s in result.stimuli}
        assert result.searches[0].telemetry["termination"] in ("provider-unavailable", "search-failed")
    assert stage == "preparation" or provider.closed
    if stage == "preparation":
        assert cleaned == ["prepared resources"]
    assert active_search() is None


@pytest.mark.parametrize("debug", (False, True))
def test_public_baseline_precedes_search_and_is_not_replayed(tmp_path, monkeypatch, debug):
    events = []
    actual = evaluation.run

    def replay(*args, **kwargs):
        events.append("replay")
        assert active_search() is None  # Required baseline is outside search's wall budget.
        return actual(*args, **kwargs)

    class Provider(_BaselineOnly):
        def select(self, seed, *, targets=None):
            events.append("select")
            assert events[0] == "replay"
            raise RuntimeError("broken search")

    monkeypatch.setattr(evaluation, "run", replay)
    request = command_request(tmp_path, provider=Provider(), probe_budget=None,
        search_policy=api.SearchPolicy(debug=debug), run_analysis=False)
    result = api.handle_command(request)
    record = json.loads(Path(result.record_path).read_text())
    assert events == ["replay", "select"]
    assert record["progress"]["baseline"]["complete"] is True
    if debug:
        assert result.status is api.CommandStatus.REFUSED
        assert result.evaluation is None
        assert result.failure.kind == "generation-debug-stop"
        assert record["progress"]["baseline_evaluation"]["passed"] is True
    else:
        assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
        assert result.evaluation.complete and result.evaluation.passed
        assert result.evaluation.execution_cost["reuse"]["completed_runs"] == 1
        assert result.generated.search_summary["stop_reason"] == "search-failed"


@pytest.mark.parametrize("debug", (False, True))
def test_typed_worker_timeout_preserves_baseline_and_debug_stop(tmp_path, debug):
    from behave.sim.engines.rtl.generation import _IsolatedRTLGeneration
    from behave.sim.job_isolation import IsolatedJobFailure

    timeout = _IsolatedRTLGeneration._unavailable("source",
        IsolatedJobFailure("timeout", "session probe exceeded 35s"))

    class Provider(_BaselineOnly):
        def select(self, seed, *, targets=None):
            raise api.Refused(timeout)

    result = api.handle_command(command_request(tmp_path, provider=Provider(), probe_budget=None,
        search_policy=api.SearchPolicy(debug=debug), run_analysis=False))
    record = json.loads(Path(result.record_path).read_text())
    assert record["progress"]["baseline"]["complete"] is True
    if debug:
        assert result.status is api.CommandStatus.REFUSED
        assert result.failure.kind == "generation-debug-stop"
        assert timeout.kind in str(result.failure.as_dict())
        assert record["progress"]["baseline_evaluation"]["passed"] is True
    else:
        assert result.decision.authoritative and result.decision.passed
        assert result.evaluation.required_complete
        assert result.generated.stimuli == result.generated.baseline
        feedback = api.command_diagnostics(result, reference_visible=True)["search.json"]
        assert feedback["status"] == "limited"
        assert timeout.kind in str(feedback)
        assert "session probe exceeded 35s" in str(feedback)


@pytest.mark.parametrize("debug", (False, True))
@pytest.mark.parametrize("diagnostic", ("cache", "cost"))
def test_public_search_diagnostic_failure_does_not_erase_baseline(
        tmp_path, diagnostic, debug):
    cache_states = []

    class Provider(_BaselineOnly):
        closed = False

        @contextmanager
        def search_scope(self, *, probe_budget):
            try:
                yield
            finally:
                self.closed = True

        def cache_telemetry(self):
            cache_states.append(self.closed)
            if self.closed and diagnostic == "cache":
                raise RuntimeError("broken cache diagnostics")
            return super().cache_telemetry()

        def execution_cost(self):
            if self.closed and diagnostic == "cost":
                raise RuntimeError("broken cost diagnostics")

    provider = Provider()
    result = api.handle_command(command_request(tmp_path, provider=provider,
        probe_budget=None, search_policy=api.SearchPolicy(debug=debug),
        run_analysis=False))
    saved = json.loads(Path(result.record_path).read_text())
    assert saved["progress"]["baseline"]["passed"] is True
    assert provider.closed and active_search() is None
    assert cache_states == [True]  # Finalize once, after the search resource scope.
    if debug:
        assert result.status is api.CommandStatus.REFUSED, result.as_dict()
        assert result.failure.kind == "generation-debug-stop"
        assert not result.decision.authoritative
        assert f"broken {diagnostic} diagnostics" in str(result.failure.as_dict())
    else:
        assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
        assert result.decision.authoritative and result.decision.passed
        assert result.generated.stimuli == result.generated.baseline
        assert result.generated.search_summary["stop_reason"] == "search-failed"
        telemetry = result.generated.searches[0].as_dict()["telemetry"]
        assert f"broken {diagnostic} diagnostics" in str(telemetry["diagnostic_failure"])


@pytest.mark.parametrize("stage", ("probe", "cleanup"))
@pytest.mark.parametrize("wrong_on_new_input", (False, True))
def test_public_search_failure_keeps_new_input_and_its_concrete_verdict(
        tmp_path, stage, wrong_on_new_input):
    # Both devices pass the zero-valued baseline. Only the new input exposes
    # gain=3, so throwing away the retained proposal would invent a pass.
    provider = BrokenSearch(stage)
    request = command_request(tmp_path, artifact=Doubling(
        gain=3 if wrong_on_new_input else 2), provider=provider,
        probe_budget=None, search_policy=api.SearchPolicy(max_probes=2),
        run_analysis=False)
    baseline = request.baseline.stimuli
    assert len(baseline) == 1
    assert dict(baseline[0].queues)["in"] == ((0,),)
    added = _replace(baseline[0], 0, 1)

    result = api.handle_command(request)

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative
    assert result.decision.passed is not wrong_on_new_input
    assert result.generated.baseline == baseline
    assert {seed.identity_bytes for seed in result.generated.stimuli} == {
        baseline[0].identity_bytes, added.identity_bytes}
    assert result.generated.search_summary["stop_reason"] == "search-failed"
    assert provider.closed and active_search() is None
    evaluated = result.evaluation
    assert any(run.stimulus == added for run in evaluated.results)
    assert bool(evaluated.mismatches) is wrong_on_new_input
    assert (evaluated.failure_witness is not None) is wrong_on_new_input
    assert evaluated.execution_cost["reuse"]["completed_runs"] >= len(baseline)
    saved = json.loads(Path(result.record_path).read_text())
    assert saved["progress"]["baseline"]["passed"] is True


def test_diagnostic_failure_preserves_the_original_probe_error_and_new_input():
    class Provider(BrokenSearch):
        def cache_telemetry(self):
            if self.closed:
                raise RuntimeError("broken final diagnostics")
            return super().cache_telemetry()

    provider = Provider("probe")
    seed = _stimulus([0])
    result = generate_independently((seed,), (provider,), contract=contract(),
        generation_spec=_spec(seed), search_policy=api.SearchPolicy(max_probes=2))
    telemetry = result.searches[0].as_dict()["telemetry"]
    assert result.searches[0].generated == (_replace(seed, 0, 1),)
    assert "broken probe" in str(telemetry["failure"])
    assert "broken final diagnostics" in str(telemetry["diagnostic_failure"])
    assert provider.closed and active_search() is None


@pytest.mark.parametrize("debug", (False, True))
def test_baseline_refusal_is_not_disguised_as_search_fallback(tmp_path, monkeypatch, debug):
    calls = []

    def cannot_observe(*_args, **_kwargs):
        calls.append("baseline")
        raise api.Refused(api.Refusal("native-events-unscheduled-trigger", "observer unavailable"))

    class Provider(_BaselineOnly):
        def select(self, _seed, *, targets=None):
            calls.append("search")
            raise AssertionError("search cannot precede baseline")

    monkeypatch.setattr(evaluation, "run", cannot_observe)
    result = api.handle_command(command_request(tmp_path, provider=Provider(), probe_budget=None,
        search_policy=api.SearchPolicy(debug=debug), run_analysis=False))
    assert calls == ["baseline"]
    assert not result.evaluation or not result.evaluation.passed


@pytest.mark.parametrize("debug", (False, True))
@pytest.mark.parametrize("deferred", (False, True))
@pytest.mark.parametrize("outcome", ("pass", "mismatch", "candidate-fault"))
def test_baseline_decision_controls_search_without_replaying_evidence(
        tmp_path, debug, deferred, outcome):
    calls = []

    class Device(Doubling):
        def settle(self, presented, controls):
            if outcome == "candidate-fault":
                raise RuntimeError("candidate failed")
            signals = super().settle(presented, controls)
            if outcome == "mismatch" and signals["out_valid"]:
                signals["out_y"] ^= 1
            return signals

    class Provider(_BaselineOnly):
        def select(self, seed, *, targets=None):
            calls.append("select")
            return super().select(seed, targets=targets)

    provider = Provider()

    def prepare(**_kwargs):
        calls.append("prepare")
        return provider

    recipe = (deferred_provider(provider.name, provider.policy,
                               {"source": "test"}, prepare)
              if deferred else provider)
    result = api.handle_command(command_request(
        tmp_path, artifact=Device(), provider=recipe, probe_budget=None,
        search_policy=api.SearchPolicy(debug=debug), run_analysis=False))

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative
    assert result.decision.passed is (outcome == "pass")
    assert result.evaluation.execution_cost["reuse"]["completed_runs"] == 1
    assert result.generated.stimuli == result.generated.baseline
    summary = result.generated.search_summary
    assert summary["probes_started"] == summary["probes_completed"] == 0
    if outcome == "pass":
        assert calls == (["prepare", "select"] if deferred else ["select"])
        assert summary["stop_reason"] != "baseline-failed"
    else:
        assert calls == []
        assert summary["stop_reason"] == "baseline-failed"
        if outcome == "mismatch":
            assert result.evaluation.mismatches
        else:
            assert result.evaluation.candidate_failures


@pytest.mark.parametrize("outcome", (Outcome.LOCAL_UNSAT, Outcome.UNKNOWN, Outcome.UNSUPPORTED))
def test_debug_distinguishes_solver_result_from_missing_capability(outcome):
    seed = _stimulus([0])
    provider = _Provider({((0,),): [_request("one")]}, {"one": outcome})

    def run(debug):
        return generate_independently((seed,), (provider,), contract=contract(),
            generation_spec=_spec(seed), search_policy=api.SearchPolicy(debug=debug, max_probes=1))

    assert run(False).stimuli == (seed,)
    if outcome is Outcome.UNSUPPORTED:
        with pytest.raises(api.Refused, match="generation-debug-stop"):
            run(True)
    else:
        assert run(True).stimuli == (seed,)
