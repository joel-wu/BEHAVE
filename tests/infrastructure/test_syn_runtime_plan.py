"""Shared Syn composition without archived launchers, services or model calls."""

from dataclasses import replace
from pathlib import Path

import pytest
from syn_runtime_fixture import runtime as fixture_runtime
from test_slime_training_plan import inputs  # noqa: F401

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.improve.input_stores import Inputs
from behave.improve.runtime_plan import continue_experience, round_runtime
from behave.train.plan import prepare_from_release


@pytest.fixture
def runtime(inputs):  # noqa: F811
    return fixture_runtime(inputs[0])


@pytest.fixture
def composition(tmp_path, inputs, runtime):  # noqa: F811
    configuration, release, split, task_id = inputs
    prepared = tmp_path / "training"
    binding = prepare_from_release(prepared, configuration, release, [task_id], split,
        repository=Path(__file__).resolve().parents[2])
    handoff = {"checkpoint": BlobRef.of(b"fixture-checkpoint").as_dict(),
        "model_path": configuration["model_path"], "model_revision": "verified-export",
        "training_objects": binding["objects"], "training_manifest": binding["manifest"]}
    objects = DirectoryStore(tmp_path / "operator-inputs")
    reader = Inputs(objects, DirectoryStore(Path(binding["objects"])))
    endpoint = LocalChatEndpoint.from_dict(binding["endpoint"])
    policy = {**endpoint.model_policy(), "checkpoint": handoff["checkpoint"]}
    guide = objects.put(b"Fixture guidance, no model calls.")
    return {"runtime": runtime, "handoff": handoff, "inputs": reader, "objects": objects,
        "endpoint": endpoint, "policy": policy, "configuration": configuration, "env": {},
        "guides": {"construction": guide, "acquisition": guide}, "binding": binding}


@pytest.mark.parametrize("require_license_metadata", [False, True, None])
def test_shared_round_composes_roles_and_preserves_release_policy(composition, require_license_metadata):
    from behave.data.release_records import ReleasePolicy

    runtime = {**composition["runtime"], "require_license_metadata": require_license_metadata}
    if require_license_metadata is None:
        runtime.pop("require_license_metadata")
    plan = round_runtime(**{**composition, "runtime": runtime})
    assert ReleasePolicy.from_bytes(composition["objects"].read(plan.release_policy)).require_license_metadata is (
        True if require_license_metadata is None else require_license_metadata)
    assert len(plan.diagnosis.jobs) == 8  # The fixture release has one task.
    assert plan.diagnosis.workers == plan.diagnosis.model_admission.concurrency == 64
    assert plan.workers == 64  # No independent per-stage bottleneck.
    assert plan.diagnosis.verification.concurrency == 8
    assert all(job.attempt.model_policy == composition["policy"] and job.model == composition["endpoint"].command()
               for job in plan.diagnosis.jobs)
    synthesis = plan.construction.synthesis.plan
    assert plan.construction.synthesis.development_admission is None
    assert plan.construction.pools()["development_pool"] is None
    for role in (synthesis.processes.author, synthesis.processes.reader,
                 synthesis.processes.source_review):
        role.check_execution()
    assert synthesis.rtl is None
    assert plan.construction.scope is None and synthesis.preparation is None
    assert plan.proposals_per_source is None
    attempts = (synthesis.author, synthesis.reader, synthesis.source_review.reviewer,
                plan.acquisitions[0].job.attempt)
    assert {attempt.role for attempt in attempts} == {
        "task_author", "spec_reader", "source_reviewer",
        "source_acquirer"}
    assert all(attempt.model_policy == composition["policy"] for attempt in attempts)
    assert plan.acquired_updates == plan.raw_updates == 1
    assert plan.task.seconds is plan.task.cleanup_seconds is plan.raw.seconds is plan.raw.cleanup_seconds is None
    assert plan.raw.configuration["layout"]["rollout_gpus"] == 0
    assert plan.task.configuration["model_revision"] == plan.raw.configuration["model_revision"] == "verified-export"


def test_admission_target_is_preserved(composition):
    target = {"admitted_tasks": 20, "max_batches": 2}
    plan = round_runtime(**{**composition,
        "runtime": {**composition["runtime"], "generation_target": target}})
    assert plan.generation_target == target
    assert not plan.keep_serving


