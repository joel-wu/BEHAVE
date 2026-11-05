"""Multi-update feedback uses real report projections, with no model/EDA calls."""

from dataclasses import replace

import pytest
from test_evaluation import plan_for
from test_improvement_feedback import inputs as diagnostic_inputs
from test_improvement_feedback import report_at

from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json
from behave.improve.feedback import (
    FeedbackRequest,
    FeedbackWindow,
    export_feedback,
    read_feedback_request,
    select_feedback,
)


def window_fixture(tmp_path, publication):
    job, store, _, first = diagnostic_inputs(tmp_path, publication, ("pass", "fault", "partial"))
    members = [(1, store.put(first.to_bytes()))]
    for update, states in ((2, ("pass", "pass", "pass")), (3, ("fault", "cancelled", "unstarted"))):
        plan = replace(plan_for(*(replace(job, attempt=replace(job.attempt, attempt_id=f"u{update}-{i}"))
            for i in range(len(states)))), run_id=f"fixture-update-{update}")
        other, report = report_at(tmp_path / f"batch-{update}", publication, job, states, plan=plan)
        for path in other.directory.iterdir():
            store.put(path.read_bytes())
        # Same model transport deliberately: update identity must still split groups.
        member = replace(first, round_id=f"round-{update}",
                         checkpoint=store.put(f"weights-{update}".encode()), reports=(report.head,))
        members.append((update, store.put(member.to_bytes())))
    window = FeedbackWindow("window-1-3", first.training_manifest, tuple(members))
    store.put(window.to_bytes())
    return store, window


def test_window_preserves_groups_checkpoints_and_full_denominators(tmp_path, publication):
    store, window = window_fixture(tmp_path, publication)
    assert read_feedback_request(window.to_bytes()) == window
    bundle = export_feedback(window, store, approved_manifest=window.training_manifest)
    facts = bundle.facts
    assert facts["checkpoint"] is None
    assert len(facts["tasks"]) == 3
    assert [g["samples"] for g in facts["tasks"]] == [3, 3, 1]
    assert [g["planned_samples"] for g in facts["tasks"]] == [3, 3, 3]
    assert [g["complete"] for g in facts["tasks"]] == [True, True, False]
    assert len({g["context"] for g in facts["tasks"]}) == 3
    assert len({g["checkpoint"]["sha256"] for g in facts["tasks"]}) == 3
    assert [row["update"] for row in facts["window"]] == [1, 2, 3]
    summary = facts["window_summary"]
    assert summary["settled_reward_groups"] == {"different_rewards": 1, "all_1": 1, "other_same_reward": 0}
    assert summary["incomplete_groups"] == 1 and summary["excluded_attempts"] == 2
    assert summary["groups_with_unresolved_execution"] == 1
    assert summary["attempts"]["requested"] == 7
    assert export_feedback(window, store, approved_manifest=window.training_manifest, recorded_reports=True) == bundle
    data = b"\n".join(file.data for file in bundle.files)
    assert b"PRIVATE_REFERENCE_TRACE" not in data and b"UNSELECTED_PRIVATE_CONTEXT" not in data
    empty = select_feedback(facts, ())
    assert not empty["observations"] and not empty["tasks"]
    assert empty["window_summary"] == summary  # shared overview, not relabeled as shard totals


def test_window_rejects_duplicate_reports_wrong_membership_and_nonconsecutive_updates(tmp_path, publication):
    store, window = window_fixture(tmp_path, publication)
    first = FeedbackRequest.from_bytes(store.read(window.members[0][1]))
    repeated = store.put(replace(first, round_id="copy").to_bytes())
    bad = replace(window, members=(window.members[0], (2, repeated)))
    with pytest.raises(RecordError, match="appear twice"):
        export_feedback(bad, store, approved_manifest=window.training_manifest)
    with pytest.raises(RecordError, match="operator-approved"):
        export_feedback(window, store, approved_manifest=BlobRef.of(b"other"))
    changed = store.put(replace(first, training_manifest=BlobRef.of(b"other")).to_bytes())
    with pytest.raises(RecordError, match="mixes training memberships"):
        export_feedback(replace(window, members=((1, changed),)), store, approved_manifest=window.training_manifest)
    with pytest.raises(ValueError, match="consecutive"):
        replace(window, members=(window.members[0], window.members[2]))
    assert read_feedback_request(first.to_bytes()) == first  # legacy format unchanged


