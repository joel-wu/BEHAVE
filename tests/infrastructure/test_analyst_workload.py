"""Bound Analyst reading work without losing feedback or multiplying proposals."""

from collections import Counter
from dataclasses import replace

import pytest
from test_attempts import Scripted, call
from test_improvement_acquisition import configuration, material
from test_improvement_feedback_window import window_fixture

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import parse_json
from behave.core.storage import DirectoryStore
from behave.data.role import RoleJob, input_closure
from behave.improve.acquisition import (
    AcquisitionConfiguration,
    acquisition_assignments,
    acquisition_view,
    feedback_task_ids,
    load_acquired_materials,
    run_acquisition,
    task_shards,
    workload_limits,
)
from behave.improve.acquisition_records import AcquisitionInputs
from behave.improve.analyst_feedback import compact_feedback
from behave.improve.feedback import export_feedback

LIMITS = {"max_tasks": 3, "max_observations": 24}


def test_shards_are_deterministic_keep_updates_together_and_preserve_incomplete_tasks():
    observations = [{"task_id": f"task-{i}", "update": update}
                    for i in range(7) for update in (1, 2) for _ in range(4)]
    observations += [{"task_id": "oversized", "update": 3} for _ in range(25)]
    tasks = ("task-0", "oversized", *(f"task-{i}" for i in range(1, 7)), "cancelled", "unstarted")
    facts = {"observations": observations}
    shards = task_shards(facts, tasks, LIMITS)
    assert shards == task_shards(facts, tasks, LIMITS)
    assert Counter(t for shard in shards for t in shard) == Counter(tasks)
    weights = Counter(row["task_id"] for row in observations)
    assert len(shards) == 4  # Cancellation-only tasks do not create extra Analysts.
    for shard in shards:
        observed = [task for task in shard if weights[task]]
        assert len(observed) <= 3
        assert sum(weights[t] for t in shard) <= 24 or observed == ["oversized"]
    assert task_shards({"observations": []}, (), LIMITS) == ((),)
    assert task_shards({"observations": []}, ("cancelled", "unstarted"), LIMITS) == (("cancelled", "unstarted"),)


@pytest.mark.parametrize("bad", [{"max_tasks": 0, "max_observations": 24},
    {"max_tasks": True, "max_observations": 24}, {"max_tasks": 3, "max_observations": -1}])
def test_invalid_limits_rejected(bad):
    with pytest.raises(ValueError):
        workload_limits(bad)


def test_inputs_preserve_legacy_encoding_and_validate_new_options():
    ref = BlobRef.of(b"fixture")
    old = AcquisitionInputs(ref, ref, ref)
    assert set(old.as_dict()) == {"role", "library", "feedback", "guidance"}
    assert AcquisitionInputs.from_dict(old.as_dict()) == old
    new = replace(old, feedback_view="compact-v1", proposal_target=0)
    assert AcquisitionInputs.from_dict(new.as_dict()) == new
    with pytest.raises(ValueError):
        replace(old, proposal_target=True)
    with pytest.raises(ValueError):
        replace(old, feedback_view="unknown")


def test_bounded_nonwindow_feedback_also_retains_incomplete_groups():
    facts = {"tasks": [{"task_id": "partial", "complete": False}],
             "excluded_attempts": [{"task_id": "unstarted"}]}
    assert feedback_task_ids(facts) == ()
    assert feedback_task_ids(facts, include_incomplete=True) == ("partial", "unstarted")


def test_compact_view_preserves_all_details_and_group_denominators(tmp_path, publication):
    store, window = window_fixture(tmp_path, publication)
    facts = export_feedback(window, store, approved_manifest=window.training_manifest).facts
    files = {file.reference.path: file.data for file in compact_feedback(facts)}
    compact = parse_json(files["feedback.json"])
    assert compact["tasks"] == facts["tasks"]
    assert compact["counts"] == facts["counts"]
    assert compact["window"] == facts["window"]
    assert [parse_json(files[row["details"]]) for row in compact["observations"]] == facts["observations"]
    excluded = compact["excluded_summary"]
    assert excluded["count"] == sum(group["count"] for group in excluded["groups"]) == 2
    assert [parse_json(row) for row in files[excluded["details"]].splitlines()] == facts["excluded_attempts"]
    assert "excluded_attempts" not in compact
    assert len(feedback_task_ids(facts)) == 1  # All three updates stay with the same Analyst.


