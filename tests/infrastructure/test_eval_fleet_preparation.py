"""Resume interrupted metadata preparation through the real study interface."""

from pathlib import Path

import pytest
from test_eval_checkpoint_study import fixture

from behave.eval import checkpoint as checkpoint_module
from behave.eval import fleet as fleet_module
from behave.runtime.records import write_json
from tools import eval_checkpoint_study as study


def workspace(root):
    archive = root / "archive"
    fixture(archive)
    write_json(archive / "judge_policy.json", {})
    item = {"key": "main-5-3", "entry": {"model_line": "main", "update": 5}, "seed": 3,
            "allocation": 7, "directory": str(archive / "main/update-005/eval-seed-03")}
    settings = {"archive_root": str(archive), "code_directory": "code", "name": "launch",
        "nodes": {"7": {"generation_cpu_ids": list(range(32)), "legacy_generation": [],
                         "legacy_scoring": [], "scoring_enabled": False}},
        "rollout_workers_per_gpu": [1] * 8, "isolation_startup_slots": 4,
        "serving_probe_request": "fixture", "measurements": [item]}
    write_json(root / "fleet.json", settings)
    return item, {"allocation": 7, "gpu": 2}, {"model_path": "/fixture/model", "model_revision": "fixture"}


def one_controller_poll(root, monkeypatch):
    class EndPoll(Exception):
        pass

    monkeypatch.setattr(fleet_module.time, "sleep", lambda _: (_ for _ in ()).throw(EndPoll()))
    with pytest.raises(EndPoll):
        fleet_module.run(root)


def test_declared_larger_allocation_retains_owner_above_gpu_seven(tmp_path):
    item, slot, checkpoint = workspace(tmp_path)
    settings = study.read(tmp_path / "fleet.json")
    settings["nodes"]["7"].update(allocated_gpus=16, rollout_workers_per_gpu=[1] * 16)
    write_json(tmp_path / "fleet.json", settings)
    slot = {**slot, "gpu": 10}
    config = fleet_module.measurement_config(tmp_path, item, slot)
    assignment = fleet_module.begin_preparation(tmp_path, item, slot, config, checkpoint)
    assert fleet_module.saved_assignment(tmp_path, item) == assignment
    assert fleet_module.reconcile_assignments(tmp_path, settings, []) == [assignment]
    config["resources"]["allocated_gpus"] = 8
    with pytest.raises(ValueError, match="exceed allocated_gpus"):
        fleet_module._check_resources(slot, config)


@pytest.mark.parametrize("boundary", ["intent", "configuration", "queue", "checkpoint", "settings",
    "sampling", "trained", "serving", "manifest", "prepared", "submitted", "control_created", "input_link"])
def test_controller_resumes_same_preparation_without_resubmitting_a_created_control(tmp_path, monkeypatch, boundary):
    item, slot, checkpoint = workspace(tmp_path)
    lane = tmp_path / "generation" / item["key"]
    directory = Path(item["directory"])
    paths = {"configuration": lane / "configuration.json", "queue": lane / "queue.json",
        "checkpoint": directory.parent / "checkpoint.json", "settings": directory / "rollout/settings.json",
        "sampling": directory / "rollout/sampling.json", "trained": directory / "rollout/trained/configuration.json",
        "serving": directory / "serving.json", "manifest": directory / "manifest.json"}
    interrupted, submissions = [], []

    def write(path, value):
        write_json(path, value)
        owner_state = value.get("assignment", {}).get("launch_state") if isinstance(value, dict) else None
        hit = (path == paths.get(boundary) or (path == lane / "owner.json" and owner_state ==
            {"intent": "preparing", "prepared": "prepared", "submitted": "submitted"}.get(boundary)))
        if hit and not interrupted:
            interrupted.append(True)
            raise RuntimeError("controller interrupted")

    for module in (fleet_module, checkpoint_module):
        monkeypatch.setattr(module, "write_json", write)
    symlink = Path.symlink_to

    def link(path, target, *args, **kwargs):
        symlink(path, target, *args, **kwargs)
        if boundary == "input_link" and not interrupted:
            interrupted.append(True)
            raise RuntimeError("controller interrupted")

    monkeypatch.setattr(Path, "symlink_to", link)

    def submit(selected, index, **kwargs):
        assert selected == lane and index == 0
        control = directory / "control/launch"
        assert not control.exists(), "must never submit the same owner twice"
        submissions.append((selected, index))
        write_json(control / "launch.json", {"metadata": {}, "heartbeat_seconds": 60})
        write_json(control / "status.json", {"state": "supervising"})
        if boundary == "control_created" and not interrupted:
            interrupted.append(True)
            raise RuntimeError("controller interrupted")
        return {"state": "submitted"}

    monkeypatch.setattr(fleet_module, "submit_checkpoint", submit)
    with pytest.raises(RuntimeError, match="controller interrupted"):
        fleet_module.start_measurement(tmp_path, item, slot, checkpoint)
    assert interrupted
    # Already-written files must not be rewritten, even after a coordinator restart.
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths.values() if path.exists()}
    monkeypatch.setattr(fleet_module, "resolve_checkpoint", lambda _: pytest.fail("must retain saved checkpoint"))
    monkeypatch.setattr(fleet_module, "measurement_config", lambda *a, **kwargs: pytest.fail("must retain saved settings"))
    one_controller_poll(tmp_path, monkeypatch)
    assert len(submissions) == 1
    restored, = study.read(tmp_path / "assignments.json")
    assert restored["gpu"] == 2 and restored["allocation"] == 7
    assert restored["preparation"]["checkpoint"] == checkpoint
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before} == before
    settings = study.read(directory / "rollout/settings.json")
    assert settings["attempt"]["max_turns"] == 60 and settings["attempt"]["context_tokens"] == 131072
    assert settings["attempt_namespace"] == str(directory) and settings["score_on_submit"] is False
    assert study.read(directory / "serving.json")["spec"]["seed"] == 3
    assert study.read(tmp_path / "progress.json")["blocked"] == {}
    one_controller_poll(tmp_path, monkeypatch)
    assert len(submissions) == 1


