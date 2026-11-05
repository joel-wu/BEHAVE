"""Real-task launch preparation without models, tools, or optimizer execution."""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from behave.core.artifacts import BlobRef, DirectoryReader, read_verified
from behave.core.records import RecordError, canonical_json
from behave.sim import api
from behave.task_execution.judge_policy import JudgePolicy
from behave.train.plan import TrainingManifest, TrainingPlan
from behave.train.slime_data import prepare_task_plan
from behave.train.slime_rollout import read_binding
from tools.slime_training_plan import native_arguments, prepare


def test_new_final_eval_task_is_rejected_even_with_a_stale_allowlist(tmp_path):
    from tools.dataset_splits import current_path

    task = (current_path() / "eval.txt").read_text().split()[0]
    stale = tmp_path / "old_training.txt"
    stale.write_text(task + "\n")
    with pytest.raises(ValueError, match="current evaluation tasks"):
        prepare(tmp_path / "plan", {}, tmp_path / "nonexistent_release", [task], stale)
    assert not (tmp_path / "plan").exists()


@pytest.mark.parametrize("missing", ["--configuration", "--train-split"])
def test_cli_requires_runtime_configuration_and_membership(monkeypatch, capsys, missing):
    from tools import slime_training_plan

    arguments = {"--configuration": "runtime.json", "--train-split": "train.txt",
                 "--release": "release", "--task": "example"}
    argv = ["slime_training_plan", "output", *(item for pair in arguments.items()
            if pair[0] != missing for item in pair)]
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr(slime_training_plan, "prepare", lambda *a, **k: pytest.fail("implicit preparation"))
    with pytest.raises(SystemExit) as error:
        slime_training_plan.main()
    assert error.value.code == 2 and missing in capsys.readouterr().err


@pytest.fixture
def inputs(tmp_path, publication):
    configuration = json.loads((Path(__file__).resolve().parents[2] /
                               "infra/train/qwen27b_acceptance.json").read_text())
    # This fixture owns one synthetic task and an empty tokenizer. Reuse the
    # model/sampling profile, but select its small test schedule explicitly.
    configuration.pop("sampler")
    configuration.pop("training_seed", None)
    configuration.pop("curriculum")
    configuration.pop("update_groups")
    configuration.pop("group_replenishment")
    configuration.pop("checkpoint_retention")
    configuration.pop("serving_context_tokens")
    configuration.update(native_token_continuation=False, phase_timing=False,
                         initial_updates=2, resume_updates=1, checkpoint_interval=2)
    configuration.pop("hf_checkpoint_interval", None)
    model, upstream = tmp_path / "model", tmp_path / "slime"
    model.mkdir()
    (model / "config.json").write_text(json.dumps({"hidden_size": 16, "num_attention_heads": 4,
        "num_key_value_heads": 4, "intermediate_size": 64, "num_hidden_layers": 4}))
    for name in ("tokenizer.json", "tokenizer_config.json"):
        (model / name).write_text("{}")
    (upstream / "scripts/models").mkdir(parents=True)
    (upstream / "scripts/models/qwen3.5-27B.sh").write_text(
        "MODEL_ARGS=( --num-layers 4 --spec upstream.model get_spec )\n")
    (upstream / "train.py").write_text("raise AssertionError('preparation must not execute training')\n")
    configuration.update(model_path=str(model), slime_source=str(upstream))
    policy = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, search_seconds=0)
    task = replace(publication.task, judge_policy=publication.put(policy.to_bytes()))
    _, ref = publication.publish(task)
    release = tmp_path / "release"
    release.mkdir()
    shutil.copytree(publication.root, release / "objects")
    (release / "release.json").write_bytes(canonical_json(ref.as_dict()))
    split = tmp_path / "train.txt"
    split.write_text(task.task_id + "\n")
    return configuration, release, split, task.task_id


