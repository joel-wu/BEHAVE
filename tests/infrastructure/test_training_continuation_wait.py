import json
from pathlib import Path

import pytest

from behave.train import jobs as job


def test_waits_for_parent_completion(tmp_path, monkeypatch):
    control = tmp_path / "control"
    control.mkdir()
    path = control / "status.json"
    path.write_text(json.dumps({"state": "supervising"}))
    waits = []

    def finish(seconds):
        waits.append(seconds)
        path.write_text(json.dumps({"state": "completed", "returncode": 0, "finished_at": "now"}))

    monkeypatch.setattr(job.time, "sleep", finish)
    job.wait_for_completion(tmp_path, poll_seconds=17)
    assert waits == [17]


def test_failed_parent_does_not_authorize_continuation(tmp_path):
    control = tmp_path / "control"
    control.mkdir()
    (control / "status.json").write_text(json.dumps(
        {"state": "failed", "returncode": 1, "finished_at": "now"}))
    with pytest.raises(RuntimeError, match="did not complete"):
        job.wait_for_completion(tmp_path, poll_seconds=17)


def test_transient_storage_error_keeps_waiting_for_parent(tmp_path, monkeypatch, capsys):
    path = tmp_path / "control/status.json"
    reads = iter([
        OSError(521, "shared storage unavailable"),
        {"state": "supervising"},
        {"state": "completed", "returncode": 0, "finished_at": "now"},
    ])
    original_read = Path.read_text
    waits = []

    def read_status(self, *args, **kwargs):
        if self != path:
            return original_read(self, *args, **kwargs)
        value = next(reads)
        if isinstance(value, OSError):
            raise value
        return json.dumps(value)

    monkeypatch.setattr(Path, "read_text", read_status)
    monkeypatch.setattr(job.time, "sleep", waits.append)
    job.wait_for_completion(tmp_path, poll_seconds=17)

    assert waits == [17, 17]
    assert "Parent status unavailable; still waiting" in capsys.readouterr().out


def completed_node(root):
    records = {
        "control/status.json": {"state": "supervising", "heartbeat_at": "2026-09-29T17:49:00+00:00"},
        "runtime.json": {"state": "completed", "host": "fixture-node", "first_update": 40,
                         "updated_at": "2026-09-29T20:00:00+00:00"},
        "node_status.json": {"state": "stopped", "host": "fixture-node",
                             "finished_at": "2026-09-29T20:00:01+00:00"},
        "configuration.json": {"initial_updates": 50, "resume_updates": 0},
        "artifacts/plan/result.json": {"native_cycle_completed": True,
                                       "rounds": [{"round": step} for step in range(40, 50)]},
    }
    for name, row in records.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row))
    marker = root / "artifacts/checkpoint/latest_checkpointed_iteration.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("49")
    return records


def test_completed_node_can_continue_without_rewriting_lost_login_supervisor(tmp_path, monkeypatch):
    completed_node(tmp_path)
    before = (tmp_path / "control/status.json").read_bytes()
    monkeypatch.setattr(job.time, "sleep", lambda *_: pytest.fail("completed node should not wait forever"))
    job.wait_for_completion(tmp_path, poll_seconds=17)
    assert (tmp_path / "control/status.json").read_bytes() == before


@pytest.mark.parametrize("path,changes", [
    ("runtime.json", {"state": "training"}),
    ("runtime.json", {"state": "interrupted"}),
    ("node_status.json", {"state": "live"}),
    ("node_status.json", {"host": "different-node"}),
    ("node_status.json", {"finished_at": "2026-09-29T19:59:59+00:00"}),
    ("artifacts/plan/result.json", {"native_cycle_completed": False}),
    ("artifacts/plan/result.json", {"rounds": [{"round": 49}]}),
    ("configuration.json", {"initial_updates": 60}),
])
def test_node_fallback_needs_complete_matching_evidence(tmp_path, path, changes):
    records = completed_node(tmp_path)
    (tmp_path / path).write_text(json.dumps({**records[path], **changes}))
    assert not job.node_completed(tmp_path)


def test_node_fallback_rejects_missing_or_stale_checkpoint(tmp_path):
    completed_node(tmp_path)
    marker = tmp_path / "artifacts/checkpoint/latest_checkpointed_iteration.txt"
    marker.write_text("48")
    assert not job.node_completed(tmp_path)
    marker.rename(marker.with_suffix(".retained"))
    assert not job.node_completed(tmp_path)


def test_recorded_parent_failure_is_not_overridden_by_node_fallback(tmp_path):
    completed_node(tmp_path)
    (tmp_path / "control/status.json").write_text(json.dumps(
        {"state": "failed", "returncode": 1, "finished_at": "now"}))
    with pytest.raises(RuntimeError, match="did not complete"):
        job.wait_for_completion(tmp_path, poll_seconds=17)
