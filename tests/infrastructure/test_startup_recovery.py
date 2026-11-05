import json

import pytest

from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.train.pool import inherited_handoff


@pytest.fixture
def run(tmp_path, monkeypatch):
    root = tmp_path / "run"
    (root / "artifacts/plan").mkdir(parents=True)
    objects = DirectoryStore(root / "recovery_objects")
    checkpoint = {"sha256": "a" * 64, "size_bytes": 1}
    manifest = {"sha256": "b" * 64, "size_bytes": 1}
    record = {"schema": "behave.rollout-handoff/1", "workers_stopped": True,
              "round": 48, "checkpoint": checkpoint, "manifest": manifest}
    descriptor = {"objects": str(objects.directory), "record": objects.put(canonical_json(record)).as_dict()}
    rows = {"configuration.json": {}, "continuation.json": {"checkpoint": checkpoint,
            "first_update": 48, "rollout_recovery": descriptor},
            "runtime.json": {"state": "interrupted", "phases": [{"report": {"process": {"cleanup": "complete"}}}]},
            "artifacts/plan/binding.json": {"parent_checkpoint": checkpoint, "manifest": manifest,
                                           "rollout_recovery": record}}
    for name, row in rows.items():
        (root / name).write_text(json.dumps(row))
    monkeypatch.setattr("behave.train.slime_rollout.validate_execution_change", lambda *a: None)
    return root, descriptor


def test_pre_round_retry_retains_exact_descriptor_without_republishing(run, monkeypatch):
    root, descriptor = run
    monkeypatch.setattr(DirectoryStore, "publish_owned", lambda *a, **kw: pytest.fail("no republication"))
    assert inherited_handoff(root, {}, completed=48) == descriptor


@pytest.mark.parametrize("cleanup", ["unknown", "incomplete"])
def test_incomplete_cleanup_cannot_authorize_startup_retry(run, cleanup):
    root, _ = run
    (root / "runtime.json").write_text(json.dumps({"state": "interrupted",
        "phases": [{"report": {"process": {"cleanup": cleanup}}}]}))
    with pytest.raises(ValueError, match="complete cleanup"):
        inherited_handoff(root, {}, completed=48)


def test_changed_checkpoint_boundary_cannot_reuse_imported_candidates(run):
    with pytest.raises(ValueError, match="checkpoint/policy"):
        inherited_handoff(run[0], {}, completed=49)


def test_started_round_requires_normal_recovery(run, monkeypatch):
    monkeypatch.setattr("behave.agent.journal.AttemptJournal.load", lambda *a, **kw: [object()])
    assert inherited_handoff(run[0], {}, completed=48) is None
