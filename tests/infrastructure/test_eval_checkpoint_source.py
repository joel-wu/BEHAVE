"""Checkpoint polling observes commits, never hydrates or reads tensor bytes."""

import json
import os
from pathlib import Path

import pytest

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.eval import checkpoint as source
from behave.runtime import models as hf_archive
from behave.runtime.records import write_json


def native_model(job, *, update=6, indexed=True, committed=True):
    model = job / f"artifacts/plan/hf_{update - 1}"
    model.mkdir(parents=True)
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (model / name).write_text("{}")
    shard = "part-00001.safetensors" if indexed else "model.safetensors"
    (model / shard).write_bytes(b"fixture weights; not deserialized")
    if indexed:
        write_json(model / "model.safetensors.index.json", {"weight_map": {"w": shard}})
    if committed:
        journal = JournalDirectory(job / "artifacts/plan/rounds/native").journal("rounds")
        journal.append([], "checkpoint_saved", {"round": update - 1})
    return model


def cold_job(job):
    model = native_model(job)
    handoff = source.resolve_checkpoint({"update": 6, "jobs": [str(job)]})
    receipt = hf_archive.publish_model(model, handoff["weights"]["files"],
        checkpoint=handoff["checkpoint"], staging=job / "transport", destination=job / "cold")
    assert hf_archive.evict_model(model, receipt)
    return model, handoff, receipt


def deny_model_payloads(monkeypatch):
    original = Path.open
    original_descriptor = os.open

    def open_metadata(path, *args, **kwargs):
        assert path.suffix not in {".safetensors", ".tar"}, "polling must not read model bytes"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_metadata)
    def open_descriptor(path, *args, **kwargs):
        if not isinstance(path, int):
            assert Path(os.fsdecode(path)).suffix not in {".safetensors", ".tar"}
        return original_descriptor(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_descriptor)
    monkeypatch.setattr(hf_archive, "restore_model", lambda *a, **kw: pytest.fail("polling must not restore weights"))


@pytest.mark.parametrize("indexed", [False, True])
def test_hot_export_requires_commit_without_creating_a_journal(tmp_path, monkeypatch, indexed):
    model = native_model(tmp_path, indexed=indexed, committed=False)
    assert not source.checkpoint_ready(tmp_path, 6)
    assert not (tmp_path / "artifacts/plan/rounds").exists()
    journal = JournalDirectory(tmp_path / "artifacts/plan/rounds/native").journal("rounds")
    journal.append([], "checkpoint_saved", {"round": 5})
    deny_model_payloads(monkeypatch)
    assert source.checkpoint_ready(tmp_path, 6)
    result = source.resolve_checkpoint({"update": 6, "jobs": [str(tmp_path)]})
    assert result["step"] == 5 and result["model_path"] == str(model)
    assert not source.checkpoint_ready(tmp_path, 9)


def test_absent_job_is_an_observation_not_a_directory_creation(tmp_path):
    job = tmp_path / "not-created"
    assert not source.checkpoint_ready(job, 6)
    assert source.resolve_checkpoint({"update": 6, "jobs": [str(job)]}) is None
    assert not job.exists()


def test_readiness_observes_the_mapped_native_journal(tmp_path, monkeypatch):
    job = tmp_path / "old-job"
    native_model(job, committed=False)
    native = job / "artifacts/plan/rounds/native"
    hot = tmp_path / "hot-native"
    profile = tmp_path / "locations.json"
    write_json(profile, {"schema": "behave.runtime-locations/1",
        "mappings": [{"source": str(native), "destination": str(hot)}], "deny_unmapped_roots": []})
    monkeypatch.setenv("BEHAVE_RUNTIME_LOCATIONS", str(profile))
    journal = JournalDirectory(native).journal("rounds")
    assert journal.directory == hot / "rounds"
    journal.append([], "checkpoint_saved", {"round": 5}, (BlobRef.of(b"unread native tensor payload"),))
    deny_model_payloads(monkeypatch)
    assert source.checkpoint_ready(job, 6)
    assert not native.exists()


