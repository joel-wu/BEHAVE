"""The stable simulator command handler composes the public typed phases."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

import behave.sim.command as command_module
from behave.sim import api, witness_pool
from behave.sim import evaluation as evaluation_service
from behave.sim import generation as generation_service
from behave.sim.core.admission import _admitted_task_from_record
from behave.sim.core.catalogue import COVERAGE, Goal
from behave.sim.core.domain import TraceDomain
from behave.sim.core.goal_provider import (
    BEHAVIOR_LOCAL_GUIDANCE_POLICY,
    LOCAL_GUIDANCE,
)
from behave.sim.core.instance import BoundedInstance
from behave.sim.core.stimulus import canonical_json
from behave.sim.engines import probing
from behave.sim.generation_cache import GenerationCacheTelemetry
from behave.sim.goal_policy import evaluation_goal_policy
from tests.support.device_boundary import prepare_device_command

from .test_api_path import Doubling, _ref, contract, realization, schedule


def test_admitted_task_public_constructor_is_closed():
    with pytest.raises(TypeError, match=r"admit\(\)"):
        api.AdmittedTask(contract(), realization())


@pytest.mark.parametrize(
    ("field", "replacement", "kind"),
    (
        ("checks", {"forged": True}, "admission-handoff-identity-mismatch"),
        ("reference", "forged-reference", "admission-handoff-identity-mismatch"),
        ("realization", "forged-source", "admission-handoff-identity-mismatch"),
        ("contract", "0" * 64, "admission-handoff-contract-mismatch"),
    ),
)
def test_admitted_task_handoff_rejects_forged_complete_fields(
        field, replacement, kind):
    task_contract = contract()
    task = api.admit(task_contract, realization(), reference=_ref())
    image = deepcopy(task.as_dict())
    if field == "realization":
        image[field]["source"] = replacement
    else:
        image[field] = replacement

    with pytest.raises(api.Refused) as caught:
        _admitted_task_from_record(
            task_contract, image, expected_identity=task.handoff_identity)

    assert caught.value.refusal.kind == kind


def test_command_version_moves_with_nested_contract_and_stimulus_wire_schema(
        tmp_path):
    image = _request(tmp_path).as_dict()

    assert api.COMMAND_VERSION == "sim-command/52"
    assert image["version"] == api.COMMAND_VERSION
    assert image["task"]["contract"]["version"] == api.TASK_CONTRACT_VERSION
    assert image["task"]["admission"] == _request(
        tmp_path / "other").task.as_dict()
    assert image["goal_policy"]["digest"] == _request(
        tmp_path / "policy").goal_policy.digest()
    assert image["goal_policy"]["policy"]["catalogue_digest"] == \
        image["catalogue"]["digest"]
    assert image["baseline"]["contract"] == _request(
        tmp_path / "baseline").task.contract.digest
    assert image["baseline"]["version"] == "generation-baseline/13"
    assert image["baseline"]["stimuli"] == [
        stimulus.as_dict() for stimulus in _request(
            tmp_path / "members").baseline.stimuli]
    assert image["baseline"]["generation_spec"]["identity"] == \
        image["generation_spec"]["identity"]
    assert image["baseline"]["policy_identity"].startswith(
        "generation-baseline/13:")
    assert "realization" not in image and "contract" not in image


class _BaselineOnly:
    name = "tests/baseline-only"
    policy = "tests/baseline-only/v1"
    targetable_goals = ()

    @staticmethod
    def catalogue_manifest():
        return api.CatalogueManifest(())

    @staticmethod
    def cache_telemetry():
        return GenerationCacheTelemetry()

    def select(self, _seed, *, targets=None):
        return api.ProbeSelection((), self.catalogue_manifest().digest())

    def probe(self, _seed, _request):
        raise AssertionError("an empty selection cannot be probed")


def _request(tmp_path, *, artifact=None, provider=None, providers=None, probe_budget=0,
             search_policy=None, run_analysis=True, capture_analysis=False, secondary_pool_budget=0,
             analysis_wall_timeout_seconds=None):
    stimulus = schedule([3], drain=2)
    task_contract = contract()
    reference = _ref()
    task = api.admit(task_contract, realization(), reference=reference)
    spec = api.GenerationSpec(
        horizon=len(stimulus.slots), queue_bound=1,
        reset_hold_cycles=task_contract.clocks[0].reset_hold_cycles, drain=2)
    return prepare_device_command(
        task, artifact or Doubling(),
        run_directory=tmp_path / "invocation",
        baseline=api.build_baseline(task_contract, spec,
            policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1)),
        providers=(provider or _BaselineOnly(),) if providers is None else providers,
        generation_spec=spec,
        probe_budget=probe_budget,
        search_policy=search_policy,
        run_analysis=run_analysis,
        capture_analysis=capture_analysis,
        analysis_wall_timeout_seconds=analysis_wall_timeout_seconds,
        secondary_pool_budget=secondary_pool_budget,
        reference=reference,
    )


def test_behavior_command_does_not_add_signal_level_monitor_goals(tmp_path):
    class BehaviorArtifact:
        kind = "behavior"
        identity = "behavior-artifact"

    task_contract = contract()
    frontend = api.CatalogueManifest((Goal(
        "behavior.branch.true",
        COVERAGE,
        family="decision",
        provider="python-structural",
        event="T",
        policy="source-site/1",
    ),))
    offered = realization()
    task = api.admit(
        task_contract,
        api.Realization(
            "behavior", tuple(offered.ports.values()),
            channel_ports=dict(offered.channel_ports),
            source=offered.source, identity=BehaviorArtifact.identity))

    manifest = api.evaluation_manifest(task, frontend)

    assert manifest == frontend
    assert all(not goal.startswith("monitor.") for goal in manifest)


def test_command_refuses_a_generation_provider_without_a_source_catalogue(tmp_path):
    class LegacyProvider:
        name = "tests/legacy-optional"
        policy = "tests/legacy-optional/v1"
        targetable_goals = ()

        @staticmethod
        def select(_seed):
            return ()

        @staticmethod
        def probe(_seed, _request):
            raise AssertionError("an empty selection cannot be probed")

    with pytest.raises(
            api.Refused, match="sim-command-provider-catalogue-missing"):
        _request(tmp_path, provider=LegacyProvider())


@pytest.mark.parametrize("timed", [False, True])
def test_command_keeps_source_guidance_separate_from_scored_catalogue(tmp_path, timed):
    source_goal = Goal(
        "reference.branch:T", COVERAGE, family="decision",
        provider="python-structural", event="T", policy="reference/v1")
    source_catalogue = api.CatalogueManifest((source_goal,))
    source_policy = evaluation_goal_policy(
        contract(), source_catalogue, artifact_kind="behavior")
    capability = source_policy.bind_implementation(
        LOCAL_GUIDANCE, BEHAVIOR_LOCAL_GUIDANCE_POLICY, source_catalogue)

    class SourceProvider:
        name = "reference-behavior->candidate-device"
        policy = BEHAVIOR_LOCAL_GUIDANCE_POLICY
        targetable_goals = (source_goal.id,)

        @staticmethod
        def catalogue_manifest():
            return source_catalogue

        @staticmethod
        def cache_telemetry():
            return GenerationCacheTelemetry()

        @staticmethod
        def goal_capability_use():
            return capability

        @staticmethod
        def select(seed):
            offered = next(index for index, slot in enumerate(seed.slots)
                           if slot.offers)
            return api.ProbeSelection((api.ProbeRequest(
                (source_goal.id,), occurrence="reference:process:0",
                mutable_fields=(api.StimulusField("in", 0, 0, offered),),
                catalogue_digest=source_catalogue.digest()),),
                source_catalogue.digest())

        @staticmethod
        def probe(seed, request):
            candidate = api.Stimulus(
                queues={"in": ((4,),)}, slots=seed.slots,
                source=seed.source,
                clock_schedule=seed.clock_schedule)
            return probing.ProbeResult(
                probing.Outcome.SAT, probing.Scope.EXACT_LOCAL_HIT,
                goal=request.targets[0], candidate=candidate)

    options = ({"probe_budget": None, "search_policy": api.SearchPolicy(max_probes=1)}
               if timed else {"probe_budget": 1})
    request = _request(tmp_path, provider=SourceProvider(), **options)
    assert source_goal.id not in request.catalogue
    capability_image = request.as_dict()["providers"][0]["goal_capability_use"]
    assert type(capability_image) is dict
    assert capability_image == capability.as_dict()

    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED
    assert got.evaluation.catalogue == request.catalogue
    assert source_goal.id not in got.evaluation.catalogue
    assert len(got.generated.stimuli) == 2
    assert dict(got.generated.searches[0].telemetry)[
        "catalogue_digest"] == source_catalogue.digest()
    assert got.generated.searches[0].as_dict()["telemetry"][
        "goal_capability_use"] == capability.as_dict()


def test_command_refuses_when_search_uses_another_source_catalogue(tmp_path):
    first = Goal(
        "source.branch:T", COVERAGE, family="decision",
        provider="python-structural", event="T", policy="source/v1")
    second = Goal(
        first.id, COVERAGE, family="decision",
        provider="python-structural", event="T", policy="source/v2")
    catalogues = [api.CatalogueManifest((first,)), api.CatalogueManifest((second,))]

    class SwitchingProvider:
        name = "source-switch"
        policy = "source-switch/v1"
        targetable_goals = (first.id,)
        calls = 0

        @classmethod
        def catalogue_manifest(cls):
            # Command construction freezes the first manifest.  The scheduler's one
            # read sees the second, and the later live-capability check sees the first
            # again.  Only direct reconciliation with the search record catches this.
            index = 1 if cls.calls == 2 else 0
            cls.calls += 1
            return catalogues[index]

        @staticmethod
        def cache_telemetry():
            return GenerationCacheTelemetry()

        @staticmethod
        def select(_seed):
            return api.ProbeSelection((), catalogues[1].digest())

        @staticmethod
        def probe(_seed, _request):
            raise AssertionError("an empty selection cannot be probed")

    request = _request(tmp_path, provider=SwitchingProvider())
    got = api.handle_command(request)

    assert got.status is api.CommandStatus.REFUSED
    assert got.failure.kind == "sim-command-generation-source-mismatch"
    assert got.evaluation is None


def test_handler_runs_the_public_phases_and_persists_analysis_after_score(tmp_path):
    request = _request(tmp_path, secondary_pool_budget=10)

    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED
    assert got.admitted is request.task
    assert got.generated.task is got.admitted
    assert got.generated.catalogue is got.evaluation.catalogue
    assert got.generated.catalogue == api.evaluation_manifest(
        got.admitted, request.frontend_catalogue)
    assert got.evaluation.passed
    assert got.analysis.verdict is api.Verdict.REFUSED
    assert got.analysis.concrete == got.evaluation.fingerprint
    # This stop happens before an exact domain can be bound.  The catalogue remains
    # authoritative in the immutable concrete result; the ancillary refusal must not
    # copy it into a second, apparently bound analysis manifest.
    assert "catalogue_manifest" not in got.analysis.evidence
    assert got.evaluation.catalogue is got.generated.catalogue
    assert got.evaluation.as_dict()["catalogue_digest"] == \
        got.generated.catalogue.digest()
    assert got.authority_kind == "concrete_evaluation"

    record = json.loads(Path(got.record_path).read_text())
    assert record["progress"]["command"]["identity"] == request.identity
    assert record["progress"]["phase_reached"] == "concrete-evaluation"
    assert record["progress"]["generation"]["catalogue_digest"] == \
        request.catalogue.digest()
    assert "catalogue_manifest" not in record["progress"]["generation"]
    assert canonical_json(record["authority"]["evaluation"]) == \
        canonical_json(got.evaluation.as_evidence_dict())
    assert record["report"]["analysis"] == got.analysis.as_dict()
    assert record["authority"]["passed"] is True
    assert "passed" not in record["report"]["analysis"]
    secondary = dict(record["report"]["secondary_witness_pool"])
    assert dict(secondary["pool"])["evaluation"] == got.evaluation.fingerprint
    assert "secondary_witness_pool" not in record["authority"]["telemetry"]


def test_score_only_command_never_enters_bounded_analysis(
        tmp_path, monkeypatch):
    request = _request(tmp_path, run_analysis=False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a score-only command entered bounded analysis")

    monkeypatch.setattr(command_module, "_analysis_inputs", forbidden)
    monkeypatch.setattr(command_module, "analyse_after_decision", forbidden)
    monkeypatch.setattr(witness_pool, "secondary_pool_report", forbidden)
    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED
    assert got.evaluation.passed
    assert got.analysis is None
    assert not got.failure
    assert request.as_dict()["analysis"]["requested"] is False
    record = json.loads(Path(got.record_path).read_text())
    assert canonical_json(record["authority"]["evaluation"]) == \
        canonical_json(got.evaluation.as_evidence_dict())
    assert record["report"]["analysis_status"] == "not-requested"
    assert record["report"]["secondary_witness_pool"] == {
        "status": "not-requested", "max_candidate_checks": 0}
    assert "analysis" not in record["report"]


def test_analysis_choice_is_part_of_the_frozen_command_identity(tmp_path):
    score_only = _request(tmp_path, run_analysis=False)
    with_analysis = _request(tmp_path, run_analysis=True)

    assert score_only.identity != with_analysis.identity
    assert score_only.as_dict()["analysis"]["requested"] is False
    assert with_analysis.as_dict()["analysis"]["requested"] is True


@pytest.mark.parametrize("run_analysis", [False, True])
def test_pool_reduction_is_requested_independently_of_analysis(tmp_path, run_analysis):
    without_pool = _request(tmp_path, run_analysis=run_analysis)
    with_pool = _request(
        tmp_path, run_analysis=run_analysis, secondary_pool_budget=10)
    assert without_pool.identity != with_pool.identity
    assert with_pool.as_dict()["secondary_pool"] == {
        "requested": True, "max_candidate_checks": 10}

    got = api.handle_command(with_pool)

    record = json.loads(Path(got.record_path).read_text())
    assert record["authority"]["passed"] is True
    assert record["report"]["secondary_witness_pool"]["status"] == "available"
    assert record["report"]["secondary_witness_pool"]["max_candidate_checks"] == 10


@pytest.mark.parametrize("budget", [-1, True, 1.5, None])
def test_pool_budget_is_a_nonnegative_exact_integer(tmp_path, budget):
    with pytest.raises(ValueError, match="secondary_pool_budget"):
        _request(tmp_path, secondary_pool_budget=budget)


def test_handler_carries_one_frozen_goal_policy_into_post_score_analysis(
        tmp_path, monkeypatch):
    request = _request(tmp_path)

    def no_reconstruction(*_args, **_kwargs):
        raise AssertionError("post-score analysis reconstructed the goal policy")

    from behave.sim import goal_policy as goal_policy_module

    monkeypatch.setattr(
        goal_policy_module, "evaluation_goal_policy", no_reconstruction)
    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED
    assert got.evaluation.catalogue is request.goal_policy.manifest
    assert got.analysis.verdict is api.Verdict.REFUSED
    assert got.analysis.evidence["refusal"]["kind"] == \
        "analysis-exact-instance-missing"

    wire = json.loads(json.dumps(request.as_dict()))
    identity = wire.pop("identity")
    assert hashlib.sha256(canonical_json(wire).encode("utf-8")).hexdigest() == \
        identity
    assert wire["goal_policy"]["digest"] == request.goal_policy.digest()
    assert wire["goal_policy"]["policy"] == request.goal_policy.as_dict()


def test_handler_consumes_the_admission_handoff_without_re_admitting(
        tmp_path, monkeypatch):
    request = _request(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the simulator handler repeated admission")

    monkeypatch.setattr(api, "admit", forbidden)
    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED
    assert got.admitted is request.task
    assert got.generated.task is request.task


def test_handler_evaluates_and_decides_the_score_once(tmp_path, monkeypatch):
    request = _request(tmp_path)
    real_evaluate = evaluation_service.evaluate_generated
    real_verdict = command_module.verdict
    calls = []

    def evaluate(*args, **kwargs):
        result = real_evaluate(*args, **kwargs)
        calls.append(("evaluate", result))
        return result

    def decide(result):
        calls.append(("verdict", result))
        return real_verdict(result)

    monkeypatch.setattr(evaluation_service, "evaluate_generated", evaluate)
    monkeypatch.setattr(command_module, "verdict", decide)

    got = api.handle_command(request)

    assert [name for name, _value in calls] == ["evaluate", "verdict"]
    assert calls[0][1] is calls[1][1] is got.evaluation


def test_handler_refuses_a_generated_set_from_an_equal_but_other_admission(
        tmp_path, monkeypatch):
    request = _request(tmp_path)
    other = api.admit(
        request.task.contract, request.task.realization, reference=_ref())
    assert other is not request.task
    assert other.as_dict() == request.task.as_dict()
    generated = api.generate_stimuli(
        other, request.baseline.stimuli, (_BaselineOnly(),),
        catalogue=request.catalogue,
        generation_spec=request.generation_spec,
        probe_budget=0,
        baseline_policy=request.baseline.policy.identity)
    monkeypatch.setattr(generation_service, "generate_stimuli", lambda *_args, **_kwargs: generated)

    got = api.handle_command(request)

    assert got.status is api.CommandStatus.REFUSED
    assert got.failure.kind == "sim-command-generation-task-mismatch"
    assert got.evaluation is None


def test_handler_refuses_a_concrete_score_from_an_equal_but_other_admission(
        tmp_path, monkeypatch):
    request = _request(tmp_path)
    other = api.admit(
        request.task.contract, request.task.realization, reference=_ref())
    generated = api.generate_stimuli(
        other, request.baseline.stimuli, (_BaselineOnly(),),
        catalogue=request.catalogue,
        generation_spec=request.generation_spec,
        probe_budget=0,
        baseline_policy=request.baseline.policy.identity)
    evaluation = evaluation_service.evaluate_generated(
        generated, Doubling(), reference=_ref())
    assert evaluation.task is other
    monkeypatch.setattr(
        evaluation_service, "evaluate_generated", lambda *_args, **_kwargs: evaluation)

    got = api.handle_command(request)

    assert got.status is api.CommandStatus.REFUSED
    assert got.failure.kind == "sim-command-concrete-task-mismatch"
    assert got.generated.task is request.task
    assert got.evaluation is None


def test_public_command_constructor_refuses_task_and_capability_splices(tmp_path):
    stimulus = schedule([3], drain=2)
    task_contract = contract()
    reference = _ref()
    task = api.admit(task_contract, realization(), reference=reference)
    common = {
        "run_directory": tmp_path / "invocation",
        "baseline": api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        "providers": (_BaselineOnly(),),
        "generation_spec": api.GenerationSpec(
            horizon=len(stimulus.slots), queue_bound=1,
            reset_hold_cycles=task_contract.clocks[0].reset_hold_cycles,
            drain=2),
        "probe_budget": 0,
        "reference": reference,
    }

    class OtherArtifact(Doubling):
        identity = "other-candidate"

    with pytest.raises(api.Refused, match="sim-command-artifact-binding-mismatch"):
        prepare_device_command(task, OtherArtifact(), **common)

    class PretendsToBeRTL(Doubling):
        kind = "rtl"

    with pytest.raises(
            api.Refused, match="sim-command-public-artifact-kind-unsupported"):
        prepare_device_command(task, PretendsToBeRTL(), **common)

    with pytest.raises(api.Refused, match="sim-command-reference-binding-mismatch"):
        prepare_device_command(
            task, Doubling(),
            **{**common, "run_directory": tmp_path / "other-reference",
               "reference": _ref(gain=3)})

    with pytest.raises(TypeError, match="AdmittedTask"):
        prepare_device_command(task_contract, Doubling(), **common)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
        prepare_device_command(
            task, Doubling(), **common,
            frontend_catalogue=api.CatalogueManifest(()))


def test_existing_explicit_run_directory_is_a_closed_refusal(tmp_path):
    request = _request(tmp_path)
    first = api.handle_command(request)
    assert first.status is api.CommandStatus.EVALUATED

    second = api.handle_command(request)

    assert second.status is api.CommandStatus.REFUSED
    assert second.failure.kind == "sim-command-run-directory-exists"
    assert second.invocation == "" and second.record_path == ""
    image = second.failure.as_dict()
    with pytest.raises(TypeError):
        second.failure.detail["run_directory"] = "/forged"
    with pytest.raises(AttributeError):
        second.failure.kind = "forged"
    assert second.failure.as_dict() == image


def test_two_commands_cannot_claim_one_explicit_directory(tmp_path):
    requests = (_request(tmp_path), _request(tmp_path))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(api.handle_command, requests))

    assert sorted(result.status.value for result in results) == [
        "evaluated", "refused"]
    winner = next(result for result in results
                  if result.status is api.CommandStatus.EVALUATED)
    loser = next(result for result in results
                 if result.status is api.CommandStatus.REFUSED)
    assert loser.failure.kind == "sim-command-run-directory-exists"
    record = json.loads(Path(winner.record_path).read_text())
    assert record["run_invocation"] == winner.invocation
    assert record["progress"]["command"]["identity"] == winner.command.identity


def test_changed_provider_identity_is_refused_before_generation(tmp_path):
    provider = _BaselineOnly()
    request = _request(tmp_path, provider=provider)
    provider.policy = "tests/changed-after-request/v2"

    got = api.handle_command(request)

    assert got.status is api.CommandStatus.REFUSED
    assert got.failure.kind == "sim-command-capability-reference-changed"
    assert got.admitted is request.task and got.generated is None
    record = json.loads(Path(got.record_path).read_text())
    assert record["authority"]["kind"] == "refused"
    assert record["authority"]["refusals"][0]["kind"] == got.failure.kind


def test_provider_cannot_change_its_recorded_policy_while_generating(tmp_path):
    class ChangesPolicy(_BaselineOnly):
        def select(self, _seed, *, targets=None):
            self.policy = "tests/mutated-during-select/v2"
            return api.ProbeSelection((), self.catalogue_manifest().digest())

    request = _request(tmp_path, provider=ChangesPolicy(), probe_budget=1)

    got = api.handle_command(request)

    assert got.status is api.CommandStatus.REFUSED
    assert got.failure.kind == "sim-command-capability-reference-changed"
    assert got.generated is not None and got.evaluation is None
    assert got.failure.detail["phase"] == "generation"


def test_unexpected_phase_failure_is_not_an_artifact_failure(
        tmp_path, monkeypatch):
    request = _request(tmp_path)

    def broken(*_args, **_kwargs):
        raise RuntimeError("scheduler process broke")

    monkeypatch.setattr(generation_service, "generate_stimuli", broken)
    got = api.handle_command(request)

    assert got.status is api.CommandStatus.INFRASTRUCTURE_FAILURE
    assert got.admitted is not None
    assert got.generated is None and got.evaluation is None
    assert dict(got.failure)["phase"] == "generation"
    record = json.loads(Path(got.record_path).read_text())
    assert record["authority"]["kind"] == "partial"
    assert "passed" not in record["authority"]


def test_artifact_failure_stays_inside_the_concrete_record(tmp_path):
    class Broken(Doubling):
        def settle(self, _presented, _controls):
            raise RuntimeError("candidate failed")

    request = _request(tmp_path, artifact=Broken())

    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED
    assert not got.evaluation.infrastructure
    assert len(got.evaluation.candidate_failures) == 1
    _, failure = got.evaluation.candidate_failures[0]
    assert failure.origin is api.FailureOrigin.CANDIDATE
    assert failure.disposition is api.FailureDisposition.SCORE_ZERO
    assert got.authority_kind == "concrete_evaluation"
    assert not got.evaluation.passed
    assert got.analysis.concrete == got.evaluation.fingerprint
    assert dict(got.failure) == {}


def test_secondary_pool_failure_cannot_suppress_the_score(tmp_path, monkeypatch):
    request = _request(tmp_path, secondary_pool_budget=10)

    def broken(_evaluation, *, max_checks):
        assert max_checks == 10
        raise RuntimeError("optional reducer broke")

    monkeypatch.setattr("behave.sim.witness_pool.minimize_witnesses", broken)
    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED
    assert got.evaluation.passed and got.authority_kind == "concrete_evaluation"
    record = json.loads(Path(got.record_path).read_text())
    assert canonical_json(record["authority"]["evaluation"]) == \
        canonical_json(got.evaluation.as_evidence_dict())
    secondary = dict(record["report"]["secondary_witness_pool"])
    assert secondary["status"] == "refused"
    assert dict(secondary["refusal"])["kind"] == \
        "secondary-witness-pool-failed"


def test_score_is_persisted_before_the_secondary_pool_runs(tmp_path, monkeypatch):
    request = _request(tmp_path, secondary_pool_budget=10)

    class ProcessDied(BaseException):
        pass

    def dies_after_checking_the_record(_evaluation, *, max_checks):
        assert max_checks == 10
        paths = tuple((Path(request.run_directory) / "runs").glob("*/result.json"))
        assert len(paths) == 1
        record = json.loads(paths[0].read_text())
        assert record["terminal"] is True
        assert record["authority"]["kind"] == "concrete_evaluation"
        assert record["authority"]["passed"] is True
        raise ProcessDied

    monkeypatch.setattr(
        witness_pool, "secondary_pool_report", dies_after_checking_the_record)
    with pytest.raises(ProcessDied):
        api.handle_command(request)

    path = next((Path(request.run_directory) / "runs").glob("*/result.json"))
    record = json.loads(path.read_text())
    assert record["terminal"] is True
    assert record["authority"]["kind"] == "concrete_evaluation"
    assert record["authority"]["passed"] is True


def test_post_score_report_failure_is_still_an_evaluated_command(
        tmp_path, monkeypatch):
    request = _request(tmp_path)
    original = command_module._attach_report_fields

    def broken(store, **fields):
        if "secondary_witness_pool" in fields:
            raise OSError("derived report filesystem broke")
        return original(store, **fields)

    monkeypatch.setattr(command_module, "_attach_report_fields", broken)
    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED
    assert got.evaluation.passed
    assert got.authority_kind == "concrete_evaluation"
    assert dict(got.failure)["kind"] == "sim-command-persistence-failure"
    record = json.loads(Path(got.record_path).read_text())
    assert record["terminal"] is True
    assert record["authority"]["passed"] is True


def test_changed_exact_provider_is_refused_only_after_the_score(tmp_path):
    class MutableExact:
        identity = "exact/test/1"

        def __init__(self, catalogue):
            self.catalogue = catalogue

        def analysis_identity(self):
            return self.identity

        def evidence_identity(self, _steps):
            return {"representation": "rep/test/1", "encoding": "enc/test/1"}

        def catalogue_manifest(self):
            return self.catalogue

        def goals(self):
            return {}

    task_contract = contract()
    reference = _ref()
    admitted = api.admit(task_contract, realization(), reference=reference)
    provider = MutableExact(api.evaluation_manifest(
        admitted, api.CatalogueManifest(())))
    exact = BoundedInstance(
        provider,
        TraceDomain(
            bound=6, contract={}, frontend="test/1", policy="test/1",
            artifact=realization().identity, task_contract=task_contract.digest))
    request = command_module._command_request(
        run_directory=tmp_path / "invocation",
        task=admitted,
        artifact=Doubling(),
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=(_BaselineOnly(),),
        frontend_catalogue=api.CatalogueManifest(()),
        generation_spec=api.GenerationSpec(
            horizon=6, queue_bound=1,
            reset_hold_cycles=task_contract.clocks[0].reset_hold_cycles,
            drain=2),
        probe_budget=0,
        reference=reference,
        exact=exact,
        analysis_wall_timeout_seconds=60,
    )
    assert request.as_dict()["analysis"]["exact"] == {
        "domain": exact.domain.digest(),
        "analysis": "exact/test/1",
        "evidence": {
            "representation": "rep/test/1", "encoding": "enc/test/1"},
        "catalogue": request.as_dict()["analysis"]["exact"]["catalogue"],
        "goal_policy": None,
        "goal_capability_use": None,
    }
    assert request.exact_capability_unchanged()
    provider.identity = "exact/test/2"

    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED
    assert got.evaluation.passed and got.analysis is None
    assert dict(got.failure)["kind"] == "sim-command-post-decision-refusal"
    refusal = got.as_dict()["failure"]["refusal"]
    assert refusal["kind"] == "sim-command-exact-capability-changed"
    record = json.loads(Path(got.record_path).read_text())
    assert record["authority"]["kind"] == "concrete_evaluation"
    assert record["authority"]["passed"] is True


def test_exact_catalogue_is_not_reconstructed_from_a_legacy_goals_method(tmp_path):
    class LegacyExact:
        @staticmethod
        def analysis_identity():
            return "exact/legacy-goals"

        @staticmethod
        def evidence_identity(_steps):
            return {"representation": "rep/test/1", "encoding": "enc/test/1"}

        @staticmethod
        def goals():
            return {}

    task_contract = contract()
    reference = _ref()
    admitted = api.admit(task_contract, realization(), reference=reference)
    exact = BoundedInstance(
        LegacyExact(),
        TraceDomain(
            bound=6, contract={}, frontend="test/1", policy="test/1",
            artifact=realization().identity,
            task_contract=task_contract.digest))

    with pytest.raises(api.Refused, match="sim-command-exact-catalogue-missing"):
        command_module._command_request(
            run_directory=tmp_path / "invocation",
            task=admitted,
            artifact=Doubling(),
            baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
            providers=(_BaselineOnly(),),
            frontend_catalogue=api.CatalogueManifest(()),
            generation_spec=api.GenerationSpec(
                horizon=6, queue_bound=1,
                reset_hold_cycles=task_contract.clocks[0].reset_hold_cycles,
                drain=2),
            probe_budget=0,
            reference=reference,
            exact=exact)


def test_exact_manifest_omission_is_refused_when_the_command_is_frozen(tmp_path):
    class OmitsTaskGoals:
        def analysis_identity(self):
            return "exact/omits-task-goals"

        def evidence_identity(self, _steps):
            return {"representation": "rep/test/1", "encoding": "enc/test/1"}

        def catalogue_manifest(self):
            return api.CatalogueManifest(())

        def goals(self):
            return {}

    task_contract = contract()
    reference = _ref()
    admitted = api.admit(task_contract, realization(), reference=reference)
    exact = BoundedInstance(
        OmitsTaskGoals(),
        TraceDomain(
            bound=6, contract={}, frontend="test/1", policy="test/1",
            artifact=realization().identity,
            task_contract=task_contract.digest))

    with pytest.raises(api.Refused, match="sim-command-exact-catalogue-mismatch"):
        command_module._command_request(
            run_directory=tmp_path / "invocation",
            task=admitted,
            artifact=Doubling(),
            baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
            providers=(_BaselineOnly(),),
            frontend_catalogue=api.CatalogueManifest(()),
            generation_spec=api.GenerationSpec(
                horizon=6, queue_bound=1,
                reset_hold_cycles=task_contract.clocks[0].reset_hold_cycles,
                drain=2),
            probe_budget=0,
            reference=reference,
            exact=exact)


@pytest.mark.cosim
def test_artifact_manifest_mismatch_is_refused_before_generation(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact

    rtl = tmp_path / "branch.sv"
    rtl.write_text(r"""module branch (
        input logic clk, input logic rst,
        input logic in_valid, output logic in_ready,
        input logic [7:0] in_a,
        output logic out_valid, input logic out_ready,
        output logic [7:0] out_y
    );
        assign in_ready = 1'b1;
        assign out_valid = 1'b1;
        always_comb begin
            if (in_a[0]) out_y = in_a;
            else out_y = 8'b0;
        end
    endmodule
    """)
    artifact = RTLArtifact(rtl, "branch", tmp_path / "rtl-work")

    stimulus = schedule([3], drain=2)
    task_contract = contract()
    reference = _ref()
    offered = realization(identity=artifact.identity)
    rtl_realization = api.Realization(
        "rtl", offered.ports.values(), channel_ports=dict(offered.channel_ports),
        identity=offered.identity)
    admitted = api.admit(
        task_contract, rtl_realization, reference=reference)
    with pytest.raises(api.Refused, match="sim-command-frontend-catalogue-mismatch"):
        command_module._command_request(
            run_directory=tmp_path / "invocation",
            task=admitted,
            artifact=artifact,
            baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
            providers=(_BaselineOnly(),),
            frontend_catalogue=api.CatalogueManifest(()),
            generation_spec=api.GenerationSpec(
                horizon=len(stimulus.slots), queue_bound=1,
                reset_hold_cycles=task_contract.clocks[0].reset_hold_cycles,
                drain=2),
            probe_budget=0,
            reference=reference)


def test_record_audit_cannot_exist_without_the_provider_that_owns_it(tmp_path):
    stimulus = schedule([3], drain=2)
    task_contract = contract()
    reference = _ref()
    admitted = api.admit(task_contract, realization(), reference=reference)

    with pytest.raises(ValueError, match="capability of its exact provider"):
        command_module._command_request(
            run_directory=tmp_path / "invocation",
            task=admitted,
            artifact=Doubling(),
            baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
            providers=(_BaselineOnly(),),
            frontend_catalogue=api.CatalogueManifest(()),
            generation_spec=api.GenerationSpec(
                horizon=len(stimulus.slots), queue_bound=1,
                reset_hold_cycles=task_contract.clocks[0].reset_hold_cycles,
                drain=2),
            probe_budget=0,
            reference=reference,
            record_audit=lambda _result, _exact: None,
        )


def test_post_score_analysis_failure_annotates_without_redeciding(
        tmp_path, monkeypatch):
    request = _request(tmp_path)

    def broken(*_args, **_kwargs):
        raise RuntimeError("analysis store handoff broke")

    monkeypatch.setattr(command_module, "analyse_after_decision", broken)
    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED
    assert got.evaluation.passed and got.analysis is None
    assert got.authority_kind == "concrete_evaluation"
    assert dict(got.failure)["kind"] == "sim-command-analysis-handoff-failed"
    record = json.loads(Path(got.record_path).read_text())
    assert record["authority"]["kind"] == "concrete_evaluation"
    assert record["authority"]["passed"] is True
    assert record["report"]["command_failure"]["kind"] == \
        "sim-command-analysis-handoff-failed"


def test_persistence_failure_crosses_as_a_closed_command_result(
        tmp_path, monkeypatch):
    request = _request(tmp_path)

    def broken(_self, _authority, timeout=None):
        raise OSError("record filesystem broke")

    monkeypatch.setattr(command_module.ResultStore, "decided", broken)
    got = api.handle_command(request)

    assert got.status is api.CommandStatus.INFRASTRUCTURE_FAILURE
    assert got.evaluation is not None and got.evaluation.passed
    assert dict(got.failure)["kind"] == "sim-command-persistence-failure"
    assert dict(got.failure)["phase"] == "concrete-evaluation"
    # `begin()` still left this invocation's in-progress envelope; no fabricated
    # terminal score is reported when the decision itself could not be persisted.
    record = json.loads(Path(got.record_path).read_text())
    assert record["terminal"] is False
    assert record["authority"]["authoritative"] is False


def test_request_rejects_ambiguous_boundary_values(tmp_path):
    stimulus = schedule([3], drain=2)
    task_contract = contract()
    reference = _ref()
    task = api.admit(task_contract, realization(), reference=reference)
    common = {
        "baseline": api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        "providers": (_BaselineOnly(),),
        "generation_spec": api.GenerationSpec(
            len(stimulus.slots), 1,
            task_contract.clocks[0].reset_hold_cycles, 2),
        "probe_budget": 0,
        "reference": reference,
    }

    with pytest.raises(ValueError, match="absolute"):
        prepare_device_command(
            task, Doubling(), run_directory="relative/run", **common)
    with pytest.raises(ValueError, match="nonnegative"):
        prepare_device_command(
            task, Doubling(), run_directory=tmp_path / "bad-budget",
            **{**common, "probe_budget": True})
    with pytest.raises(TypeError, match="LoadedReference"):
        prepare_device_command(
            task, Doubling(), run_directory=tmp_path / "live-reference",
            **{**common, "reference": object()})


def test_command_baseline_is_versioned_and_bound_to_the_run(tmp_path):
    task_contract = contract()
    reference = _ref()
    task = api.admit(task_contract, realization(), reference=reference)
    spec = api.GenerationSpec(6, 1, 3, 2)
    common = {
        "run_directory": tmp_path / "invocation",
        "providers": (_BaselineOnly(),),
        "generation_spec": spec,
        "probe_budget": 0,
        "reference": reference,
    }

    with pytest.raises(TypeError, match="BaselineSet or BaselinePolicy"):
        prepare_device_command(
            task, Doubling(), baseline=(schedule([3], drain=2),), **common)

    foreign_contract = contract(deadline=4)
    foreign = api.build_baseline(
        foreign_contract, spec,
        policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1))
    with pytest.raises(
            api.Refused, match="sim-command-baseline-contract-mismatch"):
        prepare_device_command(task, Doubling(), baseline=foreign, **common)

    other_spec = api.GenerationSpec(6, 1, 3, 2, stall_every=2)
    other = api.build_baseline(
        task_contract, other_spec,
        policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1))
    with pytest.raises(
            api.Refused, match="sim-command-baseline-generation-spec-mismatch"):
        prepare_device_command(task, Doubling(), baseline=other, **common)

    baseline = api.build_baseline(
        task_contract, spec,
        policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1))
    fingerprint, rows = baseline.provenance[0]
    forged = dict(rows[0])
    forged["baseline_policy"] = "generation-baseline/other"
    object.__setattr__(baseline, "provenance", ((fingerprint, (forged,)),))
    with pytest.raises(
            api.Refused, match="sim-command-baseline-policy-mismatch"):
        prepare_device_command(task, Doubling(), baseline=baseline, **common)


def test_command_request_cannot_be_constructed_from_raw_capabilities(tmp_path):
    stimulus = schedule([3], drain=2)
    task_contract = contract()
    reference = _ref()
    task = api.admit(task_contract, realization(), reference=reference)

    with pytest.raises(TypeError, match="closed artifact handle"):
        api.CommandRequest(
            run_directory=tmp_path / "raw", task=task, artifact=Doubling(),
            baseline=(stimulus,), providers=(_BaselineOnly(),),
            frontend_catalogue=api.CatalogueManifest(()),
            generation_spec=api.GenerationSpec(
                len(stimulus.slots), 1,
                task_contract.clocks[0].reset_hold_cycles, 2),
            probe_budget=0, reference=reference)


def test_command_identity_ignores_compatibility_import_class_paths(tmp_path, monkeypatch):
    # The semantic comparison includes recorded cost telemetry. Fix its clocks so
    # two equivalent invocations differ only in the import path under test.
    monkeypatch.setattr(evaluation_service, "perf_counter", lambda: 0.0)
    monkeypatch.setattr("behave.sim.run_assessment.perf_counter", lambda: 0.0)
    class CompatibilityProvider(_BaselineOnly):
        """The same declared provider reached through another implementation path."""

    native = _request(tmp_path, provider=_BaselineOnly())
    compatibility = _request(tmp_path, provider=CompatibilityProvider())

    assert native.identity == compatibility.identity
    assert native.as_dict()["providers"] == compatibility.as_dict()["providers"]
    assert "type" not in native.as_dict()["providers"][0]
    assert "type" not in native.as_dict()["artifact"]

    native_result = api.handle_command(native)
    Path(native.run_directory).rename(tmp_path / "native-finished")
    compatibility_result = api.handle_command(compatibility)
    assert native_result.status is compatibility_result.status \
        is api.CommandStatus.EVALUATED
    assert native_result.generated.as_dict() == \
        compatibility_result.generated.as_dict()
    assert native_result.evaluation.as_dict() == \
        compatibility_result.evaluation.as_dict()
    assert native_result.analysis.as_dict() == \
        compatibility_result.analysis.as_dict()


def test_command_result_is_a_closed_coherent_record(tmp_path):
    request = _request(tmp_path)

    with pytest.raises(TypeError, match="closed persisted-result"):
        api.CommandResult(request, "evaluated")
    evaluated = api.handle_command(request)
    assert evaluated.status is api.CommandStatus.EVALUATED
    with pytest.raises(TypeError, match="closed persisted-result"):
        api.CommandResult(
            request, api.CommandStatus.REFUSED,
            generated=evaluated.generated, failure=api.Refusal("x", "x"))
    with pytest.raises(TypeError, match="closed persisted-result"):
        api.CommandResult(
            request, api.CommandStatus.REFUSED,
            authority_kind=True, failure=api.Refusal("x", "x"))
    with pytest.raises(TypeError, match="closed persisted-result"):
        api.CommandResult(
            request, api.CommandStatus.REFUSED,
            failure=api.Refusal("x", "x"), invocation="run-1")

    # The handler-created record remains deeply immutable.  Its phase records are
    # carried from the one persisted run rather than accepted again at this boundary.
    with pytest.raises(TypeError):
        evaluated.failure["new"] = "forged"