def test_role_attempt_overrides_do_not_change_rollout_or_optimizer(composition):
    runtime = composition["runtime"]
    plan = round_runtime(**composition)
    synthesis = plan.construction.synthesis.plan
    # Syn roles can keep recent history and have their own turn allowance.
    # Diagnostic rollout and optimizer settings still use the training runtime.
    syn_runtime = {**runtime, "role_attempts": {
        "task_author": {"max_turns": 100, "context_reset_message": None},
        "spec_reader": {"max_turns": 100, "context_reset_message": None},
        "source_reviewer": {"context_reset_message": None},
        "source_acquirer": {"context_reset_message": None},
    }}
    configured = round_runtime(**{**composition, "runtime": syn_runtime})
    syn = configured.construction.synthesis.plan
    assert syn.author.max_turns == syn.reader.max_turns == 100
    assert syn.source_review.reviewer.max_turns == synthesis.source_review.reviewer.max_turns
    assert all(attempt.context_reset_message is None for attempt in (
        syn.author, syn.reader, syn.source_review.reviewer, configured.acquisitions[0].job.attempt))
    assert configured.diagnosis == plan.diagnosis
    assert configured.task == plan.task and configured.raw == plan.raw
    assert syn.max_revisions == synthesis.max_revisions


def test_analyst_budgets_and_output_limits_propagate_to_all_roles(composition):
    runtime = composition["runtime"]
    plan = round_runtime(**composition)
    from behave.improve.experience import ExperiencePolicy
    reset_runtime = {**runtime, "acquisition_workers": 5, "output_preview_bytes": 32768,
        "experience_policy": ExperiencePolicy().as_dict(),
        "acquisition_limits": {"max_tasks": 3, "max_observations": 24},
        "attempt": {**runtime["attempt"], "context_reset_message": "Fixture: workspace files are unchanged."},
        "role_attempts": {name: {"max_turns": 100} for name in (
            "source_acquirer", "task_author", "spec_reader", "source_reviewer")}}
    reset_plan = round_runtime(**{**composition, "runtime": reset_runtime})
    reset_syn = reset_plan.construction.synthesis.plan
    assert len(reset_plan.acquisitions) == 5
    assert all(a.job.inputs.experience_mode == "delta-v1" and
               a.job.inputs.experience_policy == reset_runtime["experience_policy"]
               for a in reset_plan.acquisitions)
    assert reset_plan.acquisition_limits == reset_runtime["acquisition_limits"]
    assert reset_plan.record()["acquisition_limits"] == reset_runtime["acquisition_limits"]
    for attempt in (reset_syn.author, reset_syn.reader, reset_syn.source_review.reviewer,
                    *(a.job.attempt for a in reset_plan.acquisitions)):
        assert attempt.max_turns == 100
        assert attempt.context_reset_message == reset_runtime["attempt"]["context_reset_message"]
    assert all(role.output_preview_bytes == 32768 for role in (
        reset_syn.processes.author, reset_syn.processes.reader, reset_syn.processes.source_review))
    assert all(a.job.output_preview_bytes == 32768 for a in reset_plan.acquisitions)
    assert reset_plan.task == plan.task and reset_plan.raw == plan.raw


def test_explicit_model_transport_survives_controller_change(composition):
    endpoint, handoff = composition["endpoint"], composition["handoff"]
    # Installing a corrected controller must not rebind the original broker.
    broker = replace(endpoint.command(), argv=("/original/controller/python", *endpoint.command().argv[1:]))
    resume_policy = {**endpoint.model_policy(command=broker), "checkpoint": handoff["checkpoint"]}
    resumed = round_runtime(**{**composition, "policy": resume_policy, "model": broker})
    assert resumed.model == broker
    assert resumed.model_policy["transport"] == broker.identity
    assert all(job.model == broker for job in resumed.diagnosis.jobs)
    assert resumed.acquisitions[0].job.model == broker
    assert resumed.construction.synthesis.plan.processes.source_review.model == broker


