"""T3-16 public RTL exact construction without internal frontend types."""

from __future__ import annotations

import pytest

from behave.sim import api

from .test_api_path import _ref, contract, realization, schedule
from .test_command_handler import _BaselineOnly


class _ObserverStub:
    observation_binding = None

    def bind_observation(self, task_contract):
        self.observer_contract = task_contract


def test_public_records_prepare_one_exact_handle_and_command(tmp_path, monkeypatch):
    stimulus = schedule([3], drain=2)
    task_contract = contract()
    offered = realization()
    admitted = api.admit(
        task_contract,
        api.Realization(
            "rtl", offered.ports.values(),
            channel_ports=dict(offered.channel_ports),
            identity=offered.identity),
        reference=_ref())
    generation = api.GenerationSpec(
        horizon=len(stimulus.slots), queue_bound=1,
        reset_hold_cycles=task_contract.clocks[0].reset_hold_cycles,
        drain=2)
    rtl = tmp_path / "candidate.sv"
    rtl.write_text("module candidate; endmodule\n")
    config = api.RTLConfig(
        rtl, "candidate", tmp_path / "work", run_policy={"horizon": 6})

    class FakeArtifact(_ObserverStub):
        kind = "rtl"

        def __init__(self, *_args, **_kwargs):
            self.identity = admitted.realization.identity

        @staticmethod
        def frontend_catalogue(*, expected_identity):
            assert expected_identity == admitted.realization.identity
            return api.CatalogueManifest(())

    monkeypatch.setattr("behave.sim.rtl.artifact.RTLArtifact", FakeArtifact)

    binds = []

    def fake_bind(*_args, **_kwargs):
        binds.append("bound")
        raise AssertionError("the exact relation was built before score persistence")

    from behave.sim.rtl import analysis_binding

    monkeypatch.setattr(analysis_binding, "bind_analysis", fake_bind)

    handle = api.prepare_rtl(admitted, config, generation)

    assert type(handle) is api.RTLHandle
    assert handle.artifact_identity == admitted.realization.identity
    assert handle.frontend_catalogue == api.CatalogueManifest(())
    assert binds == []
    assert not hasattr(handle, "artifact")
    request = handle.command_request(
        run_directory=tmp_path / "invocation",
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=(_BaselineOnly(),), probe_budget=0,
        reference=_ref())
    assert type(request) is api.CommandRequest
    assert request.task is admitted
    assert request.frontend_catalogue is handle.frontend_catalogue
    assert request.as_dict()["analysis"]["exact"] is None
    policy = api.evaluation_policy(admitted, handle.frontend_catalogue)
    request_image = request.as_dict()["analysis"]["request"]
    assert {key: request_image[key] for key in (
        "admission", "task_contract", "artifact", "generation_spec",
        "frontend_catalogue", "shared_exact_context", "clock_schedule",
    )} == {
        "admission": admitted.handoff_identity,
        "task_contract": admitted.contract.digest,
        "artifact": admitted.realization.identity,
        "generation_spec": generation.as_dict(),
        "frontend_catalogue": handle.frontend_catalogue.digest(),
        "shared_exact_context": False,
        "clock_schedule": None,
    }
    assert request_image["goal_policy"] == policy.digest()
    capability = request_image["goal_capability_use"]
    assert capability["capability"] == api.EXACT_BOUNDED_HIT
    assert capability["implementation"] == "rtl-task-bounded-hit/3"
    assert capability["policy_digest"] == policy.digest()
    assert capability["source_catalogue_digest"] == policy.manifest.digest()
    assert capability["target_catalogue_digest"] == policy.manifest.digest()
    assert request_image["goal_capability_refusal"] is None
    assert binds == []
    assert not hasattr(request, "artifact")
    assert not hasattr(request, "exact")
    assert not hasattr(request, "realization")

    # A prepared handle carries the same policy through repeated commands and
    # explicit-pool replay. Neither operation is another registration boundary.
    def no_rebuild(*_args, **_kwargs):
        raise AssertionError("a prepared goal policy was rebuilt")

    monkeypatch.setattr("behave.sim.goal_policy.evaluation_goal_policy", no_rebuild)
    monkeypatch.setattr("behave.sim.command.evaluation_goal_policy", no_rebuild)
    providers = handle.generation_providers(
        _ref(), workdir=tmp_path / "search", enable_local_guidance=False)
    assert providers[1].catalogue_manifest() is request.catalogue
    repeated = handle.command_request(
        run_directory=tmp_path / "invocation-again",
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=providers, probe_budget=0, reference=_ref())
    assert repeated.goal_policy is request.goal_policy
    assert repeated.catalogue is request.catalogue

    captured = []
    def record_catalogue(*_args, catalogue, _execution, **_kwargs):
        assert catalogue is request.catalogue
        captured.append(_execution.capture_query_boundaries)
        return "replayed"

    monkeypatch.setattr("behave.sim.evaluation.evaluate", record_catalogue)
    monkeypatch.setattr("behave.sim.evaluation_policy.verdict", lambda value: value)
    assert handle.replay((stimulus,), reference=_ref()) == "replayed"
    assert handle.replay((stimulus,), reference=_ref(), capture_analysis=True) == "replayed"
    assert captured == [True, 'history']
    with pytest.raises(TypeError, match='capture_analysis'):
        handle.replay((stimulus,), reference=_ref(), capture_analysis=1)