@pytest.mark.parametrize("artifact", ["generation.json", "rollout/run", "rollout/candidate.json",
    "rollout/trained/unexpected", "control/another-owner", "rollout/.candidate.json.abcdefgh.tmp",
    ".manifest.json.unknown.tmp"])
def test_unknown_execution_artifacts_block_automatic_preparation(tmp_path, monkeypatch, artifact):
    item, slot, checkpoint = workspace(tmp_path)
    assignment = fleet_module.begin_preparation(tmp_path, item, slot,
        fleet_module.measurement_config(tmp_path, item, slot), checkpoint)
    path = Path(item["directory"]) / artifact
    write_json(path, {"retained": True})
    monkeypatch.setattr(fleet_module, "submit_checkpoint", lambda *a, **kwargs: pytest.fail("cannot submit ambiguous execution"))
    with pytest.raises(ValueError, match="possible execution"):
        fleet_module.continue_preparation(tmp_path, item, assignment, submit=fleet_module.submit_checkpoint)
    assert study.read(path) == {"retained": True}


@pytest.mark.parametrize("state", [None, "supervising", "failed", "completed", "unknown"])
def test_any_created_control_is_observation_only(tmp_path, monkeypatch, state):
    item, slot, checkpoint = workspace(tmp_path)
    assignment = fleet_module.begin_preparation(tmp_path, item, slot,
        fleet_module.measurement_config(tmp_path, item, slot), checkpoint)
    control = Path(assignment["control"])
    control.mkdir(parents=True)
    if state is not None:
        write_json(control / "status.json", {"state": state})
    monkeypatch.setattr(checkpoint_module, "prepare_measurement", lambda *a, **kw: pytest.fail("cannot reprepare a control"))
    monkeypatch.setattr(fleet_module, "submit_checkpoint", lambda *a, **kwargs: pytest.fail("cannot resubmit a control"))
    assert fleet_module.continue_preparation(tmp_path, item, assignment, submit=fleet_module.submit_checkpoint) == assignment


@pytest.mark.parametrize("changed", [*fleet_module.INPUTS, "code/snapshot.json"])
def test_changed_inputs_require_review_without_new_generation(tmp_path, monkeypatch, changed):
    item, slot, checkpoint = workspace(tmp_path)
    assignment = fleet_module.begin_preparation(tmp_path, item, slot,
        fleet_module.measurement_config(tmp_path, item, slot), checkpoint)
    write_json(tmp_path / "archive" / changed, {"changed": True})
    monkeypatch.setattr(fleet_module, "submit_checkpoint", lambda *a, **kwargs: pytest.fail("cannot change input identity"))
    one_controller_poll(tmp_path, monkeypatch)
    assert study.read(tmp_path / "progress.json")["blocked"] == {
        item["key"]: "preparation resume failed: ValueError"}
    assert not Path(assignment["control"]).exists()


def test_resume_does_not_overwrite_changed_prepared_contents(tmp_path, monkeypatch):
    item, slot, checkpoint = workspace(tmp_path)
    monkeypatch.setattr(fleet_module, "submit_checkpoint", lambda *a, **kwargs: (_ for _ in ()).throw(RuntimeError("before submit")))
    with pytest.raises(RuntimeError):
        fleet_module.start_measurement(tmp_path, item, slot, checkpoint)
    path = Path(item["directory"]) / "rollout/settings.json"
    saved = study.read(path)
    write_json(path, {**saved, "workers": 99})
    one_controller_poll(tmp_path, monkeypatch)
    assert study.read(path)["workers"] == 99
    assert study.read(tmp_path / "progress.json")["blocked"][item["key"]].endswith("ValueError")


