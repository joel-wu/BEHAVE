"""Metadata-only pool and feedback-window preparation; no rollout or Sim calls."""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.agent.process_model import ModelCommand
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.published import TaskManifest, TaskRevision
from behave.improve import feedback as feedback_module
from behave.improve.feedback import FeedbackWindow
from behave.task_execution.judge_policy import JudgePolicy
from behave.train.plan import TrainingManifest
from behave.train.pool import resume_pool


def pool_inputs(publication, count=3):
    policy = JudgePolicy("sim/old", "authority/old", seed="fixture",
                         baseline_cases=2, sequence_length=4, search_seconds=0)
    old_policy = publication.put(policy.to_bytes())
    new_policy = publication.put(replace(policy, command_version="sim/new",
                                         authority_version="authority/new").to_bytes())
    old_tasks, tasks = [], []
    for i in range(count):
        task = replace(publication.task, task_id=f"task-{i}", judge_policy=old_policy)
        old_tasks.append((task.task_id, publication.put(task.to_bytes())))
        tasks.append((task.task_id, publication.put(replace(task, judge_policy=new_policy).to_bytes())))
    state = {"tasks": {key: {"revision": ref.as_dict(), "cooldown_until": 17,
                            "observed_groups": 4, "history": [{"score": .5}]}
                       for key, ref in old_tasks}, "last_update": 9}
    return state, tuple(tasks), (old_policy, new_policy)


class CountingReader:
    def __init__(self, reader):
        self.reader, self.counts = reader, Counter()

    def read(self, reference):
        self.counts[reference] += 1
        return self.reader.read(reference)


def test_pool_reuses_only_policy_metadata_and_preserves_state(publication):
    state, tasks, policies = pool_inputs(publication, count=12)
    before = deepcopy(state)
    reader = CountingReader(publication.reader)
    actual, changes = resume_pool(state, tasks, reader, reader)
    expected = deepcopy(state)
    for key, ref in tasks:
        expected["tasks"][key]["revision"] = ref.as_dict()
    assert actual == expected and state == before
    assert changes == {"sim_version_only": [key for key, _ in tasks], "changed_tasks": []}
    assert all(reader.counts[ref] == 1 for ref in policies)
    expected_reads = {*policies, *(ref for _, ref in tasks),
                      *(BlobRef.from_dict(row["revision"]) for row in state["tasks"].values())}
    assert set(reader.counts) == expected_reads  # No source bodies, tokens or weights.
    actual["tasks"]["task-0"]["history"][0]["score"] = 0
    assert state == before


def test_unchanged_or_new_tasks_need_no_policy_reads(publication):
    state, tasks, _ = pool_inputs(publication)
    same = tuple((key, BlobRef.from_dict(row["revision"])) for key, row in state["tasks"].items())
    reader = CountingReader(publication.reader)
    actual, changes = resume_pool(state, (*same, ("not-in-pool", tasks[0][1])), reader, reader)
    assert actual == state and not reader.counts
    assert changes == {"sim_version_only": [], "changed_tasks": []}


@pytest.mark.parametrize("change", ["spec", "judge-settings"])
def test_each_changed_task_is_still_checked(publication, change):
    state, tasks, policies = pool_inputs(publication)
    before = deepcopy(state)
    key, ref = tasks[-1]
    task = TaskManifest.from_bytes(publication.reader.read(ref))
    if change == "spec":
        task = replace(task, spec=replace(task.spec, content=BlobRef.of(b"other computation")))
    else:
        policy = JudgePolicy.from_bytes(publication.reader.read(policies[1]))
        task = replace(task, judge_policy=publication.put(replace(policy, seed="other").to_bytes()))
    tasks = (*tasks[:-1], (key, publication.put(task.to_bytes())))
    actual, changes = resume_pool(state, tasks, publication.reader, publication.reader)
    assert changes == {"sim_version_only": ["task-0", "task-1"], "changed_tasks": [key]}
    assert actual["tasks"][key] == state["tasks"][key] and state == before


def test_equal_policy_references_are_verified_in_each_reader(publication):
    state, tasks, policies = pool_inputs(publication)
    # Same policy, different task metadata: both stores must authenticate it.
    changed = []
    for key, ref in tasks:
        task = TaskManifest.from_bytes(publication.reader.read(ref))
        changed.append((key, publication.put(replace(task, judge_policy=policies[0],
            spec=replace(task.spec, content=BlobRef.of(b"new problem"))).to_bytes())))
    first, second = CountingReader(publication.reader), CountingReader(publication.reader)
    _, changes = resume_pool(state, tuple(changed), first, second)
    assert changes["changed_tasks"] == [key for key, _ in tasks]
    assert first.counts[policies[0]] == second.counts[policies[0]] == 1

    class CorruptReader:
        def read(self, ref):
            return b"corrupt" if ref == policies[0] else publication.reader.read(ref)

    with pytest.raises(RecordError, match="content does not match"):
        resume_pool(state, tuple(changed), first, CorruptReader())


def test_next_resume_revalidates_policies(publication):
    state, tasks, policies = pool_inputs(publication)
    resume_pool(state, tasks, publication.reader, publication.reader)
    (publication.root / policies[1].sha256).write_bytes(b"!" * policies[1].size_bytes)
    with pytest.raises(RecordError, match="digest differs"):
        resume_pool(state, tasks, publication.reader, publication.reader)