def test_window_shards_share_only_safe_overview(tmp_path, publication):
    from test_improvement_acquisition import configuration

    from behave.improve.acquisition import acquisition_view

    store, window = window_fixture(tmp_path / "feedback", publication)
    target, role, _, _ = configuration(tmp_path / "analyst", publication)
    for path in store.directory.iterdir():
        target.put(path.read_bytes())
    bundle = export_feedback(window, target, approved_manifest=window.training_manifest)
    for file in bundle.files:
        target.put(file.data)
    snapshot = target.put(WorkspaceSnapshot(tuple(file.reference for file in bundle.files)).to_bytes())
    _, facts, files, _ = acquisition_view(replace(role.inputs, feedback=snapshot, task_ids=()), target)
    assert not facts["observations"]
    names = {file.reference.path for file in files}
    assert {"window.json", "brief.md", "feedback.json"} <= names
    assert not any(path.startswith("observations/") for path in names)
    assert parse_json(next(file.data for file in files if file.reference.path == "window.json"))["summary"] == bundle.facts["window_summary"]


def test_window_prepare_indexes_existing_single_update_exports(tmp_path, monkeypatch):
    from behave.core.storage import DirectoryStore
    from behave.improve import feedback as feedback_module

    binding = tmp_path / "binding.json"
    manifest = BlobRef.of(b"manifest")
    binding.write_bytes(canonical_json({"manifest": manifest.as_dict(), "objects": "fixture"}))
    calls = []
    def prepare(directory, binding_path, output):
        calls.append(directory)
        store = DirectoryStore(output / "objects")
        ref = store.put(FeedbackRequest(directory.name, BlobRef.of(directory.name.encode()), manifest,
            "fixture", (BlobRef.of(f"report-{directory.name}".encode()),)).to_bytes())
        return {"request": ref.as_dict(), "objects": str(store.directory), "source_objects": ["existing-store"],
                "training_manifest": manifest.as_dict()}
    monkeypatch.setattr(feedback_module, "prepare", prepare)
    rounds = tuple((step, tmp_path / f"round-{step}") for step in (4, 5, 6))
    result = feedback_module.prepare_window(rounds, binding, tmp_path / "output")
    assert result["updates"] == [4, 5, 6] and result["new_rollouts"] == result["new_scores"] == 0
    assert result["source_objects"] == ["existing-store"] and len(calls) == 3
    assert feedback_module.prepare_window(rounds, binding, tmp_path / "output") == result
    with pytest.raises(ValueError, match="another recorded window"):
        feedback_module.prepare_window(rounds[1:], binding, tmp_path / "output")
    with pytest.raises(ValueError, match="consecutive"):
        feedback_module.prepare_window((), binding, tmp_path / "empty")
    assert not (tmp_path / "empty").exists()


def test_window_with_only_excluded_work_has_no_reward_group(tmp_path, publication):
    _, store, _, request = diagnostic_inputs(tmp_path, publication, ("cancelled", "unstarted"))
    window = FeedbackWindow("excluded", request.training_manifest, ((1, store.put(request.to_bytes())),))
    facts = export_feedback(window, store, approved_manifest=request.training_manifest).facts
    assert not facts["observations"] and not facts["tasks"]
    assert facts["window_summary"]["incomplete_groups"] == 1
    assert facts["window_summary"]["complete_groups"] == 0
    assert facts["window_summary"]["attempts"]["requested"] == 0
    assert not any(facts["window_summary"]["settled_reward_groups"].values())
    assert all(row["context"] in facts["contexts"] for row in facts["excluded_attempts"])


def test_recorded_feedback_accepts_only_sim_version_refresh_and_keeps_old_context(tmp_path, publication):
    from behave.data.published import ReleaseManifest, TaskManifest, TaskRevision
    from behave.task_execution.judge_policy import JudgePolicy
    from behave.train.plan import TrainingManifest

    store, window = window_fixture(tmp_path, publication)
    old = TrainingManifest.from_bytes(store.read(window.training_manifest))
    task_id, revision = old.tasks[0]
    task = TaskManifest.from_bytes(store.read(revision))
    policy = JudgePolicy.from_bytes(store.read(task.judge_policy))
    release = ReleaseManifest.from_bytes(store.read(old.release))

    def changed_manifest(new_task):
        ref = store.put(new_task.to_bytes())
        new_release = replace(release, tasks=(TaskRevision(task_id, ref),))
        return store.put(replace(old, release=store.put(new_release.to_bytes()),
                                 tasks=((task_id, ref),)).to_bytes())

    new_policy = replace(policy, command_version='sim/new', authority_version='authority/new')
    refreshed = changed_manifest(replace(task, judge_policy=store.put(new_policy.to_bytes())))
    original = export_feedback(window, store, approved_manifest=window.training_manifest, recorded_reports=True)
    actual = export_feedback(window, store, approved_manifest=refreshed, recorded_reports=True)
    assert actual == original  # Old rewards and judge contexts must not be relabeled.
    with pytest.raises(RecordError, match='operator-approved'):
        export_feedback(window, store, approved_manifest=refreshed)
    for changed in (replace(task, spec=replace(task.spec, content=store.put(b'new problem'))),
                    replace(task, judge_policy=store.put(replace(new_policy, search_seconds=5).to_bytes()))):
        with pytest.raises(RecordError, match='operator-approved'):
            export_feedback(window, store, approved_manifest=changed_manifest(changed), recorded_reports=True)