def test_memory_and_history_are_only_visible_to_analysts(tmp_path, composition):
    plan = round_runtime(**composition)
    objects = composition["objects"]
    from behave.data.role import RoleJob
    from behave.improve.experience import experience_files, experience_snapshot, load_experience

    previous = DirectoryStore(tmp_path / "prior-memory")
    files = experience_files({"construction": "Check output record shapes.", "solver": "Check reset."})
    for file in files:
        previous.put(file.data)
    memory = previous.put(experience_snapshot(files).to_bytes())
    history_bytes = canonical_json({"schema": "behave.construction-history/1", "rounds": [
        {"round_id": "prior", "tasks": [{"task_id": "failed-task", "status": "pending"}]}]})
    history = previous.put(history_bytes)
    continued = continue_experience(plan, {"experience": memory.as_dict(),
        "construction_history": history.as_dict()}, previous, objects)
    # Every non-Analyst field remains identical, including solver and optimizer.
    assert replace(continued, acquisitions=plan.acquisitions) == plan
    assert load_experience(objects, memory) == tuple(sorted(files, key=lambda file: file.reference.path))
    assert objects.read(history) == history_bytes
    for config in continued.acquisitions:
        assert config.job.inputs.experience == memory and config.job.inputs.construction_history == history
        assert RoleJob.from_bytes(config.job.to_bytes()) == config.job
    historical = continue_experience(plan, {"experience": memory.as_dict()}, previous, objects)
    assert all(config.job.inputs.construction_history is None for config in historical.acquisitions)


def test_pending_memory_deltas_survive_continuation_without_reaching_training(tmp_path, composition):
    from behave.data.role import RoleJob
    from behave.improve.experience import ExperiencePolicy, delta_files, experience_snapshot, load_delta

    policy = ExperiencePolicy()
    runtime = {**composition["runtime"], "acquisition_workers": 3,
               "experience_policy": policy.as_dict()}
    plan = round_runtime(**{**composition, "runtime": runtime})
    previous = DirectoryStore(tmp_path / "pending-memory")
    files = delta_files({"construction": "Check the source contract.", "solver": ""}, policy.delta_bytes)
    for file in files:
        previous.put(file.data)
    delta = previous.put(experience_snapshot(files).to_bytes())
    continued = continue_experience(plan, {"experience_pending_changes": [delta.as_dict()]},
                                    previous, composition["objects"])
    assert replace(continued, acquisitions=plan.acquisitions) == plan
    assert load_delta(composition["objects"], delta, policy.delta_bytes) == files
    for config in continued.acquisitions:
        assert config.job.inputs.pending_experience == (delta,)
        assert config.job.inputs.experience is None
        assert RoleJob.from_bytes(config.job.to_bytes()) == config.job
    assert all(not config.job.inputs.pending_experience for config in plan.acquisitions)


@pytest.mark.parametrize("field", ["experience", "construction_history", "experience_pending_changes"])
def test_invalid_memory_cannot_silently_continue(tmp_path, composition, field):
    from behave.core.records import RecordError
    from behave.improve.experience import ExperiencePolicy

    plan = round_runtime(**{**composition, "runtime": {
        **composition["runtime"], "experience_policy": ExperiencePolicy().as_dict()}})
    previous = DirectoryStore(tmp_path / "invalid-memory")
    invalid = previous.put(canonical_json({"unexpected": "not a memory record"})).as_dict()
    selected = {field: [invalid] if field == "experience_pending_changes" else invalid}
    # Legacy construction history indexes its schema directly; memory snapshots
    # use typed record validation. Both must reject the malformed handoff.
    with pytest.raises((RecordError, KeyError)):
        continue_experience(plan, selected, previous, composition["objects"])


def test_imported_feedback_uses_one_template_and_explicit_judge_version(composition, monkeypatch):
    from behave.data.release_records import ReleasePolicy
    from behave.task_execution.judge_policy import JudgePolicy
    from behave.train.plan import TrainingPlan

    # Imported training feedback already supplies observations. Composition
    # needs a solver template, not a reconstruction of every training group.
    def unexpected(*args, **kwargs):
        pytest.fail("imported feedback must not rebuild the full TrainingPlan")

    monkeypatch.setattr(TrainingPlan, "from_bytes", unexpected)
    plan = round_runtime(**{**composition,
        "handoff": {**composition["handoff"], "feedback_imported": True},
        "runtime": {**composition["runtime"], "judge_version": {
            "command": "fixture-command-v2", "authority": "fixture-authority-v2"}}})
    assert len(plan.diagnosis.jobs) == 1
    release = ReleasePolicy.from_bytes(composition["objects"].read(plan.release_policy))
    judge = JudgePolicy.from_bytes(composition["objects"].read(release.judge_policy))
    assert judge.command_version == "fixture-command-v2"
    assert judge.authority_version == "fixture-authority-v2"
    assert plan.construction.synthesis.plan.judge_policy == release.judge_policy