def test_real_task_plan_reuses_runtime_and_native_token_evidence(tmp_path, inputs):
    configuration, release, split, task_id = inputs
    configuration["endpoint_url"] = "http://127.0.0.1:40123/v1/chat/completions"
    output = tmp_path / "training"
    binding = prepare(output, configuration, release, [task_id], split)
    assert read_binding(output / "binding.json") == binding
    assert all(binding[name] is None for name in
               ("round_seconds", "cleanup_seconds", "control_timeout_seconds", "round_log_bytes"))
    assert binding["control_python"] and binding["control_package"]
    assert binding["run_directory"] == str(output / "rounds")
    assert binding["endpoint"]["url"] == configuration["endpoint_url"]
    reader = DirectoryReader(binding["objects"])
    plan = TrainingPlan.from_bytes(read_verified(reader, BlobRef.from_dict(binding["plan"])), reader)
    plan.preflight(reader, approved_manifest=BlobRef.from_dict(binding["manifest"]))
    assert len(plan.execution.jobs) == 8
    assert len({job.attempt.attempt_id for job in plan.execution.jobs}) == 8
    assert plan.group_ids == (task_id,) * 8
    assert plan.requirements.required_signals == ("old_log_probs",)
    assert plan.execution.score_on_submit is True
    assert plan.execution.testbench is None  # Coverage is not a training reward.
    assert plan.execution.verification.wait_seconds is None
    for job in plan.execution.jobs:
        assert job.setting == "joint"
        assert job.attempt.max_turns == 60
        assert job.attempt.context_tokens == 131072
        assert job.attempt.request_timeout_seconds is None
    parameters = binding["endpoint"]["parameters"]
    assert parameters == configuration["sampling"]
    assert "max_tokens" not in parameters and "seed" not in parameters
    assert parameters["chat_template_kwargs"]["enable_thinking"]
    assert parameters["chat_template_kwargs"]["reasoning_effort"] == "xhigh"
    assert "reasoning_effort" not in parameters
    assert binding["endpoint"]["routing"] == "attempt"
    assert not (output / "rounds").exists()


def test_native_continuation_is_an_explicit_training_runtime_choice(tmp_path, inputs):
    from test_native_continuation import PROFILE, TEMPLATE

    configuration, release, split, task_id = inputs
    configuration["native_token_continuation"] = True
    configuration["runtime"]["context_reset_message"] = "Fixture reminder. Continue the same attempt."
    (Path(configuration["model_path"]) / "tokenizer_config.json").write_text(json.dumps({
        "chat_template": TEMPLATE, "eos_token": "<end>",
        "added_tokens_decoder": {"99": {"content": "<end>"}}}))
    binding = prepare(tmp_path / "native", configuration, release, [task_id], split)
    assert binding["endpoint"]["tokens"]["continuation"] == PROFILE
    assert read_binding(tmp_path / "native/binding.json") == binding
    reader = DirectoryReader(binding["objects"])
    plan = TrainingPlan.from_bytes(reader.read(BlobRef.from_dict(binding["plan"])), reader)
    assert all(job.attempt.model_policy["native_continuation"] for job in plan.execution.jobs)
    assert all(job.attempt.context_reset_message == configuration["runtime"]["context_reset_message"]
               for job in plan.execution.jobs)


@pytest.mark.parametrize("preview", [None, 32768])
def test_training_shell_preview_is_explicit_in_runtime_and_solver_jobs(tmp_path, inputs, preview):
    configuration, release, split, task_id = inputs
    configuration["output_preview_bytes"] = preview
    binding = prepare(tmp_path / "preview", configuration, release, [task_id], split)
    reader = DirectoryReader(binding["objects"])
    plan = TrainingPlan.from_bytes(reader.read(BlobRef.from_dict(binding["plan"])), reader)
    assert all(job.output_preview_bytes == preview for job in plan.execution.jobs)


def test_preparation_resumes_partial_immutable_files_and_rejects_changed_inputs(tmp_path, inputs, monkeypatch):
    from behave.train import slime_data as slime_data_module

    configuration, release, split, task_id = inputs
    output = tmp_path / "partial-plan"
    install = slime_data_module._install
    def interrupted(directory, name, value):
        if name == "configuration.json":
            raise InterruptedError("fixture coordinator stopped after binding installation")
        install(directory, name, value)
    monkeypatch.setattr(slime_data_module, "_install", interrupted)
    with pytest.raises(InterruptedError):
        prepare(output, configuration, release, [task_id], split)
    original = (output / "binding.json").read_bytes()
    monkeypatch.setattr(slime_data_module, "_install", install)
    binding = prepare(output, configuration, release, [task_id], split)
    assert (output / "binding.json").read_bytes() == original == canonical_json(binding)
    assert prepare(output, configuration, release, [task_id], split) == binding
    changed = {**configuration, "sampling": {**configuration["sampling"], "temperature": 0.123}}
    with pytest.raises(RecordError, match="prepared learning inputs changed"):
        prepare(output, changed, release, [task_id], split)


