"""Controller interruption must not erase or duplicate a measurement owner."""

import json
from pathlib import Path

import pytest

from behave.eval import checkpoint as checkpoint_module
from behave.eval import fleet as ownership
from behave.runtime import jobs
from behave.runtime.records import write_json


def measurement(root, key="task", seed=3):
    item = {
        "key": key,
        "entry": {"model_line": "main", "update": 5},
        "seed": seed,
        "directory": str(root / "archive" / key),
        "allocation": 7,
    }
    row = {
        "key": key,
        "allocation": 7,
        "gpu": 2,
        "control": str(Path(item["directory"]) / "control/first"),
        "launch_state": "prepared",
    }
    return item, row


def test_reconciliation_repairs_only_the_index_and_retains_legacy_rows(tmp_path):
    item, row = measurement(tmp_path)
    other = {"key": "old", "allocation": 7, "gpu": 1, "control": "/old/control", "note": "retained"}
    ownership.record_assignment(tmp_path, item, row)
    active = [other]
    restored = ownership.reconcile_assignments(tmp_path, {"measurements": [item]}, active)
    assert restored == [other, row] and active == [other]
    assert not Path(row["control"]).exists()  # Reconciliation never submits.
    assert not (tmp_path / "assignments.json").exists()  # Caller owns publication.
    assert ownership.reconcile_assignments(tmp_path, {"measurements": [item]}, restored) == restored
    assert ownership.reconcile_assignments(tmp_path, {"measurements": []}, active) == active


def test_matching_index_keeps_additional_annotations(tmp_path):
    item, row = measurement(tmp_path)
    ownership.record_assignment(tmp_path, item, row)
    active = [{**row, "note": "operator evidence"}]
    assert ownership.reconcile_assignments(tmp_path, {"measurements": [item]}, active) == active


def test_only_an_explicit_previous_owner_allows_replacement(tmp_path):
    item, old = measurement(tmp_path)
    new = {**old, "control": str(Path(item["directory"]) / "control/retry")}
    ownership.record_assignment(tmp_path, item, old)
    with pytest.raises(ValueError, match="previous assignment"):
        ownership.record_assignment(tmp_path, item, new)
    ownership.record_assignment(tmp_path, item, new, previous=old)
    submitted = {**new, "launch_state": "submitted"}
    ownership.record_assignment(tmp_path, item, submitted)
    assert ownership.reconcile_assignments(tmp_path, {"measurements": [item]}, [old]) == [submitted]
    record = json.loads((tmp_path / "generation/task/owner.json").read_text())
    assert record["previous"] == old
    with pytest.raises(ValueError, match="ambiguous"):
        ownership.reconcile_assignments(
            tmp_path, {"measurements": [item]}, [{**old, "control": str(Path(item["directory"]) / "control/foreign")}]
        )


@pytest.mark.parametrize("change", ["seed", "entry", "directory", "allocation"])
def test_receipt_cannot_be_reinterpreted_as_another_measurement(tmp_path, change):
    item, row = measurement(tmp_path)
    ownership.record_assignment(tmp_path, item, row)
    edited = {
        **item,
        change: {
            "seed": 42,
            "entry": {"model_line": "main", "update": 10},
            "directory": str(tmp_path / "elsewhere"),
            "allocation": 8,
        }[change],
    }
    with pytest.raises(ValueError, match="measurement identity"):
        ownership.reconcile_assignments(tmp_path, {"measurements": [edited]}, [])


def test_receipts_cannot_relocate_or_escape_the_measurement(tmp_path):
    item, row = measurement(tmp_path)
    ownership.record_assignment(tmp_path, item, row)
    with pytest.raises(ValueError, match="relocate"):
        ownership.record_assignment(tmp_path, item, {**row, "gpu": 3}, previous=row)
    with pytest.raises(ValueError, match="outside"):
        ownership.record_assignment(tmp_path, item, {**row, "control": "/other/control"})
    with pytest.raises(ValueError, match="simple directory"):
        ownership.reconcile_assignments(tmp_path, {"measurements": [{**item, "key": "../other"}]}, [])


def test_duplicate_controls_and_queue_keys_are_rejected(tmp_path):
    item, row = measurement(tmp_path)
    other = {**row, "key": "other"}
    with pytest.raises(ValueError, match="multiple measurements"):
        ownership.reconcile_assignments(tmp_path, {"measurements": []}, [row, other])
    with pytest.raises(ValueError, match="duplicate queued"):
        ownership.reconcile_assignments(tmp_path, {"measurements": [item, item]}, [])


def test_bad_measurement_key_is_rejected_before_preparation_writes(tmp_path, monkeypatch):
    from behave.eval import fleet as fleet_module

    monkeypatch.setattr(fleet_module, "measurement_config", lambda *a, **kwargs: pytest.fail("must reject before preparation"))
    with pytest.raises(ValueError, match="simple directory"):
        fleet_module.start_measurement(tmp_path, {"key": "../escaped"}, {}, {})
    assert not list(tmp_path.iterdir())


def test_missing_or_malformed_receipt_is_unknown_not_terminal(tmp_path):
    from behave.eval import fleet as fleet_module

    assert ownership.observe_control(tmp_path)["state"] == "unknown"
    assert not fleet_module.terminal(tmp_path)
    write_json(tmp_path / "launch.json", {"metadata": {}, "heartbeat_seconds": 60})
    (tmp_path / "status.json").write_text("{")
    assert ownership.observe_control(tmp_path)["state"] == "unknown"
    assert not fleet_module.terminal(tmp_path)


