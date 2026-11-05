"""Production task-pool handoff and recovery, with no GPU/model execution."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_slime_recipe import arguments
from test_slime_training_plan import inputs
from test_training_task_pool import POLICY

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.data.published import TaskRevision
from behave.sim import api
from behave.task_execution.judge_policy import JudgePolicy
from behave.train.plan import TrainingManifest
from behave.train.pool import SamplingPolicy, select_pool
from behave.train.slime_data import ScoredDataSource, prepare_task_plan
from behave.train.slime_launch import preset
from behave.train.slime_rollout import read_binding, round_plan
from behave.train.trainer import active_events


def source_at(tmp_path, publication, inputs, *, tasks="abcd", parent=None, parent_reader=None,
              replenishment=None, sampler_order="cycle", manifest=None, curriculum=POLICY):
    configuration = {**inputs[0], "samples_per_task": 3,
        "sampler": {"tasks_per_update": 2, "order": sampler_order, "seed": "fixture"},
        "curriculum": curriculum.as_dict()}
    if replenishment is not None:
        configuration.update(group_replenishment=replenishment, update_groups=1)
    if manifest is None:
        judge = publication.put(JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, search_seconds=0).to_bytes())
        revisions = tuple(TaskRevision(task, publication.put(replace(publication.task, task_id=task,
                                                                   judge_policy=judge).to_bytes()))
                          for task in tasks)
        _, release = publication.publish(tasks=revisions)
        manifest = TrainingManifest(release, tuple((r.task_id, r.manifest) for r in revisions))
    output = tmp_path / "plan"
    binding = prepare_task_plan(output, configuration, publication.reader, manifest,
                               parent_reader=parent_reader, parent_checkpoint=parent)
    assert read_binding(output / "binding.json") == binding
    args = arguments(preset("grpo"))
    args.behave_rollout_binding = str(output / "binding.json")
    args.behave_rollout_binding_sha256 = BlobRef.of(canonical_json(binding)).sha256
    args.save = args.load = str(tmp_path / "checkpoint")
    args.save_interval, args.num_rollout = 1, 8
    args.sglang_router_ip, args.sglang_router_port = "127.0.0.1", 9000
    args.rollout_num_gpus, args.rollout_num_gpus_per_engine = 8, 4
    return ScoredDataSource(args)


def completed_checkpoint(source, step):
    root = Path(source.args.save)
    directory = root / f"iter_{step:07d}"
    directory.mkdir(parents=True)
    (directory / "state.pt").write_bytes(b"opaque optimizer fixture")
    (root / "latest_checkpointed_iteration.txt").write_text(str(step))
    source.save(step)
    return source.journal.seal()[0]


def mock_scoring(monkeypatch, source):
    from behave.train import slime_data as slime_data_module

    state = {"version": 1, "selection": None, "requests": []}
    monkeypatch.setattr(slime_data_module, "engine_versions", lambda *a, **kw:
        tuple((f"http://127.0.0.1:{port}", str(state["version"])) for port in (9001, 9002)))

    def score(argv, **kwargs):
        request = json.loads(Path(argv[-2]).read_bytes())
        state["requests"].append(request)
        state["selection"] = request["policy"]["task_selection"]
        ep = LocalChatEndpoint.from_dict(request["endpoint"])
        policy = BlobRef.of(canonical_json(request["policy"]))
        ep = replace(ep, tokens=replace(ep.tokens, checkpoint=policy))
        Path(argv[-1]).write_bytes(canonical_json({"schema": "behave.slime-scored-round/1",
            "round": request["round"], "manifest": source.binding["manifest"], "policy": policy.as_dict(),
            "endpoint": ep.as_dict(), "objects": source.binding["objects"],
            "head": BlobRef.of(b"scored fixture").as_dict(), "task_selection": state["selection"]}))
        return SimpleNamespace(status="exited", returncode=0, stdout=b"", stderr=b"", elapsed_seconds=.1,
                               cleanup_state=SimpleNamespace(quiescent=True))

    def batch(*args, **kwargs):
        # All-correct a/b, mixed c/d. Group membership is production-selected.
        candidates = tuple({"group_id": row["task_id"], "reward": reward, "retained_turns": 1}
            for row in state["selection"]
            for reward in ((1, 1, 1) if row["task_id"] in "ab" else (1, 0, 1)))
        return SimpleNamespace(payloads=({},), candidates=candidates, group_selection=None,
                               rollout_output=lambda: "native batch")

    monkeypatch.setattr(slime_data_module, "run_service", score)
    monkeypatch.setattr(slime_data_module, "from_training_report", batch)
    return state


def test_live_handoff_cold_restore_and_rollback_observe_once(tmp_path, publication, inputs, monkeypatch):
    source = source_at(tmp_path, publication, inputs)
    state = mock_scoring(monkeypatch, source)
    source.load(-1)
    assert source.generate(0) == "native batch"
    before = source._pool_state(source.journal.load())
    assert before["tasks"]["a"]["category"] == "mastered"
    completed_checkpoint(source, 0)
    cold = ScoredDataSource(source.args)
    cold.load(0)
    assert cold._pool_state(cold.journal.load()) == before
    assert cold.generate(1) == "native batch"
    assert [r["task_id"] for r in state["selection"]] == ["c", "d"]
    once = cold._pool_state(cold.journal.load())
    assert once["tasks"]["c"]["observed_groups"] == 1
    cold.native.abandon(reason="fixture optimizer interruption", workers_stopped=True)
    restarted = ScoredDataSource(source.args)
    restarted.load(0)
    assert restarted._pool_state(restarted.journal.load()) == before
    assert restarted.generate(1) == "native batch"
    assert len(state["requests"]) == 2  # Recovery used the original scored batch.
    assert restarted._pool_state(restarted.journal.load()) == once
    completed_checkpoint(restarted, 1)
    assert [e.payload["round"] for e in active_events(restarted.journal.load())
            if e.kind == "round_scored"] == [0, 1]


def test_new_self_improvement_pool_inherits_checkpoint_state(tmp_path, publication, inputs, monkeypatch):
    source = source_at(tmp_path / "before", publication, inputs)
    state = mock_scoring(monkeypatch, source)
    source.generate(0)
    head = completed_checkpoint(source, 0)
    # The same parent import used by improve.learn_round; only the task manifest
    # changes. Old mastery persists and new c/e get the next opportunities.
    after = source_at(tmp_path / "after", publication, inputs, tasks="ace",
                      parent=head, parent_reader=source.run.objects)
    after.args.load = source.args.save
    after.load(0)
    inherited = after._pool_state(after.journal.load())
    assert inherited == source._pool_state(source.journal.load())
    assert after.native.first_step == 1
    selected = select_pool(after._pool_tasks, inherited,
        SamplingPolicy.from_dict(after.binding["sampler"]), 1)
    assert [r["task_id"] for r in selected] == ["c", "e"]
    assert all(r["reason"] == "new" for r in selected)
    assert len(state["requests"]) == 1


def test_shuffle_continuation_after_five_updates_keeps_pool_and_cooldown(
        tmp_path, publication, inputs, monkeypatch):
    source = source_at(tmp_path / "before", publication, inputs)
    source.args.save_interval = 5
    state = mock_scoring(monkeypatch, source)
    for step in range(5):
        state["version"] = step + 1
        source.generate(step)
    before = source._pool_state(source.journal.load())
    assert before["tasks"]["a"]["cooldown_until"] == 11
    head = completed_checkpoint(source, 4)

    after = source_at(tmp_path / "after", publication, inputs,
                      parent=head, parent_reader=source.run.objects, sampler_order="shuffle")
    after.args.load = source.args.save
    after.load(4)
    inherited = after._pool_state(after.journal.load())
    assert after.native.first_step == 5
    assert inherited == before
    sampler = SamplingPolicy.from_dict(after.binding["sampler"])
    for step in range(5, 11):
        selected = select_pool(after._pool_tasks, inherited, sampler, step, include_reserve=True)
        assert {row["task_id"] for row in selected} == {"c", "d"}
    rejoined = select_pool(after._pool_tasks, inherited, sampler, 11, include_reserve=True)
    assert {row["task_id"] for row in rejoined} == set("abcd")


def test_control_and_native_use_identical_explicit_groups(tmp_path, publication, inputs):
    source = source_at(tmp_path, publication, inputs)
    store = DirectoryStore(source.binding["objects"])
    endpoint = LocalChatEndpoint.from_dict(source.binding["endpoint"])
    selection = ({"task_id": "d", "reason": "rotation"},
                 {"task_id": "a", "reason": "rotation"})
    kwargs = dict(round_id=3, endpoint=endpoint, policy=BlobRef.of(b"policy"))
    with pytest.raises(ValueError, match="recorded"):
        round_plan(source.binding, store, **kwargs)
    native = round_plan(source.binding, store, **kwargs, task_selection=selection)
    transported = json.loads(canonical_json(selection))
    producer = round_plan(source.binding, store, **kwargs, task_selection=transported)
    assert native == producer
    assert native.group_ids == ("d",) * 3 + ("a",) * 3
    assert len(native.execution.jobs) == 6


def test_sim_upgrade_keeps_absolute_cooldown_and_survives_another_checkpoint(
        tmp_path, publication, inputs, monkeypatch):
    source = source_at(tmp_path / "before", publication, inputs)
    source.args.save_interval = 5
    state = mock_scoring(monkeypatch, source)
    for step in range(5):
        state["version"] = step + 1
        source.generate(step)
    before = source._pool_state(source.journal.load())
    head = completed_checkpoint(source, 4)
    monkeypatch.setattr(api, "COMMAND_VERSION", api.COMMAND_VERSION + "-upgrade")
    monkeypatch.setattr(api, "RESULT_VERSION", api.RESULT_VERSION + "-upgrade")

    after = source_at(tmp_path / "after", publication, inputs,
                      parent=head, parent_reader=source.run.objects)
    after.args.load = source.args.save
    after.load(4)
    restored = after._pool_state(after.journal.load())
    for task in "abcd":
        assert restored["tasks"][task] == {
            **before["tasks"][task], "revision": dict(after._pool_tasks)[task].as_dict()}
    backup = json.loads((after.path.parent / "task_pool_resume.json").read_text())
    assert backup["before"] == before and backup["after"] == restored
    assert set(backup["sim_version_only"]) == set("abcd")
    assert backup["changed_tasks"] == []
    sampler = SamplingPolicy.from_dict(after.binding["sampler"])
    for step in range(5, 11):
        assert {row["task_id"] for row in select_pool(
            after._pool_tasks, restored, sampler, step, include_reserve=True)} == {"c", "d"}
    assert {row["task_id"] for row in select_pool(
        after._pool_tasks, restored, sampler, 11, include_reserve=True)} == set("abcd")

    mock_scoring(monkeypatch, after)
    after.generate(5)
    next_head = completed_checkpoint(after, 5)
    cold = source_at(tmp_path / "cold", publication, inputs,
                     parent=next_head, parent_reader=after.run.objects)
    cold.args.load = after.args.save
    cold.load(5)
    assert cold.native.first_step == 6
    assert cold._pool_state(cold.journal.load())["tasks"]["a"] == restored["tasks"]["a"]
    assert source._pool_state(source.journal.load()) == before  # No historical rewrite.


@pytest.mark.parametrize("change", ["spec", "judge_settings"])
def test_task_or_scoring_changes_do_not_silently_inherit_mastery(
        tmp_path, publication, inputs, monkeypatch, change):
    from behave.data.published import TaskManifest
    from behave.train.pool import sync_state

    source = source_at(tmp_path / "before", publication, inputs)
    mock_scoring(monkeypatch, source)
    source.generate(0)
    before = source._pool_state(source.journal.load())
    head = completed_checkpoint(source, 0)
    store = DirectoryStore(source.binding["objects"])
    revisions = []
    for task_id, revision in source._pool_tasks:
        task = TaskManifest.from_bytes(store.read(revision))
        if task_id == "a":
            if change == "spec":
                task = replace(task, spec=replace(task.spec, content=publication.put(b"Changed computation.")))
            else:
                policy = JudgePolicy.from_bytes(store.read(task.judge_policy))
                task = replace(task, judge_policy=publication.put(replace(policy, seed="different").to_bytes()))
        revisions.append(TaskRevision(task_id, publication.put(task.to_bytes())))
    _, release = publication.publish(tasks=tuple(revisions))
    manifest = TrainingManifest(release, tuple((r.task_id, r.manifest) for r in revisions))
    after = source_at(tmp_path / "after", publication, inputs, manifest=manifest,
                      parent=head, parent_reader=source.run.objects)
    backup = json.loads((after.path.parent / "task_pool_resume.json").read_text())
    assert backup["changed_tasks"] == ["a"] and backup["sim_version_only"] == []
    assert backup["before"] == before
    pool = sync_state(after._pool_state(()), after._pool_tasks)
    assert pool["tasks"]["a"]["observed_groups"] == 0
    assert pool["tasks"]["b"] == before["tasks"]["b"]


def test_job_handoff_backs_up_pool_from_inputs_without_copying_weights(
        tmp_path, publication, inputs, monkeypatch):
    from behave.data.published import TaskManifest
    from tools.slime_training_job import backup_task_pool

    job = tmp_path / "job"
    source = source_at(job / "artifacts", publication, inputs)
    mock_scoring(monkeypatch, source)
    source.generate(0)
    completed_checkpoint(source, 0)
    output = tmp_path / "backup"
    destination = DirectoryStore(output / "definitions")
    pool = backup_task_pool(job, source.journal.load(), output, destination)
    assert json.loads((output / "task_pool_parent.json").read_text()) == source._pool_state(source.journal.load())
    for row in pool["tasks"].values():
        task = TaskManifest.from_bytes(destination.read(BlobRef.from_dict(row["revision"])))
        JudgePolicy.from_bytes(destination.read(task.judge_policy))
    assert not list(output.rglob("*.pt"))


def test_curriculum_requires_explicit_sampler(tmp_path, inputs):
    configuration = {**inputs[0], "curriculum": POLICY.as_dict()}
    manifest = TrainingManifest(BlobRef.of(b"release"), (("a", BlobRef.of(b"revision")),))
    with pytest.raises(ValueError, match="explicit sampler"):
        prepare_task_plan(tmp_path / "invalid", configuration, None, manifest)


def test_native_producer_receives_priority_order_without_resetting_cooldowns(
        tmp_path, publication, inputs, monkeypatch):
    from behave.train.pool import sync_state

    source = source_at(tmp_path, publication, inputs, tasks="abcd", replenishment={"max_groups": None},
                       curriculum=replace(POLICY, incomplete_priority_slots=1))
    source._parent_pool = sync_state(source._parent_pool, source._pool_tasks)
    for task in "abc":
        source._parent_pool["tasks"][task].update(observed_groups=1, last_selected=-2)
    source._parent_pool["tasks"]["c"]["cooldown_until"] = 10
    source._parent_pool["tasks"]["d"]["last_selected"] = -1
    state = mock_scoring(monkeypatch, source)
    assert source.generate(0) == "native batch"
    assert state["selection"][0] == {"task_id": "d", "reason": "incomplete-priority"}
    assert [row["task_id"] for row in state["selection"]] == ["d", "a", "b"]
    assert source._pool_state(source.journal.load())["tasks"]["c"]["cooldown_until"] == 10


def test_offline_reuse_does_not_count_as_new_mastery_evidence(tmp_path, publication, inputs, monkeypatch):
    source = source_at(tmp_path, publication, inputs)
    mock_scoring(monkeypatch, source)
    source.generate(0)
    prior = source._pool_state(source.journal.load())
    fields, refs = source._pool_commit(source.journal.load(), 1, None, None, fresh=False)
    assert refs and fields["task_pool"] == refs[0].as_dict()
    assert json.loads(source.run.objects.read(refs[0])) == prior


def test_small_eligible_reserve_is_shared_without_reintroducing_cooled_tasks(tmp_path, publication, inputs, monkeypatch):
    from behave.train.pool import sync_state

    source = source_at(tmp_path, publication, inputs, tasks="abcde", replenishment={"max_groups": None})
    source._parent_pool = sync_state(source._parent_pool, source._pool_tasks)
    for task in "abde":
        source._parent_pool["tasks"][task].update(category="mastered", cooldown_until=10)
    state = mock_scoring(monkeypatch, source)
    assert source.generate(0) == "native batch"
    assert state["selection"] == [{"task_id": "c", "reason": "new"}]
    assert source._pool_state(source.journal.load())["tasks"]["a"]["cooldown_until"] == 10


@pytest.mark.parametrize("replenishment", [None, {"max_groups": None}])
def test_exhausted_cooldown_pool_reports_without_rollout_and_can_rollback(tmp_path, publication, inputs,
                                                                       monkeypatch, replenishment):
    from behave.core.records import RecordError

    source = source_at(tmp_path, publication, inputs, tasks="ab", replenishment=replenishment)
    state = mock_scoring(monkeypatch, source)
    assert source.generate(0) == "native batch"
    completed_checkpoint(source, 0)
    before = source._pool_state(source.journal.load())
    state["version"] = 2
    with pytest.raises(RecordError, match="eligible groups") as caught:
        source.generate(1)
    assert caught.value.code == "training-task-pool-insufficient"
    assert len(state["requests"]) == 1
    events = source.journal.load()
    assert events[-1].kind == "task_pool_insufficient"
    assert events[-1].payload["available_groups"] == 0
    assert source._pool_state(events) == before
    assert [e.payload["round"] for e in events if e.kind == "round_started"] == [0]
    source.native.abandon(reason="fixture pool exhausted", workers_stopped=True)
    cold = ScoredDataSource(source.args)
    cold.load(0)
    assert cold._pool_state(cold.journal.load()) == before