@pytest.mark.parametrize("total", [2, 30])
def test_assignment_targets_do_not_multiply_with_shards(tmp_path, publication, total):
    store, role, _, _ = configuration(tmp_path, publication)
    template = AcquisitionConfiguration(replace(role, inputs=replace(role.inputs, proposal_target=total)))
    task_ids = tuple(f"task-{i}" for i in range(10))
    facts = {"observations": [{"task_id": task} for task in task_ids for _ in range(8)]}
    kwargs = dict(library=role.inputs.library, feedback=role.inputs.feedback, round_id="new-round",
                  policy=role.attempt.model_policy, model=role.model)
    assigned = acquisition_assignments((template,), facts, task_ids, limits=LIMITS, **kwargs)
    assert len(assigned) == 4
    assert sum(config.job.inputs.proposal_target for config in assigned) == total
    assert all(config.job.inputs.feedback_view == "compact-v1" for config in assigned)
    assert Counter(task for config in assigned for task in config.job.inputs.task_ids) == Counter(task_ids)
    assert len({config.job.attempt.attempt_id for config in assigned}) == 4
    old = acquisition_assignments((AcquisitionConfiguration(role),), facts, task_ids, **kwargs)
    assert len(old) == 1 and old[0].job.inputs.task_ids is None
    assert old[0].job.inputs.feedback_view is old[0].job.inputs.proposal_target is None
    if total == 2:
        zero = next(config for config in assigned if config.job.inputs.proposal_target == 0)
        _, _, files, _ = acquisition_view(zero.job.inputs, store)
        guide = next(file.data for file in files if file.reference.path == "acquisition_guidance.md")
        assert b"analysis/experience only" in guide and b"materials=[]" in guide


def test_compact_role_supports_transfer_cold_replay_and_unchanged_handoff(tmp_path, publication):
    store, role, rows, observation = configuration(tmp_path, publication)
    role = replace(role, inputs=replace(role.inputs, feedback_view="compact-v1", proposal_target=2))
    assert RoleJob.from_bytes(role.to_bytes()) == role
    exported = DirectoryStore(tmp_path / "export-input")
    exported.publish(store, input_closure(role, store))
    _, facts, files, _ = acquisition_view(role.inputs, exported)
    entry = parse_json(next(file.data for file in files if file.reference.path == "feedback.json"))
    assert entry["observations"][0]["id"] == observation
    arguments = material(rows[0]["id"], observation=observation)
    journal = JournalDirectory(tmp_path / "attempt").journal("role")
    backend = Scripted(call("submit_materials", arguments))
    result = run_acquisition(inputs=role.inputs, reader=exported, journal=journal,
                             backend=backend, spec=role.attempt)
    assert result.reason == "final_action"
    assert "about 2 useful task proposals" in str(backend.requests[0].messages)
    head, closure = journal.seal()
    cold = DirectoryStore(tmp_path / "cold")
    cold.publish(journal.objects, tuple(dict.fromkeys((*closure, *input_closure(role, exported)))))
    materials = load_acquired_materials(role.inputs, cold, head)
    assert materials["materials"] == arguments["materials"]
    assert run_acquisition(inputs=role.inputs, reader=cold, journal=journal,
        backend=Scripted(), spec=role.attempt) == result
    # An empty task assignment cannot expose another shard's candidate or observation record.
    _, selected, subset, _ = acquisition_view(replace(role.inputs, task_ids=()), exported)
    assert selected["observations"] == []
    assert not any(file.reference.path.startswith("feedback/observations/") for file in subset)
    assert not any(file.reference.path in facts["observations"][0]["attachments"] for file in subset)