def test_no_eval_task_or_duplicate_can_enter_preparation(tmp_path, inputs):
    configuration, release, split, task_id = inputs
    for selected in (["held-out"], [task_id, task_id], []):
        output = tmp_path / "forbidden"
        with pytest.raises(ValueError):
            prepare(output, configuration, release, selected, split)
    assert not output.exists()


@pytest.mark.parametrize("attribute", ["COMMAND_VERSION", "RESULT_VERSION"])
def test_preparation_rejects_stale_evaluator_before_writing_plan(tmp_path, inputs, monkeypatch, attribute):
    configuration, release, split, task_id = inputs
    monkeypatch.setattr(api, attribute, "different-installed-version")
    output = tmp_path / "stale-evaluator"
    with pytest.raises(RecordError, match="differs from installed Sim") as error:
        prepare(output, configuration, release, [task_id], split)
    assert error.value.code == "training-judge-version-mismatch"
    assert not output.exists()


@pytest.mark.parametrize("seconds", [None, 173])
def test_preparation_preserves_runtime_concurrency_and_limits(tmp_path, inputs, seconds):
    configuration, release, split, task_id = inputs
    configuration.update(workers=64, samples_per_task=64, cleanup_seconds=seconds,
                         round_seconds=seconds, control_timeout_seconds=seconds, round_log_bytes=seconds)
    output = tmp_path / "concurrency"
    binding = prepare(output, configuration, release, [task_id], split)
    assert read_binding(output / "binding.json") == binding
    assert all(binding[name] == seconds for name in
               ("round_seconds", "cleanup_seconds", "control_timeout_seconds", "round_log_bytes"))
    reader = DirectoryReader(binding["objects"])
    plan = TrainingPlan.from_bytes(read_verified(reader, BlobRef.from_dict(binding["plan"])), reader)
    assert len(plan.execution.jobs) == plan.execution.workers == 64
    assert plan.execution.model_admission.concurrency == 64


def test_serving_reserve_does_not_expand_training_context(tmp_path, inputs):
    configuration, release, split, task_id = inputs
    configuration["serving_context_tokens"] = 131074
    output = tmp_path / "serving-reserve"
    binding = prepare(output, configuration, release, [task_id], split)
    args = native_arguments(output, updates=2, load=Path(configuration["model_path"]),
                            cpus=8, checkpoint_directory=tmp_path / "checkpoints")
    assert args[args.index("--sglang-context-length") + 1] == "131074"
    assert args[args.index("--seq-length") + 1] == "131072"
    reader = DirectoryReader(binding["objects"])
    plan = TrainingPlan.from_bytes(read_verified(reader, BlobRef.from_dict(binding["plan"])), reader)
    assert all(job.attempt.context_tokens == 131072 for job in plan.execution.jobs)


def test_package_preparation_accepts_a_guarded_manifest_without_split_files(tmp_path, publication, inputs):
    from test_corpus import guarded_publication

    from behave.train.plan import manifest_for_release

    config, _, _, _ = inputs
    publication.task = replace(publication.task, judge_policy=publication.put(
        JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION).to_bytes()))
    guarded_publication(publication)
    manifest = manifest_for_release(publication.reader, publication.ref)
    output = tmp_path / "guarded"
    binding = prepare_task_plan(output, config, publication.reader, manifest)
    store = DirectoryReader(binding["objects"])
    approved = BlobRef.from_dict(binding["manifest"])
    assert TrainingManifest.from_bytes(read_verified(store, approved)) == manifest
    plan = TrainingPlan.from_bytes(read_verified(store, BlobRef.from_dict(binding["plan"])), store)
    plan.preflight(store, approved_manifest=approved)
    assert manifest.corpus is not None
    assert json.loads((output / "training_selection.json").read_text())["training_manifest"] == approved.as_dict()
    with pytest.raises(RecordError, match="another corpus index"):
        prepare_task_plan(tmp_path / "downgraded", config, publication.reader, replace(manifest, corpus=None))
    assert not (tmp_path / "downgraded").exists()


def test_package_preparation_does_not_replace_approved_revisions(tmp_path, inputs):
    config, release, _, task_id = inputs
    reader = DirectoryReader(release / "objects")
    reference = BlobRef.from_dict(json.loads((release / "release.json").read_text()))
    manifest = TrainingManifest(reference, ((task_id, BlobRef.of(b"different task revision")),))
    with pytest.raises(RecordError, match="approved task revisions"):
        prepare_task_plan(tmp_path / "changed", config, reader, manifest)
    assert not (tmp_path / "changed").exists()