def test_old_assignments_without_saved_intent_do_not_become_resumable(tmp_path):
    assert not fleet_module.resumable_preparation({"launch_state": "prepared", "control": str(tmp_path / "missing")})


@pytest.mark.parametrize("key,value", [("allocation", 8), ("gpu_count", 4), ("engines", 2),
    ("tensor_parallel_per_engine", 4), ("device_groups", ["0"])])
def test_single_gpu_owner_cannot_publish_a_different_resource_plan(tmp_path, key, value):
    item, slot, checkpoint = workspace(tmp_path)
    config = fleet_module.measurement_config(tmp_path, item, slot)
    config["resources"][key] = value
    with pytest.raises(ValueError, match="assigned GPU slot"):
        fleet_module.begin_preparation(tmp_path, item, slot, config, checkpoint)
    assert not (tmp_path / "generation" / item["key"] / "owner.json").exists()


@pytest.mark.parametrize("blocker", ["disabled", "training", "conflicting_owner"])
def test_resume_obeys_allocation_and_gpu_ownership(tmp_path, monkeypatch, blocker):
    item, slot, checkpoint = workspace(tmp_path)
    assignment = fleet_module.begin_preparation(tmp_path, item, slot,
        fleet_module.measurement_config(tmp_path, item, slot), checkpoint)
    config = study.read(tmp_path / "fleet.json")
    control = tmp_path / "other/control"
    write_json(control / "launch.json", {"metadata": {}, "heartbeat_seconds": 60})
    write_json(control / "status.json", {"state": "supervising"})
    if blocker == "disabled":
        config["nodes"]["7"]["generation_enabled"] = False
    elif blocker == "training":
        config["nodes"]["7"]["legacy_generation"] = [str(control)]
    else:
        write_json(tmp_path / "assignments.json", [assignment,
            {**slot, "key": "other", "control": str(control), "launch_state": "submitted"}])
    write_json(tmp_path / "fleet.json", config)
    monkeypatch.setattr(checkpoint_module, "prepare_measurement", lambda *a, **kw: pytest.fail("slot is unavailable"))
    monkeypatch.setattr(fleet_module, "submit_checkpoint", lambda *a, **kwargs: pytest.fail("slot is unavailable"))
    one_controller_poll(tmp_path, monkeypatch)
    assert not Path(assignment["control"]).exists()
    assert not (tmp_path / "generation" / item["key"] / "configuration.json").exists()
    progress = study.read(tmp_path / "progress.json")
    if blocker == "conflicting_owner":
        assert progress["blocked"][item["key"]] == "preparation slot has another unresolved owner"
    else:
        assert "7" in progress["blocked"]


def test_prepared_study_entry_itself_rejects_execution_evidence(tmp_path):
    fixture(tmp_path)
    entry = {"model_line": "main", "update": 5}
    checkpoint = {"model_path": "/fixture/model", "model_revision": "fixture"}
    output = study.prepare_measurement(tmp_path, entry, 3, checkpoint)
    write_json(output / "run/saved.json", {"retained": True})
    with pytest.raises(ValueError, match="possible execution"):
        study.prepare_measurement(tmp_path, entry, 3, checkpoint, resume_preparation=True)


@pytest.mark.parametrize("path", [".manifest.json.abcd1234.tmp", "rollout/.settings.json.abcd1234.tmp",
    "rollout/trained/.configuration.json.abcd1234.tmp"])
def test_uncommitted_atomic_record_is_not_promoted_or_removed(tmp_path, monkeypatch, path):
    item, slot, checkpoint = workspace(tmp_path)
    assignment = fleet_module.begin_preparation(tmp_path, item, slot,
        fleet_module.measurement_config(tmp_path, item, slot), checkpoint)
    staging = Path(item["directory"]) / path
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b'{"incomplete":')
    submitted = []
    monkeypatch.setattr(fleet_module, "submit_checkpoint", lambda *a, **kwargs: submitted.append(a) or {"state": "submitted"})
    result = fleet_module.continue_preparation(tmp_path, item, assignment, submit=fleet_module.submit_checkpoint)
    assert result["launch_state"] == "submitted" and len(submitted) == 1
    assert staging.read_bytes() == b'{"incomplete":'
    assert study.read(Path(item["directory"]) / "manifest.json")["checkpoint"] == checkpoint