def test_public_exact_config_and_handle_are_closed(tmp_path):
    config = api.RTLConfig(
        tmp_path / "candidate.sv", "candidate", tmp_path / "work")
    with pytest.raises(AttributeError):
        config.top_module = "changed"
    with pytest.raises(TypeError, match="prepare_rtl"):
        api.RTLHandle(None, None, None, None, None)


@pytest.mark.parametrize("malformed", [False, True])
def test_public_rtl_frontend_defect_is_a_typed_no_result(tmp_path, monkeypatch, malformed):
    task_contract = contract()
    offered = realization()
    admitted = api.admit(
        task_contract,
        api.Realization(
            "rtl", offered.ports.values(),
            channel_ports=dict(offered.channel_ports),
            identity=offered.identity),
        reference=_ref())
    generation = api.GenerationSpec(
        horizon=6, queue_bound=1,
        reset_hold_cycles=task_contract.clocks[0].reset_hold_cycles,
        drain=2)
    rtl = tmp_path / "candidate.sv"
    rtl.write_text("module candidate; endmodule\n")

    class BrokenFrontendArtifact:
        kind = "rtl"

        def __init__(self, *_args, **_kwargs):
            self.identity = admitted.realization.identity

        @staticmethod
        def frontend_catalogue(*, expected_identity):
            assert expected_identity == admitted.realization.identity
            if malformed:
                return {"entries": []}
            raise RuntimeError("the compiler rejected this source")

    monkeypatch.setattr(
        "behave.sim.rtl.artifact.RTLArtifact", BrokenFrontendArtifact)

    with pytest.raises(api.FailureRaised) as caught:
        api.prepare_rtl(
            admitted,
            api.RTLConfig(rtl, "candidate", tmp_path / "work"),
            generation)

    assert caught.value.failure.origin is api.FailureOrigin.VERIFIER
    assert caught.value.failure.disposition is api.FailureDisposition.NO_RESULT
    assert caught.value.failure.reason.kind == (
        "rtl-frontend-catalogue-invalid" if malformed else "rtl-frontend-catalogue-unavailable")
    if not malformed:
        assert "RuntimeError: the compiler rejected this source" in \
            caught.value.failure.reason.detail["error"]


