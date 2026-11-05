"""Shared construction runtime for standalone and training-owned Syn calls."""

from dataclasses import replace
from pathlib import Path

from behave.agent.admission import AdmissionPolicy
from behave.agent.attempt import AttemptSpec
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.release_records import ReleasePolicy
from behave.data.role import RoleJob
from behave.data.role_policy import ProcessRoles, RolePolicy
from behave.data.supervisor import ConstructionConfiguration
from behave.data.synthesis import SynthesisConfiguration
from behave.data.synthesis_records import SourceReviewPlan, SynthesisPlan
from behave.improve.acquisition import AcquisitionConfiguration
from behave.improve.acquisition_records import AcquisitionInputs
from behave.improve.learning_round import LearningRuntime
from behave.improve.round import RoundRuntime
from behave.isolation.model import ResourceLimits
from behave.train.plan import TrainingPlan
from behave.train.training_data import RawEncoding


def round_runtime(runtime, handoff, inputs, objects, endpoint, policy, configuration, env, guides, *, model=None, binding=None):
    """Compose production stage plans; run_round binds selected sources and scopes."""
    training_objects = DirectoryStore(Path(handoff["training_objects"]))
    if binding is None:
        binding = parse_json((Path(handoff["training_objects"]).parent / "binding.json").read_bytes())
    payload = read_verified(training_objects, BlobRef.from_dict(binding["plan"]))
    if handoff.get("feedback_imported"):
        from behave.task_execution.batch_plan import BatchPlan

        template = parse_json(payload)["execution"]
        template["jobs"] = template["jobs"][:1]
        execution = BatchPlan.from_bytes(canonical_json(template), training_objects)
    else:
        execution = TrainingPlan.from_bytes(payload, training_objects).execution
    model = endpoint.command() if model is None else model
    diagnosis = replace(execution, run_id=runtime["round_id"] + ":diagnosis",
        jobs=tuple(replace(job, model=model, attempt=replace(job.attempt, model_policy=policy,
            **runtime["attempt"])) for job in execution.jobs),
        workers=runtime["workers"], model_admission=AdmissionPolicy("syn-model", runtime["workers"]),
        verification=AdmissionPolicy("syn-judge", runtime["judge_workers"]))
    model_pool = AdmissionPolicy("syn-model", runtime["workers"])
    checks = AdmissionPolicy("syn-check", runtime["check_workers"])
    role = RolePolicy(model, log_bytes=runtime["role_log_bytes"],
                      shell_limits=ResourceLimits(**runtime["shell_limits"]),
                      output_preview_bytes=runtime.get("output_preview_bytes"))

    def attempt(name):
        settings = {**runtime["attempt"], **runtime.get("role_attempts", {}).get(name, {})}
        return AttemptSpec(f"{runtime['round_id']}:{name}", name, policy, **settings)

    def job(value):
        return RoleJob(value, attempt(value.role), model, log_bytes=role.log_bytes, shell_limits=role.shell_limits,
                       output_preview_bytes=role.output_preview_bytes)

    # These slots are not tasks or invented source material. The shared round
    # controller replaces them with the actual Analyst proposal and source
    # before a role is invoked. They are recorded explicitly as unbound slots.
    slot = objects.put(canonical_json({"schema": "behave.round-input-slot/1", "round": runtime["round_id"]}))
    from behave.data.published import TaskManifest
    from behave.train.plan import TrainingManifest

    manifest = TrainingManifest.from_bytes(read_verified(inputs, BlobRef.from_dict(handoff["training_manifest"])))
    policies = {TaskManifest.from_bytes(read_verified(inputs, revision)).judge_policy
                for _, revision in manifest.tasks}
    if len(policies) != 1:
        raise ValueError("Syn probe requires an explicit common judge policy")
    judge_policy = policies.pop()
    if handoff.get("feedback_imported"):
        from behave.task_execution.judge_policy import JudgePolicy

        original = JudgePolicy.from_bytes(read_verified(inputs, judge_policy))
        judge_policy = objects.put(replace(original, command_version=runtime["judge_version"]["command"],
            authority_version=runtime["judge_version"]["authority"]).to_bytes())
    synthesis = SynthesisPlan(runtime["round_id"], runtime["round_id"], slot, judge_policy,
        attempt("task_author"), attempt("spec_reader"), max_revisions=runtime["task_revisions"],
        source_review=SourceReviewPlan(guides["construction"], attempt("source_reviewer")),
        processes=ProcessRoles(role, role, role), rtl=None)
    construction = ConstructionConfiguration(runtime["round_id"],
        SynthesisConfiguration(synthesis, model_pool, checks), None)
    # New plans specify this in runtime. Older frozen plans retain the existing
    # ReleasePolicy default rather than silently changing their admission rule.
    release_options = ({"require_license_metadata": runtime["require_license_metadata"]}
                       if "require_license_metadata" in runtime else {})
    release = objects.put(ReleasePolicy(runtime["round_id"], "acquired-training", guides["construction"],
        judge_policy, policy, **release_options).to_bytes())
    memory_policy = runtime.get("experience_policy")
    acquisitions = tuple(AcquisitionConfiguration(job(AcquisitionInputs(slot, slot, guides["acquisition"],
                         experience_mode=None if memory_policy is None else "delta-v1",
                         experience_policy=memory_policy)), model_pool)
                         for _ in range(runtime.get("acquisition_workers", 1)))
    tokenizer = objects.put((Path(handoff["model_path"]) / "tokenizer.json").read_bytes())
    encoding = RawEncoding(tokenizer, **runtime["raw_encoding"])
    task_config = {**configuration, **runtime["task_configuration"],
                   "model_path": handoff["model_path"], "model_revision": handoff["model_revision"]}
    raw_config = {**task_config, **runtime["raw_configuration"]}

    def learning(config):
        return LearningRuntime(config, runtime["cpus"], env,
            config["learning_seconds"], config["cleanup_seconds"])

    return RoundRuntime(model, policy, diagnosis, acquisitions, construction, None,
        release, (), runtime["acquired_updates"], runtime["raw_updates"], encoding,
        learning(task_config), learning(raw_config), workers=runtime["workers"],
        generation_only=runtime.get("generation_only", False),
        acquisition_limits=runtime.get("acquisition_limits"),
        generation_target=runtime.get("generation_target"))


def continue_experience(plan, selected, reader, objects):
    """Reuse memory and construction outcomes together at the next boundary."""
    from behave.improve.construction import history_file
    from behave.improve.experience import load_experience

    memory = None if selected.get("experience") is None else BlobRef.from_dict(selected["experience"])
    history = (None if selected.get("construction_history") is None else
               BlobRef.from_dict(selected["construction_history"]))
    if memory is not None:
        files = load_experience(reader, memory)
        objects.publish(reader, (memory, *(file.reference.content for file in files)))
    if history is not None:
        objects.put(history_file(reader, history).data)
    pending = tuple(BlobRef.from_dict(ref) for ref in selected.get("experience_pending_changes", ()))
    if pending:
        from .experience import ExperiencePolicy, load_delta

        policy = ExperiencePolicy.from_dict(plan.acquisitions[0].job.inputs.experience_policy)
        for ref in pending:
            files = load_delta(reader, ref, policy.delta_bytes)
            objects.publish(reader, (ref, *(file.reference.content for file in files)))
    return replace(plan, acquisitions=tuple(replace(config,
        job=replace(config.job, inputs=replace(config.job.inputs, experience=memory,
                                               construction_history=history, pending_experience=pending)))
        for config in plan.acquisitions))