def window_inputs(root, publication):
    """Write producer receipts consumed by the real preparation path."""
    state, tasks, _ = pool_inputs(publication)
    old_tasks = tuple((key, BlobRef.from_dict(row["revision"])) for key, row in state["tasks"].items())

    def manifest(revisions):
        _, release = publication.publish(tasks=tuple(TaskRevision(*item) for item in revisions))
        return publication.put(TrainingManifest(release, revisions).to_bytes())

    old, new = manifest(old_tasks), manifest(tasks)
    root.mkdir()
    prior = root / "old-binding.json"
    binding = root / "binding.json"
    prior.write_bytes(canonical_json({"manifest": old.as_dict(), "objects": str(publication.root)}))
    binding.write_bytes(canonical_json({"manifest": new.as_dict(), "objects": str(publication.root),
        "self_improvement": {"binding_overrides": {str(prior): str(binding)}}}))
    rounds = []
    model = ModelCommand(("/fixture/model-not-executed",), "fixture/1")
    for update in (1, 2, 3):
        run = JournalDirectory(root / f"round-{update}")
        checkpoint = run.objects.put(f"weights-{update}".encode())
        # Preparation reads only these producer fields, never executes a plan.
        plan = run.objects.put(canonical_json({"manifest": old.as_dict(),
            "requirements": {"checkpoint": checkpoint.as_dict()},
            "execution": {"jobs": [{"model": model.as_dict()}]}}))
        report = run.objects.put(canonical_json({"plan": plan.as_dict(),
            "execution_head": BlobRef.of(f"report-{update}".encode()).as_dict()}))
        run.journal("training").append((), "training_finished", {"result": report.as_dict()}, (report,))
        rounds.append((update, run.directory))
    return SimpleNamespace(binding=binding, rounds=tuple(rounds), old=old, new=new,
                           state=state, tasks=tasks)


def test_window_validates_immutable_membership_once_per_preparation(tmp_path, publication, monkeypatch):
    fixture = window_inputs(tmp_path / "fixture", publication)
    calls = []
    original = feedback_module.require_feedback_membership

    def validate(observed, approved, reader, **kwargs):
        calls.append((observed, approved))
        return original(observed, approved, reader, **kwargs)

    monkeypatch.setattr(feedback_module, "require_feedback_membership", validate)
    output = tmp_path / "prepared"
    actual = feedback_module.prepare_window(fixture.rounds, fixture.binding, output)
    assert calls == [(fixture.old, fixture.new)]
    window = FeedbackWindow.from_bytes(DirectoryStore(actual["objects"]).read(BlobRef.from_dict(actual["request"])))
    assert window.training_manifest == fixture.old  # Never relabel historical rewards.
    assert [step for step, _ in window.members] == [1, 2, 3]
    assert actual["new_rollouts"] == actual["new_scores"] == 0
    calls.clear()
    assert feedback_module.prepare_window(fixture.rounds, fixture.binding, output) == actual
    assert calls == [(fixture.old, fixture.new)]  # No cross-call approval cache.
    calls.clear()
    feedback_module.prepare_window(fixture.rounds, fixture.binding, tmp_path / "another")
    assert calls == [(fixture.old, fixture.new)]


@pytest.mark.parametrize("change", ["membership", "score-settings", "missing-policy"])
def test_window_rejects_changed_or_missing_approved_inputs(tmp_path, publication, change):
    fixture = window_inputs(tmp_path / "fixture", publication)
    current = TrainingManifest.from_bytes(publication.reader.read(fixture.new))
    if change == "membership":
        current = replace(current, tasks=current.tasks[:-1])
    else:
        key, ref = current.tasks[-1]
        task = TaskManifest.from_bytes(publication.reader.read(ref))
        policy = JudgePolicy.from_bytes(publication.reader.read(task.judge_policy))
        ref = (BlobRef.of(b"not present") if change == "missing-policy" else
               publication.put(replace(policy, search_seconds=2).to_bytes()))
        task = replace(task, judge_policy=ref)
        current = replace(current, tasks=(*current.tasks[:-1], (key, publication.put(task.to_bytes()))))
    row = parse_json(fixture.binding.read_bytes())
    row["manifest"] = publication.put(current.to_bytes()).as_dict()
    fixture.binding.write_bytes(canonical_json(row))
    output = tmp_path / "prepared"
    with pytest.raises(RecordError, match="operator-approved"):
        feedback_module.prepare_window(fixture.rounds, fixture.binding, output)
    assert not (output / "input.json").exists()


def test_window_rejects_later_membership_even_after_first_check(tmp_path, publication, monkeypatch):
    fixture = window_inputs(tmp_path / "fixture", publication)
    original = feedback_module.prepare

    def changed(directory, binding, output):
        result = original(directory, binding, output)
        return {**result, "training_manifest": fixture.new.as_dict()} if directory.name == "round-2" else result

    monkeypatch.setattr(feedback_module, "prepare", changed)
    output = tmp_path / "prepared"
    with pytest.raises(ValueError, match="different training memberships"):
        feedback_module.prepare_window(fixture.rounds, fixture.binding, output)
    assert not (output / "input.json").exists()