def test_verified_cold_handoff_needs_no_model_walk_or_restore(tmp_path, monkeypatch):
    model, handoff, _ = cold_job(tmp_path)
    deny_model_payloads(monkeypatch)
    monkeypatch.setattr(Path, "rglob", lambda *a, **kw: pytest.fail("cold polling uses its inventory receipt"))
    assert source.checkpoint_ready(tmp_path, 6)
    assert source.resolve_checkpoint({"update": 6, "jobs": [str(tmp_path)]}) == handoff
    assert not model.exists()


def test_recorded_migration_handoff_keeps_identity_and_waits_for_gate(tmp_path, monkeypatch):
    model, handoff, _ = cold_job(tmp_path)
    saved, ready = tmp_path / "handoff.json", tmp_path / "recovery.ready"
    write_json(saved, handoff)
    entry = {"update": 6, "recorded_handoff": str(saved), "recovery_ready": str(ready)}
    deny_model_payloads(monkeypatch)
    assert source.resolve_checkpoint(entry) is None
    ready.touch()
    assert source.resolve_checkpoint(entry) == handoff
    assert not model.exists()
    with pytest.raises(ValueError, match="another update"):
        source.resolve_checkpoint({**entry, "update": 7})
    write_json(saved, {**handoff, "model_revision": "changed"})
    with pytest.raises(ValueError, match="inventory differs"):
        source.resolve_checkpoint(entry)


def test_explicit_roots_deduplicate_aliases_but_not_distinct_checkpoint_owners(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    job = root / "original"
    native_model(job)
    alias = root / "alias"
    alias.symlink_to(job, target_is_directory=True)
    (root / "pending").mkdir()
    entry = {"update": 6, "jobs": [str(job), str(alias)], "job_roots": [str(root)]}
    deny_model_payloads(monkeypatch)
    result = source.resolve_checkpoint(entry)
    assert result["job"] == str(job.resolve())
    assert not (root / "pending/artifacts").exists()
    # Restoring the fixture writer is not a runtime repair or a source rewrite.
    monkeypatch.undo()
    native_model(root / "another-owner")
    deny_model_payloads(monkeypatch)
    with pytest.raises(ValueError, match="ambiguous checkpoint ownership"):
        source.resolve_checkpoint(entry)


def test_unverified_cold_receipt_is_not_ready(tmp_path):
    model, _, _ = cold_job(tmp_path)
    marker = hf_archive.marker(model)
    receipt = json.loads(marker.read_text())
    write_json(marker, {**receipt, "state": "unverified"})
    with pytest.raises(ValueError, match="receipt does not match"):
        source.checkpoint_ready(tmp_path, 6)
    assert not model.exists()


def test_node_stages_cold_weights_privately_with_the_same_handoff(tmp_path):
    model, handoff, receipt = cold_job(tmp_path)
    target = tmp_path / "node-scratch/model"
    target.parent.mkdir()
    assert source.stage_weights(handoff, target) == target
    assert (target / "part-00001.safetensors").read_bytes() == b"fixture weights; not deserialized"
    assert not model.exists() and Path(receipt["archive"]).is_file()
    assert BlobRef.from_dict(handoff["weights"]["checkpoint"]) == BlobRef.from_dict(handoff["checkpoint"])


def test_base_handoff_does_not_use_training_checkpoint_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "checkpoint_ready", lambda *a: pytest.fail("base is explicitly supplied"))
    entry = {"update": 0, "model_path": str(tmp_path), "model_revision": "base"}
    assert source.resolve_checkpoint(entry) == {"model_path": str(tmp_path), "model_revision": "base"}
    with pytest.raises(ValueError, match="base weights are unavailable"):
        source.resolve_checkpoint({**entry, "model_path": str(tmp_path / "missing")})
