"""One in-process declaration; independent checks at actual wire boundaries."""

import copy
import pickle
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from behave.sim.core.failure import FailureRaised, RunFailure
from behave.sim.core.catalogue import CatalogueManifest, Goal, OccurrenceKey
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.rtl import catalogue, observation_binding
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.exact_job_instance import _decode_context

from .test_rtl_execution_policy import _contract


@pytest.fixture
def declared(tmp_path, monkeypatch):
    source = tmp_path / "M.sv"
    source.write_text("module M(input clk, a, output reg q);\n"
                      "always @(posedge clk) if(a) q<=1; else q<=0;\nendmodule\n")
    artifact = RTLArtifact(source, "M", tmp_path / "run")
    goals = tuple(Goal(f"rtl-source:fixture:{arm}", "coverage", family="control-choice",
                       provider="rtl-structural", event=arm) for arm in ("true", "false"))
    manifest = CatalogueManifest(goals, occurrences=tuple(
        (OccurrenceKey("rtl-structural", ("design.sv", 2), goal.event), goal.id)
        for goal in goals))
    calls = []

    def read(*_args, **_kwargs):
        calls.append(True)
        return manifest, SimpleNamespace(as_dict=lambda: {"test": "elaborated"})

    monkeypatch.setattr(catalogue, "source_catalogue_with_execution_of", read)
    return SimpleNamespace(artifact=artifact, manifest=manifest, calls=calls, source=source)


def test_consumers_reuse_the_original_source_manifest_without_circuit_sites(declared, monkeypatch):
    artifact = declared.artifact
    manifest = artifact.frontend_catalogue()
    assert manifest is declared.manifest

    def no_rebuild(*_args, **_kwargs):
        pytest.fail("an in-process consumer rebuilt the admitted rows or goals")

    monkeypatch.setattr(catalogue, "sites_from_json", no_rebuild)
    monkeypatch.setattr(catalogue, "_goal_catalogue_from_sites", no_rebuild)
    monkeypatch.setattr(catalogue, "goal_id_of", no_rebuild)
    for _ in range(3):
        assert artifact.frontend_catalogue() is manifest

    failure = RunFailure.infrastructure("test-native-prepare", Refusal("test-stop", "stop before tools"))

    def prepare(_source, **kwargs):
        assert kwargs["catalogue"] is manifest
        assert "sites" not in kwargs
        return failure

    monkeypatch.setattr(observation_binding, "prepare_observation", prepare)
    with pytest.raises(FailureRaised) as caught:
        artifact.bind_observation(_contract())
    assert caught.value.failure is failure
    assert declared.calls == [True]


def test_shared_declaration_is_published_once_under_parallel_preparation(declared):
    ready = Barrier(4)

    def prepare(_):
        ready.wait(timeout=5)
        return declared.artifact.frontend_catalogue()

    with ThreadPoolExecutor(max_workers=4) as pool:
        manifests = list(pool.map(prepare, range(4)))
    assert all(item is manifests[0] for item in manifests)
    assert declared.calls == [True]
    assert len(declared.artifact.tool_lineage()) == 1


def test_source_manifest_wire_image_is_detached_and_worker_checks_observation(declared):
    from behave.sim.rtl.exact_job_context import RTLExactJobContext
    from behave.sim.stimulus_set import GenerationSpec

    from .test_api_path import contract

    artifact = declared.artifact
    manifest = artifact.frontend_catalogue()
    task_contract = contract(answers=1)
    artifact._observation = SimpleNamespace(binding=observation_binding.ObservationBinding(
        artifact.identity, task_contract.digest, manifest.digest(), "a" * 64, "b" * 64))
    context = RTLExactJobContext(artifact, task_contract, GenerationSpec(5, 1, 3, 1),
        manifest, manifest, workdir=declared.source.parent / "exact")
    original = context._payload()
    assert "sites" not in original
    assert _decode_context(original, ())[1] == manifest
    changed = copy.deepcopy(original)
    changed["observation"]["catalogue"] = "c" * 64
    with pytest.raises(ValueError, match="different"):
        _decode_context(changed, ())
    assert context._payload() == original
    with pytest.raises(Refused, match="rtl-exact-observation-unbound"):
        _decode_context(original | {"observation": None}, ())
    interface_only = observation_binding.ObservationBinding(
        artifact.identity, task_contract.digest, manifest.digest())
    with pytest.raises(ValueError, match="nonempty structural catalogue requires native"):
        _decode_context(original | {"observation": interface_only.as_dict()}, ())
    with pytest.raises(TypeError, match="exact payload fields"):
        _decode_context(original | {"sites": {}}, ())
    assert artifact.frontend_catalogue() is manifest


@pytest.mark.parametrize("consumer", ["frontend_catalogue", "bind_observation"])
def test_source_change_cannot_reuse_or_replace_the_denominator(declared, consumer):
    declared.artifact.frontend_catalogue()
    declared.source.write_text("module M; endmodule\n")
    with pytest.raises(Refused, match="rtl-catalogue-source-changed"):
        if consumer == "frontend_catalogue":
            declared.artifact.frontend_catalogue()
        else:
            declared.artifact.bind_observation(_contract())
    assert declared.calls == [True]


def test_a_failed_elaboration_does_not_publish_a_partial_declaration(declared, monkeypatch):
    original = catalogue.source_catalogue_with_execution_of

    def changed_during_read(*args, **kwargs):
        result = original(*args, **kwargs)
        declared.source.write_text("module M; endmodule\n")
        return result

    monkeypatch.setattr(catalogue, "source_catalogue_with_execution_of", changed_during_read)
    with pytest.raises(Refused, match="source-changed-during-elaboration"):
        declared.artifact.frontend_catalogue()
    assert declared.artifact._frontend_catalogue is None
    assert declared.artifact._frontend_catalogue_identity is None


def test_preparation_publishes_only_after_source_declaration_is_complete(declared, monkeypatch):
    def incomplete(*_args, **_kwargs):
        raise ValueError("test interrupted declaration")

    monkeypatch.setattr(catalogue, "source_catalogue_with_execution_of", incomplete)
    with pytest.raises(ValueError, match="interrupted declaration"):
        declared.artifact.frontend_catalogue()
    assert declared.artifact._frontend_catalogue is None
    assert declared.artifact._frontend_catalogue_identity is None


def test_retained_sites_are_immutable_including_nested_inputs():
    activation = [["\\outer", "default"]]
    labels = ["1'1", "default"]
    frames = [{"labels": labels}]
    site = catalogue.Site("M", "$p", "\\a", "design.sv:2.1-2.56", "1'1", 1,
                          activation, labels, activation_frames=frames)
    original = site.as_json()
    activation[0][1] = "1'1"
    labels.reverse()
    frames.clear()
    assert site.as_json() == original
    with pytest.raises(AttributeError):
        site.outcome = "default"
    for other in (copy.deepcopy(site), pickle.loads(pickle.dumps(site)), catalogue.Site.from_json(original)):
        assert other.as_json() == original
        assert (other.target, other.goal) == (site.target, site.goal)
        with pytest.raises(AttributeError):
            other.outcome = "default"