def test_launch_pool_and_native_update_size_are_distinct(tmp_path, inputs, publication):
    from behave.data.published import TaskRevision

    configuration, _, _, _ = inputs
    configuration.update(samples_per_task=8, update_groups=8,
                         sampler={"tasks_per_update": 12, "order": "cycle", "seed": "fixture"})
    policy = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, search_seconds=0)
    revisions = []
    for index in range(12):
        task = replace(publication.task, task_id=f"task-{index}", judge_policy=publication.put(policy.to_bytes()))
        revisions.append(TaskRevision(task.task_id, publication.put(task.to_bytes())))
    _, release = publication.publish(tasks=tuple(revisions))
    manifest = TrainingManifest(release, tuple((r.task_id, r.manifest) for r in revisions))
    output = tmp_path / "oversampled"
    binding = prepare_task_plan(output, configuration, publication.reader, manifest)
    assert read_binding(output / "binding.json")["update_groups"] == 8
    plan = TrainingPlan.from_bytes(read_verified(DirectoryReader(binding["objects"]),
                                   BlobRef.from_dict(binding["plan"])), DirectoryReader(binding["objects"]))
    assert len(plan.execution.jobs) == 96
    selection = json.loads((output / "training_selection.json").read_text())
    assert selection["launched_candidates_per_update"] == 96
    assert selection["candidates_per_update"] == 64
    arguments = native_arguments(output, updates=2, load=tmp_path / "load", cpus=90,
                                 checkpoint_directory=tmp_path / "save")
    assert arguments[arguments.index("--global-batch-size") + 1] == "64"
    assert arguments[arguments.index("--rollout-batch-size") + 1] == "8"


@pytest.mark.parametrize("seconds", [0, 8])
def test_training_preserves_published_search_policy(tmp_path, publication, inputs, seconds):
    configuration, _, split, task_id = inputs
    task = replace(publication.task, judge_policy=publication.put(
        JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, search_seconds=seconds).to_bytes()))
    _, ref = publication.publish(task)
    release = tmp_path / "search-release"
    release.mkdir()
    shutil.copytree(publication.root, release / "objects")
    (release / "release.json").write_bytes(canonical_json(ref.as_dict()))
    output = tmp_path / "training"
    binding = prepare(output, configuration, release, [task_id], split)
    selection = json.loads((output / "training_selection.json").read_text())
    assert selection["scoring"] == "published-task-policy"
    assert selection["judge_policies"] == {task_id: task.judge_policy.as_dict()}
    policy = JudgePolicy.from_bytes(read_verified(DirectoryReader(binding["objects"]), task.judge_policy))
    assert policy.search_seconds == seconds
    assert selection["native_training_started"] is False


def test_native_arguments_derive_batch_and_context_from_the_plan(tmp_path, inputs):
    configuration, release, split, task_id = inputs
    output = tmp_path / "training"
    prepare(output, configuration, release, [task_id], split)
    args = native_arguments(output, updates=3, load=tmp_path / "checkpoint", cpus=180,
                            checkpoint_directory=tmp_path / "checkpoint")
    for flag, value in {"--n-samples-per-prompt": "8", "--global-batch-size": "8",
                        "--rollout-batch-size": "1", "--seq-length": "131072",
                        "--max-position-embeddings": "131072", "--sglang-context-length": "131072",
                        "--num-rollout": "3", "--context-parallel-size": "2",
                        "--tensor-model-parallel-size": "4", "--behave-cpus": "180"}.items():
        assert args.count(flag) == 1
        assert args[args.index(flag) + 1] == value
    assert "--colocate" in args
    assert "--rollout-max-response-len" not in args
    assert args[args.index("--spec") + 1:args.index("--spec") + 3] == (
        "behave.train.slime_qwen", "get_qwen3_5_spec")
    assert args[args.index("--load") + 1] == str(tmp_path / "checkpoint")
    assert args.count("--sglang-tool-call-parser") == 1
    assert args[args.index("--sglang-tool-call-parser") + 1] == "behave-qwen3-coder"


