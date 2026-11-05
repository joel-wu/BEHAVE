"""Two searches share target observations, never queues, queries or score evidence."""

from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.core.failure import RunFailure
from behave.sim.core.refusal import Refusal
from behave.sim.engines.rtl.generation import RTLGenerationProvider
from behave.sim.generation import UnavailableGenerationProvider
from behave.sim.production_generation import production_generation_providers
from behave.sim.rtl.artifact import RTLArtifact

from .test_api_path import _ref, contract, realization, schedule


def _providers(tmp_path, monkeypatch, *, unavailable=False):
    path = tmp_path / "top.sv"
    path.write_text("module top; endmodule\n")
    artifact = RTLArtifact(path, "top", tmp_path / "rtl")
    monkeypatch.setattr(
        "behave.sim.rtl.catalogue.source_catalogue_with_execution_of",
        lambda *_args, **_kwargs: (api.CatalogueManifest(()),
                                  SimpleNamespace(as_dict=lambda: {"test": "empty-source"})))
    task = api.admit(contract(), realization(identity=artifact.identity), reference=_ref())
    callbacks = {}

    def source_provider(task, spec, reference, trace_of, **kwargs):
        callbacks.update(one=trace_of, many=kwargs["traces_of"])
        return UnavailableGenerationProvider("reference-test", "test/1", Refusal("test", "no source solver needed"))

    monkeypatch.setattr(api, "prepare_behavior_generation", source_provider)
    if unavailable:

        def no_candidate(*args, **kwargs):
            raise ValueError("exact frontend unavailable")

        monkeypatch.setattr(RTLGenerationProvider, "from_artifact", no_candidate)
    capabilities = production_generation_providers(
        task,
        artifact,
        _ref(),
        api.GenerationSpec(6, 2, 3, 1),
        api.CatalogueManifest(()),
        workdir=tmp_path / "generation",
    )
    if not unavailable:
        assert type(capabilities.candidate) is RTLGenerationProvider
    return artifact, task, callbacks, capabilities


def _replay(monkeypatch, artifact):
    calls = []

    def replay(self, contract, seeds, **kwargs):
        assert self is artifact
        seeds = tuple(seeds)
        calls.append(seeds)
        return tuple(
            api.AuditedTrace(
                seed, records=[api.SlotRecord() for _ in seed.slots], artifact=self.identity, domain="test"
            )
            for seed in seeds
        )

    monkeypatch.setattr(RTLArtifact, "traces_per_process", replay)
    return calls


def test_both_guidance_directions_reuse_checked_selection_not_final_replay(tmp_path, monkeypatch):
    artifact, task, callbacks, capabilities = _providers(tmp_path, monkeypatch)
    calls = _replay(monkeypatch, artifact)
    left, right = schedule([1], drain=1), schedule([2], drain=1)
    source = callbacks["many"]((left, right))
    context = capabilities.candidate_exact_context()
    candidate = context.selection_traces((right, left, right))
    assert candidate == (source[1], source[0], source[1])
    assert callbacks["one"](left) is source[0]
    assert calls == [(left, right)]

    # Final evaluation uses the artifact, not either provider's Select service.
    final = artifact.traces_per_process(task.contract, (left, right))
    assert calls == [(left, right), (left, right)]
    assert all(a is not b for a, b in zip(source, final))


@pytest.mark.parametrize("changed", ["source", "observer"])
def test_shared_selection_revalidates_identity_before_a_hit(tmp_path, monkeypatch, changed):
    artifact, _task, callbacks, _capabilities = _providers(tmp_path, monkeypatch)
    calls = _replay(monkeypatch, artifact)
    seed = schedule([1], drain=1)
    callbacks["one"](seed)
    if changed == "source":
        artifact.rtl_path.write_text("module top; wire changed; endmodule\n")
    else:
        monkeypatch.setattr(RTLArtifact, "observation_binding", property(lambda self: "changed"))
    with pytest.raises(api.Refused, match="changed-after-context-binding"):
        callbacks["many"]((seed,))
    assert calls == [(seed,)]


def test_missing_exact_capability_keeps_reference_replay(tmp_path, monkeypatch):
    artifact, _task, callbacks, capabilities = _providers(tmp_path, monkeypatch, unavailable=True)
    calls = _replay(monkeypatch, artifact)
    seed = schedule([1], drain=1)
    assert callbacks["one"](seed).stimulus == seed
    assert calls == [(seed,)]
    assert type(capabilities.candidate) is UnavailableGenerationProvider


def test_failed_shared_replay_is_not_cached_or_silently_retried(tmp_path, monkeypatch):
    _artifact, _task, callbacks, capabilities = _providers(tmp_path, monkeypatch)
    attempts = []
    refusal = Refusal("test-infrastructure", "no trace")

    def failed(self, contract, seeds, **kwargs):
        attempts.append(tuple(seeds))
        return (refusal,)

    monkeypatch.setattr(RTLArtifact, "traces_per_process", failed)
    seed = schedule([1], drain=1)
    assert callbacks["one"](seed) is refusal
    assert len(attempts) == 1
    assert capabilities.candidate_exact_context().selection_trace(seed) is refusal
    assert len(attempts) == 2  # Explicit second consumer, not an internal retry.


@pytest.mark.parametrize("origin", ("candidate", "verifier", "infrastructure"))
def test_selection_keeps_typed_replay_failure_and_does_not_cache_it(tmp_path, monkeypatch, origin):
    _artifact, _task, callbacks, capabilities = _providers(tmp_path, monkeypatch)
    failure = getattr(RunFailure, origin)("native-replay", Refusal("test-native-failure", "original reason"))
    attempts = []

    def failed(self, contract, seeds, **kwargs):
        attempts.append(tuple(seeds))
        return (failure,)

    monkeypatch.setattr(RTLArtifact, "traces_per_process", failed)
    seed = schedule([1], drain=1)
    for selected in (callbacks["one"](seed), capabilities.candidate_exact_context().selection_trace(seed)):
        assert selected.kind == "rtl-generation-selection-failed"
        assert selected.as_dict()["detail"]["failure"] == failure.as_dict()
    assert attempts == [(seed,), (seed,)]


@pytest.mark.parametrize("size", [1, 8, 64])
def test_shared_target_work_grows_with_distinct_seeds_not_consumers(tmp_path, monkeypatch, size):
    artifact, _task, callbacks, capabilities = _providers(tmp_path, monkeypatch)
    calls = _replay(monkeypatch, artifact)
    seeds = tuple(schedule([index], drain=1) for index in range(size))
    expected = callbacks["many"](seeds)
    context = capabilities.candidate_exact_context()
    assert context.selection_traces(tuple(reversed(seeds))) == tuple(reversed(expected))
    assert callbacks["many"](seeds) == expected
    assert len(calls) == 1 and sum(map(len, calls)) == size