def test_stale_heartbeat_does_not_end_worker_ownership(tmp_path):
    from behave.eval import fleet as fleet_module

    write_json(tmp_path / "launch.json", {"metadata": {}, "heartbeat_seconds": 60})
    write_json(tmp_path / "status.json", {"state": "supervising", "heartbeat_at": "2000-01-01T00:00:00+00:00"})
    state = ownership.observe_control(tmp_path)
    assert state["heartbeat_overdue"] is True and state["state"] == "supervising"
    assert not fleet_module.terminal(tmp_path)


@pytest.mark.parametrize("failure", ["during_prepare", "after_submit", "after_submit_failed"])
@pytest.mark.parametrize("other_ready", [False, True])
def test_controller_restart_keeps_interrupted_launch_reserved_without_resubmission(
    tmp_path, monkeypatch, failure, other_ready
):
    from test_eval_checkpoint_study import fixture

    from behave.eval import fleet as fleet_module
    from tools import eval_checkpoint_study as study

    archive = tmp_path / "archive"
    fixture(archive)
    write_json(archive / "judge_policy.json", {})
    item = {
        "key": "main-5-3",
        "entry": {"model_line": "main", "update": 5},
        "seed": 3,
        "allocation": 7,
        "directory": str(archive / "main/update-005/eval-seed-03"),
    }
    config = {
        "archive_root": str(archive),
        "code_directory": "code",
        "name": "launch",
        "nodes": {
            "7": {
                "generation_cpu_ids": list(range(32)),
                "legacy_generation": [],
                "legacy_scoring": [],
                "scoring_enabled": False,
            }
        },
        "rollout_workers_per_gpu": [1] * 8,
        "isolation_startup_slots": 4,
        "serving_probe_request": "fixture",
        "measurements": [item],
    }
    write_json(tmp_path / "fleet.json", config)
    slot = {"allocation": 7, "gpu": 2}
    handoff = {"model_path": "/fixture/model", "model_revision": "fixture"}
    calls = []

    def submit(lane, index, **kwargs):
        # Simulate a worker launched just before its controller disappears.
        calls.append((lane, index))
        control = study.control_directory(lane, {**item["entry"], "inference_seeds": [3]})
        write_json(control / "launch.json", {"metadata": {}, "heartbeat_seconds": 60})
        write_json(
            control / "status.json",
            {
                "state": "failed" if failure == "after_submit_failed" else "supervising",
                "heartbeat_at": "2000-01-01T00:00:00+00:00",
            },
        )
        raise RuntimeError("controller interrupted")

    if failure == "during_prepare":
        monkeypatch.setattr(checkpoint_module, "prepare_measurement", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("controller interrupted"))
        )
    monkeypatch.setattr(fleet_module, "submit_checkpoint", submit)
    with pytest.raises(RuntimeError, match="controller interrupted"):
        fleet_module.start_measurement(tmp_path, item, slot, handoff)
    assert not (tmp_path / "assignments.json").exists()
    assert len(calls) == (0 if failure == "during_prepare" else 1)
    scheduled = []
    if other_ready:
        other = {**item, "key": "main-5-42", "seed": 42, "directory": str(archive / "main/update-005/eval-seed-42")}
        config["measurements"].append(other)
        write_json(tmp_path / "fleet.json", config)
        monkeypatch.setattr(fleet_module, "resolve_checkpoint", lambda _: handoff)

    def start(root, queued, selected_slot, checkpoint, *, cwd=None):
        assert other_ready and queued["key"] == "main-5-42"
        assert selected_slot["gpu"] != slot["gpu"]
        scheduled.append(queued)
        return {
            **selected_slot,
            "key": queued["key"],
            "launch_state": "submitted",
            "control": str(Path(queued["directory"]) / "control/launch"),
        }

    monkeypatch.setattr(fleet_module, "start_measurement", start)
    monkeypatch.setattr(jobs, "submit", lambda *a, **kw: pytest.fail("no real job submission"))

    class EndPoll(Exception):
        pass

    monkeypatch.setattr(fleet_module.time, "sleep", lambda _: (_ for _ in ()).throw(EndPoll()))
    with pytest.raises(EndPoll):
        fleet_module.run(tmp_path)
    saved = study.read(tmp_path / "assignments.json")
    assert len(saved) == 1 + int(other_ready) and saved[0]["gpu"] == 2 and saved[0]["key"] == item["key"]
    assert len(scheduled) == int(other_ready)
    progress = study.read(tmp_path / "progress.json")
    assert progress["generation_busy_gpus"] == 1 + int(other_ready)
    expected_blocked = {
        "during_prepare": {item["key"]: "preparation resume failed: RuntimeError"},
        "after_submit": {},
        "after_submit_failed": {item["key"]: "failed"},
    }
    assert progress["blocked"] == expected_blocked[failure]
    assert len(progress["running"]) == (1 if failure == "after_submit" else 0)


@pytest.mark.parametrize(
    "state,terminal",
    [
        ("submitted", False),
        ("starting", False),
        ("supervising", False),
        ("missing", False),
        ("unknown", False),
        ("unrecognized", False),
        ("completed", True),
        ("failed", True),
        ("timed-out", True),
        ("launch-error", True),
    ],
)
def test_only_explicit_terminal_states_release_a_phase_wait(tmp_path, state, terminal):
    from behave.eval import fleet as fleet_module

    write_json(tmp_path / "launch.json", {"metadata": {}, "heartbeat_seconds": 60})
    write_json(tmp_path / "status.json", {"state": state})
    assert fleet_module.terminal(tmp_path) is terminal