@pytest.mark.parametrize("spec", [None, ["other_model", "build_spec"]])
def test_launch_model_and_parsers_are_configuration_not_qwen_defaults(tmp_path, inputs, spec):
    configuration, release, split, task_id = inputs
    model_flags = tmp_path / "other_model.sh"
    model_flags.write_text("MODEL_ARGS=( --num-layers 4 --spec original.model get_spec )\n")
    configuration.update(model_template=str(model_flags), model_spec=spec, native_arguments=[])
    output = tmp_path / "configured-training"
    prepare(output, configuration, release, [task_id], split)
    args = native_arguments(output, updates=17, load=tmp_path / "checkpoint", cpus=24,
                            checkpoint_directory=tmp_path / "checkpoint")
    assert args[args.index("--spec") + 1:args.index("--spec") + 3] == tuple(spec or ["original.model", "get_spec"])
    assert "--sglang-tool-call-parser" not in args
    assert "--sglang-reasoning-parser" not in args
    assert args[args.index("--num-rollout") + 1] == "17"


def test_sampler_binding_preserves_the_pool_and_sets_native_minibatch_size(tmp_path, publication, inputs, monkeypatch):
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.data.published import ReleaseManifest, TaskRevision
    from behave.train.slime_rollout import round_plan

    configuration, original, split, task_id = inputs
    policy = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, search_seconds=3)
    tasks = tuple(replace(publication.task, task_id=name, judge_policy=publication.put(policy.to_bytes()))
                  for name in (task_id, "second_train_task", "third_train_task"))
    manifest = ReleaseManifest("sampling-fixture", tuple(TaskRevision(task.task_id, publication.put(task.to_bytes()))
        for task in tasks), publication.policy, {})
    reference = publication.put(manifest.to_bytes())
    release = tmp_path / "pool-release"
    shutil.copytree(publication.root, release / "objects")
    (release / "release.json").write_bytes(canonical_json(reference.as_dict()))
    split.write_text("\n".join(task.task_id for task in tasks))
    configuration["sampler"] = {"tasks_per_update": 2, "order": "cycle", "seed": "test"}
    output = tmp_path / "sampled-training"
    binding = prepare(output, configuration, release, [task.task_id for task in tasks], split)
    assert read_binding(output / "binding.json") == binding
    assert binding["schema"] == "behave.slime-rollout-binding/2"
    reader = DirectoryReader(binding["objects"])
    template = TrainingPlan.from_bytes(read_verified(reader, BlobRef.from_dict(binding["plan"])), reader)
    assert len(template.execution.jobs) == 24
    endpoint = LocalChatEndpoint.from_dict(binding["endpoint"])
    first = round_plan(binding, reader, round_id=0, endpoint=endpoint, policy=BlobRef.of(b"weights-0"))
    second = round_plan(binding, reader, round_id=1, endpoint=endpoint, policy=BlobRef.of(b"weights-1"))
    assert len(first.execution.jobs) == len(second.execution.jobs) == 16
    assert {job.task.task_id for job in second.execution.jobs} == {tasks[2].task_id, tasks[0].task_id}
    assert set(job.attempt.attempt_id for job in first.execution.jobs).isdisjoint(
        job.attempt.attempt_id for job in second.execution.jobs)
    assert second == round_plan(binding, reader, round_id=1, endpoint=endpoint, policy=BlobRef.of(b"weights-1"))
    # The trainer and control worker run different Python ABIs. Reconstructing
    # the approved plan must not bind the trainer's executable or package path.
    with monkeypatch.context() as patch:
        patch.setattr("behave.agent.providers.local_chat.sys.executable", "/native/bin/python3.12")
        patch.setattr("behave.agent.providers.local_chat.__file__", "/native/lib/python3.12/site-packages/behave/agent/providers/local_chat.py")
        native_plan = round_plan(binding, reader, round_id=1, endpoint=endpoint, policy=BlobRef.of(b"weights-1"))
    assert second.to_bytes() == native_plan.to_bytes()
    for job in native_plan.execution.jobs:
        assert job.model.argv[0] == binding["control_python"]
        assert job.model.argv[5] == binding["control_package"]
        assert job.attempt.model_policy["transport"] == job.model.identity
    args = native_arguments(output, updates=2, load=tmp_path / "checkpoint", cpus=180,
                            checkpoint_directory=tmp_path / "checkpoint")
    assert args[args.index("--rollout-batch-size") + 1] == "2"
    assert args[args.index("--global-batch-size") + 1] == "16"
