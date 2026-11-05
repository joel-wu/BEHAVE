"""Cross-node transport changes with same-policy partial groups; no GPU calls."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_slime_training_plan import inputs

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.task_execution.batch import BatchRecovery
from behave.task_execution.judgment import ComponentJudgment, SubmissionJudgment
from behave.task_execution.result import TaskReport, TaskResult
from behave.task_execution.simulator_judge import SimulatorJudge
from behave.train.rollouts import run_rollouts
from behave.train.slime_launch import preset
from behave.train.slime_rollout import export_handoff, inspect_handoff, pack_report, round_plan
from behave.train.training_data import NativeChatVerifier, TokenTrace
from tools.eval_review_trial import write_json
from tools.slime_training_plan import prepare


def candidate(job, store, requirements, index, *, interrupted=False):
    marker = store.put(b"fixture")
    trace = TokenTrace(requirements.adapter, marker, requirements.checkpoint,
        requirements.tokenizer, requirements.template, (10, 11), (12, 13), (1, 1),
        {"old_log_probs": [-.5, -.25]})
    response = store.put(canonical_json({"token_data": trace.as_dict(), "usage": {}}))
    account = store.put(canonical_json({"turns": [{"turn": 0, "exclusion": None,
        "request": marker.as_dict(), "response": response.as_dict()}], "charges": [],
        "unpriced_dispatches": 0, "unresolved_dispatches": 0, "billing_source": "fixture"}))
    judgment = SubmissionJudgment(marker, None, ComponentJudgment(index % 2, "fixture"), marker)
    no_submission = index == 4
    result = TaskResult(job.task.task_id, job.task.release, job.task.revision, job.attempt.attempt_id,
        None if interrupted else marker,
        None if interrupted else "turn_limit" if no_submission else "final_action",
        None if interrupted or no_submission else marker,
        None if interrupted or no_submission else judgment,
        "solver" if interrupted else None, "solver-worker-cancelled" if interrupted else None, account)
    reference = store.put(result.to_bytes())
    row = dict(task_id=job.task.task_id, attempt_id=job.attempt.attempt_id,
        run_key=BlobRef.of(job.to_bytes()).sha256, result=reference.as_dict(), score=result.score,
        status=result.status, error=None, testbench=None, testbench_error=None)
    return result, reference, row, (marker, response, account, reference)


def partial_run(tmp_path, inputs, *, saved=False, policy_generation=0, activate=None):
    config, release, split, task = inputs
    config = {**config, "initial_updates": 4, "resume_updates": 0}
    root = tmp_path / "old"
    write_json(root / "configuration.json", config)
    write_json(root / "selection.json", {"tasks": [task], "release": str(release), "cpus": 4})
    binding = prepare(root / "artifacts/plan", config, release, [task], split)
    reader = DirectoryStore(binding["objects"])
    native = JournalDirectory(Path(binding["run_directory"]) / "native")
    round_id = int(saved)
    if saved:
        from behave.train.slime_stage import _native

        checkpoint = root / "artifacts/checkpoint"
        (checkpoint / "iter_0000000").mkdir(parents=True)
        (checkpoint / "iter_0000000/state.pt").write_bytes(b"fixture optimizer and weights")
        (checkpoint / "latest_checkpointed_iteration.txt").write_text("0")
        native.journal("rounds").append((), "round_scored", {"round": 0,
            "binding": BlobRef.of(canonical_json(binding)).as_dict()})
        _native(binding, config, load=checkpoint, save=checkpoint, updates=4).save(0)
    identity = BlobRef.of(canonical_json(binding))
    if activate is not None:
        binding = activate(binding, native)
        reader = DirectoryStore(binding["objects"])
    policy = {"schema": "behave.slime-policy/1", "binding": identity.as_dict(),
              "round": round_id, "engine_versions": [["http://127.0.0.1:15001", "1"]], "previous": None,
              **({"recovery": policy_generation} if policy_generation else {})}
    policy_ref = native.objects.put(canonical_json(policy))
    native.journal("rounds").append(native.journal("rounds").load(verify_artifacts=False), "round_started", {"round": round_id,
        "binding": policy["binding"], "policy": policy_ref.as_dict()}, (policy_ref,))
    endpoint = LocalChatEndpoint.from_dict(binding["endpoint"])
    write_json(native.directory / f"request_{round_id:08d}/request.json",
               {"endpoint": endpoint.as_dict(), "policy": policy, "round": round_id})
    write_json(native.directory / f"request_{round_id:08d}/binding.json", binding)
    plan = round_plan(binding, reader, round_id=round_id, endpoint=endpoint, policy=policy_ref)
    suffix = f"_recovery_{policy_generation}" if policy_generation else ""
    run = JournalDirectory(Path(binding["run_directory"]) / f"round_{round_id:08d}{suffix}")
    refs = tuple(run.objects.put(data) for job in plan.execution.jobs for data in job.input_blobs())
    run.objects.publish_owned(reader, (plan.manifest,))
    plan_ref = run.objects.put(plan.to_bytes())
    run.journal("training").append((), "training_started", {"plan": plan_ref.as_dict()},
                                  (plan_ref, plan.manifest, *refs))
    execution = JournalDirectory(run.directory / "execution")
    execution.objects.publish_owned(run.objects, (plan.manifest, *refs))
    ref = execution.objects.put(plan.execution.to_bytes())
    events = [execution.journal("report").append((), "evaluation_started", {"plan": ref.as_dict()}, (ref, *refs))]
    for index in range(6):
        _, _, row, closure = candidate(plan.execution.jobs[index], execution.objects, plan.requirements,
                                       index, interrupted=index == 5)
        events.append(execution.journal("report").append(events, "evaluation_case",
            {"index": index, "row": row}, closure))
    return root, config, release, split, task, binding, plan, policy_ref


@pytest.mark.parametrize("excluded", [None, 5, 7])
@pytest.mark.parametrize("actor_gpus,cp", [(8, 2), (4, 1)])
def test_handoff_keeps_five_of_eight_including_zero_and_only_runs_three_missing(
        tmp_path, inputs, monkeypatch, excluded, actor_gpus, cp):
    root, config, release, split, task, old_binding, old_plan, policy = partial_run(tmp_path, inputs)
    before = (root / "artifacts/plan/rounds/round_00000000/execution/report/000001.json").read_bytes()
    changed = config if excluded is None else {**config, "workers": 28,
        "layout": {**config["layout"], "rollout_gpus_per_engine": 1, "rollout_excluded_gpus": [excluded]}}
    changed = {**changed, "layout": {**changed["layout"], "actor_gpus_per_node": actor_gpus,
                                   "context_parallel": cp}}
    info = inspect_handoff(root, changed, release, [task], workers_stopped=True)
    assert info["step"] == -1 and info["parent"] is None
    assert info["recovery"].indices == frozenset(range(5))
    descriptor = export_handoff(info, tmp_path / "handoff-objects")
    # Simulate losing all node-local paths and changing ports on another node.
    new_config = {**changed, "endpoint_url": "http://127.0.0.1:25000/v1/chat/completions",
                  "rollout_recovery": descriptor}
    binding = prepare(tmp_path / "new/artifacts/plan", new_config, release, [task], split)
    if excluded is not None:
        from behave.train.slime_rollout import read_binding
        assert read_binding(tmp_path / "new/artifacts/plan/binding.json")["rollout_engine_count"] == 7
        assert binding["rollout_recovery"]["execution_change"]["after"]["workers"] == 28
    reader = DirectoryStore(binding["objects"])
    endpoint = LocalChatEndpoint.from_dict(binding["endpoint"])
    endpoint = replace(endpoint, tokens=replace(endpoint.tokens, checkpoint=policy))
    plan = round_plan(binding, reader, round_id=0, endpoint=endpoint, policy=policy)
    assert plan.execution.jobs[:5] == old_plan.execution.jobs[:5]
    assert plan.execution.jobs[5].model != old_plan.execution.jobs[5].model
    recovery = BatchRecovery.load(reader, BlobRef.from_dict(binding["rollout_recovery"]["case_head"]))
    calls = []

    def execute(**kwargs):
        job, run = kwargs["job"], kwargs["run"]
        index = plan.execution.jobs.index(job)
        calls.append(index)
        result, ref, _, closure = candidate(job, run.objects, plan.requirements, index)
        event = run.journal("task").append((), "fixture", {"result": ref.as_dict()}, closure)
        return TaskReport(result, ref, BlobRef.of(event.to_bytes()))

    monkeypatch.setattr("behave.task_execution.batch.execute_task", execute)
    monkeypatch.setattr("behave.task_execution.batch.require_task_quiescent", lambda *a, **kw: None)
    run = JournalDirectory(tmp_path / "new/round")
    args = dict(plan=plan, approved_manifest=plan.manifest, reader=reader, run=run,
        judge=SimulatorJudge(), verifier=NativeChatVerifier(endpoint), recovery=recovery,
        update_groups=1, active_groups=1)
    summary = run_rollouts(**args)
    assert sorted(calls) == [5, 6, 7]
    assert summary["group_selection"]["selected_indices"] == list(range(8))
    assert [r["examples"]["reward"] for r in summary["candidates"]] == [0, 1, 0, 1, 0, 1, 0, 1]
    packed = pack_report(summary, run.objects, recipe=preset("grpo"), workers=1,
                         directory=tmp_path / "packed")
    assert len(packed.candidates) == 8 and packed.payloads
    assert all(row["rollout_log_probs"] == [-.5, -.25] for row in packed.payloads)
    assert run_rollouts(**args) == summary
    assert sorted(calls) == [5, 6, 7]
    assert (root / "artifacts/plan/rounds/round_00000000/execution/report/000001.json").read_bytes() == before


def test_handoff_refuses_unfenced_workers_and_distribution_changes(tmp_path, inputs):
    root, config, release, split, task, binding, plan, policy = partial_run(tmp_path, inputs)
    with pytest.raises(ValueError, match="workers-stopped"):
        inspect_handoff(root, config, release, [task], workers_stopped=False)
    with pytest.raises(ValueError, match="configuration or seed"):
        inspect_handoff(root, {**config, "training_seed": 42}, release, [task], workers_stopped=True)
    with pytest.raises(ValueError, match="training layout"):
        inspect_handoff(root, {**config, "layout": {**config["layout"], "tensor_parallel": 2}},
                        release, [task], workers_stopped=True)
    native = JournalDirectory(Path(binding["run_directory"]) / "native")
    with native.owner(), pytest.raises(Exception, match="run-already-owned"):
        inspect_handoff(root, config, release, [task], workers_stopped=True)
    info = inspect_handoff(root, config, release, [task], workers_stopped=True)
    descriptor = export_handoff(info, tmp_path / "export")
    wrong = {**config, "runtime": {**config["runtime"], "max_turns": 99}, "rollout_recovery": descriptor}
    new_binding = prepare(tmp_path / "new/plan", wrong, release, [task], split)
    with pytest.raises(ValueError, match="beyond its transport"):
        round_plan(new_binding, DirectoryStore(new_binding["objects"]), round_id=0,
                   endpoint=LocalChatEndpoint.from_dict(new_binding["endpoint"]), policy=policy)


@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("reduce_actor", [False, True])
def test_public_job_entry_can_migrate_before_or_after_checkpoint(tmp_path, inputs, monkeypatch, saved, reduce_actor):
    from tools.slime_training_job import submit

    root, config, release, split, task, *_ = partial_run(tmp_path, inputs, saved=saved)
    freezes, launches = [], []
    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **kw: freezes.append((a, kw)))
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: launches.append((a, kw)))
    out = tmp_path / "job"
    submit(out, 123, root / "configuration.json", release, [task], split, cpus=144,
           updates=10, resume_from=root, resume_rollouts=True, workers_stopped=True,
           **({"actor_gpus_per_node": 4, "context_parallel": 1} if reduce_actor else {}))
    result = json.loads((out / "continuation.json").read_text())
    assert (result["checkpoint"] is not None) is saved
    assert (result["load"] is not None) is saved
    assert result["first_update"] == int(saved)
    assert result["rollout_recovery"] and len(launches) == 1
    assert freezes[0][1]["overrides"] == {}
    from behave.train.slime_stage import _native

    parent = {} if not saved else {"parent_reader": DirectoryStore(result["objects"]),
                                  "parent_checkpoint": BlobRef.from_dict(result["checkpoint"])}
    new_config = {**json.loads((out / "configuration.json").read_text()),
                  "rollout_recovery": result["rollout_recovery"]}
    new_binding = prepare(out / "artifacts/plan", new_config, release, [task], split, **parent)
    restored = _native(new_binding, new_config,
        load=Path(result["load"] or config["model_path"]), save=out / "checkpoints", updates=10)
    restored.load(0 if saved else -1)
    assert restored.first_step == int(saved)


def test_generate_uses_original_policy_and_repeated_handoff_preserves_history(tmp_path, inputs, monkeypatch):
    from types import SimpleNamespace

    from test_slime_application import configured

    from behave.train.slime_data import ScoredDataSource
    from behave.train.slime_rollout import execute_round, handoff_verifier

    root, config, release, split, task, _, _, policy_ref = partial_run(
        tmp_path, inputs, policy_generation=2)
    descriptor = export_handoff(inspect_handoff(root, config, release, [task], workers_stopped=True),
                                tmp_path / "first-export")
    child = tmp_path / "child"
    write_json(child / "configuration.json", config)
    write_json(child / "selection.json", {"tasks": [task], "release": str(release)})
    binding = prepare(child / "artifacts/plan", {**config, "rollout_recovery": descriptor}, release, [task], split)
    scratch = tmp_path / "args"
    scratch.mkdir()
    args = configured(scratch).args
    args.behave_rollout_binding = str(child / "artifacts/plan/binding.json")
    args.behave_rollout_binding_sha256 = BlobRef.of(canonical_json(binding)).sha256
    args.sglang_router_ip, args.sglang_router_port = "127.0.0.1", 25000
    args.rollout_num_gpus = args.rollout_num_gpus_per_engine = 8
    source = ScoredDataSource(args)
    source.load(-1)
    versions = (("http://127.0.0.1:25001", "1"),)
    monkeypatch.setattr("behave.train.slime_data.engine_versions", lambda *a, **kw: versions)
    calls = []

    def execute(**kwargs):
        job, run = kwargs["job"], kwargs["run"]
        index = current_plan[0].execution.jobs.index(job)
        calls.append(index)
        result, ref, _, closure = candidate(job, run.objects, current_plan[0].requirements, index)
        event = run.journal("task").append((), "fixture", {"result": ref.as_dict()}, closure)
        return TaskReport(result, ref, BlobRef.of(event.to_bytes()))

    current_plan = []

    def service(argv, **kwargs):
        request = json.loads(Path(argv[-2]).read_text())
        assert BlobRef.of(canonical_json(request["policy"])) == policy_ref
        endpoint = LocalChatEndpoint.from_dict(request["endpoint"])
        current_plan.append(round_plan(binding, DirectoryStore(binding["objects"]), round_id=0,
            endpoint=endpoint, policy=policy_ref))
        response = execute_round(binding, round_id=0, endpoint=endpoint, policy_record=request["policy"])
        Path(argv[-1]).write_bytes(canonical_json(response))
        return SimpleNamespace(status="exited", returncode=0, elapsed_seconds=1,
            stdout=b"", stderr=b"", cleanup_state=SimpleNamespace(quiescent=True))

    monkeypatch.setattr("behave.train.slime_data.run_service", service)
    monkeypatch.setattr("behave.task_execution.batch.execute_task", execute)
    monkeypatch.setattr("behave.task_execution.batch.require_task_quiescent", lambda *a, **kw: None)
    # Keep packing in the test process, exercising the ordinary prepared-batch path.
    monkeypatch.setattr("behave.train.slime_rollout.SlimeBatch.rollout_output", lambda batch: batch.payloads)
    binding["preparation_workers"] = 1
    source.binding["preparation_workers"] = 1
    result = source.generate(0)
    assert result is not None and sorted(calls) == [5, 6, 7]
    assert source.journal.load()[-1].kind == "round_scored"
    with pytest.raises(ValueError):
        source.generate(0)
    assert sorted(calls) == [5, 6, 7]
    second = inspect_handoff(child, config, release, [task], workers_stopped=True)
    assert second["recovery"].indices == frozenset(range(8))
    exported = export_handoff(second, tmp_path / "second-export")
    grandchild = prepare(tmp_path / "grandchild/plan", {**config, "rollout_recovery": exported}, release, [task], split)
    endpoint = LocalChatEndpoint.from_dict(grandchild["endpoint"])
    endpoint = replace(endpoint, tokens=replace(endpoint.tokens, checkpoint=policy_ref))
    verifier = handoff_verifier(grandchild, 0, endpoint)
    assert len(verifier.endpoints) >= 2
    assert grandchild["rollout_recovery"]["completed_indices"] == list(range(8))


def test_recovery_preparation_accepts_frozen_legacy_pool_policy(tmp_path, inputs, monkeypatch):
    from dataclasses import asdict, dataclass

    @dataclass
    class LegacyPolicy:
        mastery_cooldown_updates: int
        zero_cooldown_updates: int

        @classmethod
        def from_dict(cls, row):
            return cls(**row)

        def as_dict(self):
            return asdict(self)

    config, release, split, task = inputs
    config["sampler"] = {"seed": "legacy", "order": "shuffle", "tasks_per_update": 1}
    config["curriculum"] = {"mastery_cooldown_updates": 10, "zero_cooldown_updates": 2}
    monkeypatch.setattr("behave.train.slime_data.TaskPoolPolicy", LegacyPolicy)
    binding = prepare(tmp_path / "plan", config, release, [task], split)
    assert binding["curriculum"] == config["curriculum"]


def test_recovery_preserves_legacy_selection_call_without_injecting_priority(monkeypatch):
    from types import SimpleNamespace

    from behave.train import slime_data as slime_data_module

    calls = []
    def legacy(tasks, state, sampler, update, *, include_reserve):
        calls.append((tasks, state, sampler, update, include_reserve))
        return "legacy order"

    monkeypatch.setattr(slime_data_module, "select_pool", legacy)
    state = {"tasks": {"keep": {"cooldown_until": 25}}}
    policy = SimpleNamespace(mastery_cooldown_updates=10, zero_cooldown_updates=2)
    assert slime_data_module.select_frozen_pool(("a",), state, "sampler", 20,
        include_reserve=True, policy=policy) == "legacy order"
    assert calls == [(("a",), state, "sampler", 20, True)]
    assert state["tasks"]["keep"]["cooldown_until"] == 25


def test_scored_handoff_reuses_exact_batch_without_rpc_or_group_race(tmp_path, inputs, monkeypatch):
    from test_slime_recipe import arguments

    from behave.train.slime_data import ScoredDataSource
    from behave.train.slime_rollout import SlimeBatch, save_batch

    root, config, release, split, task, old_binding, plan, policy = partial_run(tmp_path, inputs)
    native = JournalDirectory(Path(old_binding["run_directory"]) / "native")
    original_run = JournalDirectory(Path(old_binding["run_directory"]) / "round_00000000")
    head = original_run.objects.put(b"fixture scored report")
    batch = SlimeBatch(({"tokens": [10, 11, 12], "rollout_log_probs": [-.5, -.25]},),
                      ({"retained_turns": 2, "reward": .5},), preset("grpo"),
                      group_selection={"selected_indices": [0], "launched_indices": [0, 1, 2],
                                       "completion_order": ["original"], "observations": []})
    prepared = original_run.directory / "prepared_batch.json"
    save_batch(prepared, batch, head=head, manifest=plan.manifest, plan=BlobRef.of(plan.to_bytes()))
    original_bytes = prepared.read_bytes()
    endpoint = LocalChatEndpoint.from_dict(old_binding["endpoint"])
    endpoint = replace(endpoint, tokens=replace(endpoint.tokens, checkpoint=policy))
    row = {"schema": "behave.slime-scored-round/1", "round": 0,
           "manifest": plan.manifest.as_dict(), "policy": policy.as_dict(),
           "head": head.as_dict(), "objects": str(original_run.objects.directory),
           "endpoint": endpoint.as_dict(), "prepared_batch": str(prepared)}
    receipt = native.objects.put(canonical_json(row))
    native.journal("rounds").append(native.journal("rounds").load(verify_artifacts=False),
        "round_scored", {"round": 0, "binding": BlobRef.of(canonical_json(old_binding)).as_dict(),
                         "receipt": receipt.as_dict()}, (receipt,))
    info = inspect_handoff(root, config, release, [task], workers_stopped=True)
    assert info["scored_receipt"] == receipt.as_dict()
    descriptor = export_handoff(info, tmp_path / "handoff")
    new_root = tmp_path / "new"
    new_dir = new_root / "artifacts/plan"
    changed = {**config, "endpoint_url": "http://127.0.0.1:28000/v1/chat/completions"}
    write_json(new_root / "configuration.json", changed)
    write_json(new_root / "selection.json", {"tasks": [task], "release": str(release)})
    binding = prepare(new_dir, {**changed, "rollout_recovery": descriptor}, release, [task], split)
    args = arguments(preset("grpo"))
    args.behave_rollout_binding = str(new_dir / "binding.json")
    args.behave_rollout_binding_sha256 = BlobRef.of(canonical_json(binding)).sha256
    args.load = args.save = str(tmp_path / "checkpoints")
    args.save_interval, args.num_rollout = 1, 4
    source = ScoredDataSource(args)
    source.load(-1)
    for name in ("engine_versions", "run_service", "from_training_report"):
        monkeypatch.setattr(f"behave.train.slime_data.{name}",
                            lambda *a, **kw: pytest.fail("complete batch must not be regenerated or rescored"))
    monkeypatch.setattr(SlimeBatch, "rollout_output", lambda self: self)
    result = source.generate(0)
    assert result == batch
    assert prepared.read_bytes() == original_bytes
    events = source.journal.load(verify_artifacts=False)
    assert events[-1].payload["training_segments"] == 1
    assert events[-1].payload["recorded_round"] == 0
    again = inspect_handoff(new_root, changed, release, [task], workers_stopped=True)
    assert again["scored_receipt"] == receipt.as_dict()
    assert again["policy_ref"] == policy
    assert again["run"].directory == original_run.directory
    with pytest.raises(ValueError):
        source.generate(0)


@pytest.mark.parametrize("field,value", [("round", 9), ("policy", {}), ("manifest", {})])
def test_scored_handoff_rejects_wrong_policy_round_or_manifest(field, value):
    from behave.train.slime_rollout import validate_scored_receipt

    row = {"schema": "behave.slime-scored-round/1", "round": 11, "manifest": {"id": "tasks"},
           "policy": {"id": "weights11"}}
    invalid = {**row, field: value}
    with pytest.raises(ValueError, match="restored policy/round"):
        validate_scored_receipt(invalid, round_id=11, manifest=row["manifest"], policy=row["policy"])