def test_public_exact_config_detaches_its_finite_run_policy(tmp_path):
    policy = {"horizon": 6, "tags": ["a", "b"]}
    config = api.RTLConfig(
        tmp_path / "candidate.sv", "candidate", tmp_path / "work",
        run_policy=policy)
    policy["horizon"] = 99
    policy["tags"].append("forged")

    assert config.as_dict()["run_policy"] == {
        "horizon": 6, "tags": ["a", "b"]}
    with pytest.raises(TypeError):
        config.run_policy["horizon"] = 7
    with pytest.raises(TypeError, match="finite record carrier"):
        api.RTLConfig(
            tmp_path / "candidate.sv", "candidate", tmp_path / "work",
            run_policy={"hook": object()})


def test_public_rtl_uses_one_generation_policy_authority(tmp_path, monkeypatch):
    task_contract = contract()
    offered = realization()
    admitted = api.admit(
        task_contract,
        api.Realization(
            "rtl", offered.ports.values(),
            channel_ports=dict(offered.channel_ports),
            identity=offered.identity),
        reference=_ref())
    generation = api.GenerationSpec(
        horizon=6, queue_bound=2, reset_hold_cycles=3, drain=1,
        stall_every=4)
    rtl = tmp_path / "candidate.sv"
    rtl.write_text("module candidate; endmodule\n")
    seen = []

    class FakeArtifact(_ObserverStub):
        kind = "rtl"

        def __init__(self, *_args, **kwargs):
            seen.append(kwargs["run_policy"])
            self.identity = admitted.realization.identity

        @staticmethod
        def frontend_catalogue(*, expected_identity):
            assert expected_identity == admitted.realization.identity
            return api.CatalogueManifest(())

    monkeypatch.setattr("behave.sim.rtl.artifact.RTLArtifact", FakeArtifact)
    handle = api.prepare_rtl(
        admitted,
        api.RTLConfig(
            rtl, "candidate", tmp_path / "work",
            run_policy={"adapter_version": "fixture"}),
        generation)

    assert type(handle) is api.RTLHandle
    assert seen == [{
        "adapter_version": "fixture",
        "horizon": 6,
        "queue_bound": 2,
        "reset_hold_cycles": 3,
        "drain": 1,
        "stall_every": 4,
    }]

    with pytest.raises(api.Refused) as caught:
        api.prepare_rtl(
            admitted,
            api.RTLConfig(
                rtl, "candidate", tmp_path / "other-work",
                run_policy={"reset_hold_cycles": 2}),
            generation)
    assert caught.value.refusal.kind == "rtl-public-run-policy-mismatch"


def test_rtl_post_score_request_reuses_generation_goal_policy_by_identity(
        tmp_path, monkeypatch):
    from behave.sim.core.goal_provider import (
        EXACT_BOUNDED_HIT,
        RTL_EXACT_BOUNDED_POLICY,
    )
    from behave.sim.rtl.analysis import RTLAnalysisRequest

    task_contract = contract()
    offered = realization()
    admitted = api.admit(
        task_contract,
        api.Realization(
            "rtl", offered.ports.values(),
            channel_ports=dict(offered.channel_ports),
            identity=offered.identity),
        reference=_ref())
    generation = api.GenerationSpec(
        horizon=6, queue_bound=1,
        reset_hold_cycles=task_contract.clocks[0].reset_hold_cycles,
        drain=2)
    frontend = api.CatalogueManifest(())
    policy = api.evaluation_policy(admitted, frontend)
    capability = policy.bind_complete_implementation(
        EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY, policy.manifest)

    class FakeArtifact(_ObserverStub):
        identity = admitted.realization.identity

    artifact = FakeArtifact()

    class FakeContext:
        schedule = None
        memory_environment = None
        contract = task_contract
        generation_spec = generation
        frontend_catalogue = frontend
        complete_catalogue = policy.manifest
        goal_policy = policy
        goal_capability_use = capability

        def bind_goal_policy(self, got_policy, got_capability=None):
            assert got_policy is policy
            assert got_capability is capability

    context = FakeContext()
    context.artifact = artifact

    monkeypatch.setattr("behave.sim.rtl.artifact.RTLArtifact", FakeArtifact)
    monkeypatch.setattr(
        "behave.sim.rtl.exact_job_context.RTLExactJobContext", FakeContext)

    def reconstructed(*_args, **_kwargs):
        raise AssertionError("post-score request reconstructed its goal policy")

    monkeypatch.setattr(
        "behave.sim.goal_policy.evaluation_goal_policy", reconstructed)
    request = RTLAnalysisRequest(
        admitted, artifact, generation, frontend, exact_context=context)

    assert request.goal_policy is policy
    assert request.goal_capability is capability


