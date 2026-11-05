"""Production providers retain both directions even when guidance is unavailable."""

from __future__ import annotations

from behave.sim import api
from behave.sim.core.refusal import Refusal
from behave.sim.engines.rtl.generation import RTLGenerationProvider
from behave.sim.generation import UnavailableGenerationProvider
from behave.sim.production_generation import production_generation_providers
from behave.sim.stimulus_set import GenerationSpec

from .test_api_path import Doubling, _ref, contract, realization, schedule


def test_unavailable_production_capabilities_remain_two_independent_searches(
        tmp_path, monkeypatch):
    task_contract = contract()
    reference = _ref()
    task = api.admit(task_contract, realization(), reference=reference)
    generation = GenerationSpec(
        horizon=5, queue_bound=2, reset_hold_cycles=3, drain=1,
        stall_every=0)

    def behavior_unavailable(*_args, **_kwargs):
        raise api.Refused(Refusal(
            "test-behavior-capability-unavailable",
            "the source frontend is unavailable in this test"))

    def rtl_unavailable(*_args, **_kwargs):
        raise RuntimeError("the RTL frontend is unavailable in this test")

    monkeypatch.setattr(api, "prepare_behavior_generation", behavior_unavailable)
    monkeypatch.setattr(
        RTLGenerationProvider, "from_artifact", classmethod(rtl_unavailable))
    capabilities = production_generation_providers(
        task, Doubling(), reference, generation, api.CatalogueManifest(()),
        workdir=tmp_path / "generation")

    generated = api.generate_stimuli(
        task, (schedule([2], drain=1),), capabilities.providers,
        frontend_catalogue=api.CatalogueManifest(()),
        generation_spec=generation, probe_budget=1)

    assert generated.stimuli == generated.baseline
    assert capabilities.candidate_exact_context() is None
    assert len(generated.searches) == 2
    refusals = {
        search.provider: dict(search.telemetry["refusals"])
        for search in generated.searches
    }
    assert any("test-behavior-capability-unavailable" in row
               for row in refusals.values())
    assert any("rtl-generation-provider-unavailable" in row
               for row in refusals.values())


def test_score_only_generation_can_disable_unused_local_guidance(
        tmp_path, monkeypatch):
    task_contract = contract()
    reference = _ref()
    task = api.admit(task_contract, realization(), reference=reference)
    generation = GenerationSpec(
        horizon=5, queue_bound=2, reset_hold_cycles=3, drain=1,
        stall_every=0)

    def must_not_construct(*_args, **_kwargs):
        raise AssertionError("disabled local guidance must not construct a solver")

    monkeypatch.setattr(api, "prepare_behavior_generation", must_not_construct)
    monkeypatch.setattr(
        RTLGenerationProvider, "from_artifact", classmethod(must_not_construct))
    capabilities = production_generation_providers(
        task, Doubling(), reference, generation, api.CatalogueManifest(()),
        workdir=tmp_path / "generation", enable_local_guidance=False)

    assert len(capabilities.providers) == 2
    assert all(type(provider) is UnavailableGenerationProvider
               for provider in capabilities.providers)
    generated = api.generate_stimuli(
        task, (schedule([2], drain=1),), capabilities.providers,
        frontend_catalogue=api.CatalogueManifest(()),
        generation_spec=generation, probe_budget=0)
    assert generated.stimuli == generated.baseline
    assert len(generated.searches) == 2
    assert all(not search.generated for search in generated.searches)
