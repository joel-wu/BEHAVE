"""Prepared-context exclusions remain evidence, never global goal retirement."""

import pytest

from behave.sim import api
from behave.sim.engines import probing
from behave.sim.generation import ProbeSelection, generate
from behave.sim.generation_feedback import ReplayFeedback

from .test_generation_scheduler import _canonical, _Provider, _request, _stimulus, contract


def test_excluded_in_one_seed_is_still_queried_in_another():
    seeds = _canonical(_stimulus([0]), _stimulus([1]))

    class Provider(_Provider):
        def select(self, seed, *, targets=None):
            if seed == seeds[0]:
                return ProbeSelection((), self.catalogue_manifest().digest(),
                                      constant_false_goals=("g1",))
            return super().select(seed, targets=targets)

    provider = Provider({tuple(dict(seeds[1].queues)["in"]): [_request("other")]},
                        {"other": probing.Outcome.LOCAL_UNSAT})
    result = generate(seeds, provider, contract=contract(), generation_spec=None, probe_budget=None)
    assert len(provider.probes) == 1
    assert provider.probes[0][0] == seeds[1].fingerprint
    receipt = result.as_dict()["telemetry"]
    assert receipt["selection_constant_false"] == [{
        "seed": seeds[0].fingerprint, "recorded_seed": seeds[0].fingerprint, "goals": ["g1"]}]
    assert receipt["outcomes"]["local-unsat"] == 1
    assert set(receipt["provider_targets"]) == {"g1", "g2"}


def test_selection_exclusions_must_be_declared_and_disjoint_from_queries():
    request = _request("request")
    with pytest.raises(ValueError, match="not queried"):
        ProbeSelection((request,), request.catalogue_digest, constant_false_goals=("g1",))
    for names in (("g2", "g1"), ("g1", "g1"), ("",)):
        with pytest.raises(ValueError):
            ProbeSelection((), request.catalogue_digest, constant_false_goals=names)
    selected = ProbeSelection((), request.catalogue_digest, constant_false_goals=("unknown",))
    provider = _Provider({((0,),): selected}, {})
    with pytest.raises(api.Refused, match="exclusion-outside-catalogue"):
        generate((_stimulus([0]),), provider, contract=contract(), generation_spec=None, probe_budget=None)


def test_runtime_selection_keeps_original_and_recorded_seed_scope(monkeypatch):
    from .test_runtime_guidance import setup

    provider, seeds, _replays, contexts, _scopes = setup()
    provider.select(seeds[0])
    context = contexts[0]

    def select(seed, *, targets=None):
        digest = context.catalogue_manifest().digest()
        return ProbeSelection((), digest, constant_false_goals=("g2",),
                              replay=ReplayFeedback(seed.fingerprint, digest, seed.fingerprint, ("g1",)))

    monkeypatch.setattr(context, "select", select)
    selected = provider.select(seeds[0])
    assert selected.constant_false_goals == ("g2",)
    assert selected.recorded_seed is not None
    assert selected.recorded_seed.replay_policy is None
    assert selected.replay.stimulus == seeds[0].fingerprint
    assert selected.as_dict()["constant_false_goals"] == ["g2"]


@pytest.mark.solver
@pytest.mark.isolation
def test_memory_worker_retains_selection_local_ir_exclusions_without_mutating_layout():
    from behave.sim.engines.behavior import generation_job as jobs
    from behave.sim.engines.behavior.generation_session import PreparedGeneration

    from .test_behavior_atomic_generation import _payload
    from .test_behavior_memory_generation import SOURCE, query, seed
    from .test_behavior_memory_generation import contract as memory_contract

    source = SOURCE.replace(b'self.ram[addr]}', b'self.ram[addr] >> 1}')
    payload = _payload(source, memory_contract())
    built = jobs._build(payload)
    targets = list(built[6])
    session = PreparedGeneration({**payload, "targets": targets})
    assert session.layout()["constant_false_goals"] == []
    selected = jobs.decode_selection_reply(session.select({**query(seed()), "targets": targets}))
    assert selected.constant_false_goals
    assert any("negativeshift" in name for name in selected.constant_false_goals)
    assert not set(selected.constant_false_goals) & set().union(*(set(r.targets) for r in selected.requests))
    assert session.layout()["constant_false_goals"] == []
    assert selected.catalogue_digest == built[5].digest()