def test_public_rtl_exact_refusal_happens_after_and_cannot_change_the_score(
        tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from behave.sim.core.driver import run_in_memory
    from behave.sim.core.stimulus import canonical_json
    from behave.sim.result_store import ResultStore
    from behave.sim.rtl.analysis import RTLAnalysisRequest

    from .test_api_path import Doubling

    task_contract = contract()
    reference = _ref()
    offered = realization()
    admitted = api.admit(
        task_contract,
        api.Realization(
            "rtl", offered.ports.values(),
            channel_ports=dict(offered.channel_ports),
            identity=offered.identity),
        reference=reference)
    generation = api.GenerationSpec(
        horizon=6, queue_bound=1,
        reset_hold_cycles=task_contract.clocks[0].reset_hold_cycles,
        drain=2)
    rtl = tmp_path / "candidate.sv"
    rtl.write_text("module candidate; endmodule\n")

    class FakeArtifact(_ObserverStub):
        kind = "rtl"

        def __init__(self, *_args, **_kwargs):
            self.identity = admitted.realization.identity

        def frontend_catalogue(self, *, expected_identity):
            assert expected_identity == admitted.realization.identity
            return api.CatalogueManifest(())

        def trace(self, contract_record, stimulus, *, expected_outputs=None):
            device = Doubling()
            device.identity = self.identity
            return run_in_memory(contract_record, stimulus, device,
                                 expected_outputs=expected_outputs)

        @staticmethod
        def tool_lineage():
            return ()

    monkeypatch.setattr("behave.sim.rtl.artifact.RTLArtifact", FakeArtifact)

    def unavailable(_request):
        assert persisted, "the exact request was bound before score persistence"
        raise api.Refused(api.Refusal(
            "test-rtl-exact-encoding-refused",
            "the test forces exact encoding to refuse"))

    persisted = []
    # A refused declared domain remains ancillary to the persisted score.
    monkeypatch.setattr(RTLAnalysisRequest, "bind", unavailable)
    real_decided = ResultStore.decided

    def observed_decision(store, authority):
        if authority.evaluation is not None:
            persisted.append(authority.evaluation.as_evidence_dict())
        return real_decided(store, authority)

    monkeypatch.setattr(ResultStore, "decided", observed_decision)

    handle = api.prepare_rtl(
        admitted,
        api.RTLConfig(rtl, "candidate", tmp_path / "work"),
        generation)
    request = handle.command_request(
        run_directory=tmp_path / "invocation",
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=(_BaselineOnly(),),
        probe_budget=0, run_analysis=True, reference=reference,
        analysis_wall_timeout_seconds=180)

    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED, dict(got.failure)
    assert got.evaluation.passed, [r.refused.as_dict() for r in got.evaluation.results if r.refused]
    assert got.analysis.verdict is api.Verdict.REFUSED
    assert got.analysis.evidence["refusal"]["kind"] == \
        "test-rtl-exact-encoding-refused"
    assert persisted == [got.evaluation.as_evidence_dict()]
    record = json.loads(Path(got.record_path).read_text())
    assert canonical_json(record["authority"]["evaluation"]) == \
        canonical_json(persisted[0])
    assert record["authority"]["passed"] is True
    assert record["report"]["analysis"] == got.analysis.as_dict()
    assert dict(got.failure) == {}
