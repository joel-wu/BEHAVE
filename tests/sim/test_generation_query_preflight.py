"""No symbolic query work is not an observed hit or a global UNSAT verdict."""

import pytest

from behave.sim import api
from behave.sim.core.record_value import thaw_record_value
from behave.sim.engines.behavior import generation as parent
from behave.sim.engines.behavior import generation_job as jobs
from behave.sim.generation import generate
from behave.sim.generation_feedback import ReplayFeedback
from tests.support.reference import load

from .test_behavior_generation_provider import SOURCE, _contract
from .test_generation_scheduler import _spec, _stimulus
from .test_runtime_guidance import contract, setup


def _with_preflight(provider, *, excluded=("g2",), observed=True):
    factory = provider._factory

    def prepare(*args):
        context = factory(*args)
        context.query_targets = lambda targets, *, seed=None: tuple(g for g in targets if g not in excluded)
        return context

    provider._factory = prepare
    if observed:
        def observe(seeds, manifest):
            return tuple(ReplayFeedback(seed.fingerprint, manifest.digest(),
                provider._executions.trace(seed).fingerprint, ("g1",)) for seed in seeds)

        provider._observe = observe


def test_native_feedback_can_finish_empty_query_work_without_source_reexecution():
    provider, seeds, replays, contexts, scopes = setup()
    _with_preflight(provider)
    result = generate(seeds, provider, contract=contract(),
                      generation_spec=_spec(_stimulus([0])), probe_budget=4)
    assert result.telemetry["termination"] == "queue-empty"
    assert result.telemetry["probes"] == 0 and not result.generated
    coverage = result.as_dict()["telemetry"]["coverage"]
    assert coverage["remaining"] == ["g2"]
    assert provider.targetable_goals == ("g1", "g2")
    assert len(replays) == 1 and len(contexts) == 1 and not scopes
    assert not contexts[0].received_targets
    row, = provider.execution_cost()["bindings"].values()
    assert row["selection"] == "constant-false-targets" and row["targets"] == ["g2"]


def test_elision_uses_complete_native_observation_not_only_remaining_goals():
    provider, seeds, replays, contexts, scopes = setup()
    _with_preflight(provider)
    with provider.search_scope(probe_budget=4):
        selected = provider.select(seeds[0], targets=("g2",))
        assert not selected.requests and selected.replay.hits == ("g1",)
        assert selected.replay.stimulus == seeds[0].fingerprint
        assert selected.recorded_seed == provider._executions.trace(seeds[0]).stimulus
        assert not scopes and not contexts[0].received_targets
    assert len(replays) == 1


@pytest.mark.parametrize("observed,excluded", [(False, ("g2",)), (True, ())])
def test_missing_native_feedback_or_live_query_keeps_source_select(observed, excluded):
    provider, seeds, _replays, contexts, scopes = setup()
    _with_preflight(provider, observed=observed, excluded=excluded)
    with provider.search_scope(probe_budget=1):
        selected = provider.select(seeds[0], targets=("g2",))
        assert selected.requests
        assert contexts[0].received_targets == [("g2",)]
        assert scopes
    assert not scopes


@pytest.mark.parametrize("wrong", ["refused", "seed", "catalogue"])
def test_query_elision_does_not_cover_missing_or_mismatched_native_evidence(wrong):
    provider, seeds, _replays, contexts, _scopes = setup()
    _with_preflight(provider)
    manifest = provider.catalogue_manifest()
    evidence = (api.Refusal("missing-native", "the source observation is missing")
                if wrong == "refused" else ReplayFeedback(
                    "1" * 64 if wrong == "seed" else seeds[0].fingerprint,
                    "2" * 64 if wrong == "catalogue" else manifest.digest(),
                    "3" * 64, ("g1",)))
    provider._observe = lambda _seeds, _manifest: (evidence,)
    with provider.search_scope(probe_budget=1):
        if wrong == "refused":
            assert provider.select(seeds[0], targets=("g2",)) == evidence
        else:
            with pytest.raises(api.Refused, match="complete native feedback"):
                provider.select(seeds[0], targets=("g2",))
    assert not contexts[0].received_targets


@pytest.mark.solver
def test_layout_exposes_only_existing_literal_false_query_filter():
    source = SOURCE.replace(b"        if a < b:", b"        if False:\n            y = a\n        if a < b:")
    reference = load({"behavior.py": source}, config={"class": "M"})
    built = jobs._build({
        "source": thaw_record_value(parent._authenticated_source_image(reference)),
        "contract": _contract().as_dict(),
        "generation_spec": api.GenerationSpec(3, 1, 1, 1, 0).as_dict(),
        "target_artifact": "target-a",
    })
    from behave.sim.behavior.goal_projection import literal_false_goals

    layout = jobs.decode_layout_reply(jobs._layout(built))
    expected = set(built[6]) & literal_false_goals(built[4].proc)
    assert expected and set(layout.constant_false_goals) == expected
    assert layout.targetable_goals == built[6]
    assert layout.catalogue == built[5]  # Declarations do not shrink.
    assert set(layout.targetable_goals) - expected  # Input-dependent goals remain.
    provider = object.__new__(parent.BehaviorIsolatedGenerationProvider)
    provider._layout, provider.targetable_goals = layout, layout.targetable_goals
    assert set(provider.query_targets(layout.targetable_goals)) == set(built[6]) - expected
    assert provider.query_targets(tuple(sorted(expected))) == ()
    restored = jobs.decode_layout_reply(layout.as_dict())
    assert restored.as_dict() == layout.as_dict()
    with pytest.raises(ValueError, match="sorted subset"):
        jobs.decode_layout_reply({**layout.as_dict(), "constant_false_goals": ["not-declared"]})


@pytest.mark.solver
def test_transaction_history_goals_are_not_elided_for_lacking_ir_marks():
    from .test_baseline_public_sequence import REFERENCE
    from .test_goal_query_context import _contract_with_observations

    reference = load({"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    built = jobs._build({
        "source": thaw_record_value(parent._authenticated_source_image(reference)),
        "contract": _contract_with_observations().as_dict(),
        "generation_spec": api.GenerationSpec(3, 1, 1, 1, 0).as_dict(),
        "target_artifact": "target-a",
    })
    layout = jobs.decode_layout_reply(jobs._layout(built))
    observations = set(layout.catalogue) - set(layout.frontend_catalogue)
    assert observations
    assert not observations.intersection(layout.constant_false_goals)
    provider = object.__new__(parent.BehaviorIsolatedGenerationProvider)
    provider._layout, provider.targetable_goals = layout, layout.targetable_goals
    assert set(provider.query_targets(tuple(observations))) == observations
