"""Internal phase sharing must not weaken cold replay or export ownership."""

import os
from pathlib import Path

import pytest

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import DirectoryReader
from behave.core.records import RecordError, canonical_json
from behave.core.storage import DirectoryStore


def test_new_phases_share_one_cas_without_per_artifact_republication(tmp_path, monkeypatch):
    root = JournalDirectory(tmp_path / "round")
    refs = tuple(root.objects.put(f"evidence-{i}".encode()) for i in range(128))
    batch = JournalDirectory(root.directory / "batches/batch_000", objects=root.objects)
    data = JournalDirectory(batch.directory / "data", objects=batch.objects)
    publication = JournalDirectory(data.directory / "publication", objects=data.objects)
    link = os.link
    copied = []

    def observe(source, destination, *args, **kwargs):
        if Path(source).name in {r.sha256 for r in refs}:
            copied.append(source)
        return link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", observe)
    for phase in (batch, data, publication):
        phase.objects.publish_owned(root.objects, refs)
        assert phase.objects.directory == root.objects.directory
        assert not (phase.directory / "objects").exists()
        assert DirectoryReader(phase.directory / "objects").read(refs[-1]) == b"evidence-127"
    assert copied == []
    # Reopening uses the persisted owner, not process-local state or a symlink.
    assert JournalDirectory(data.directory).objects.directory == root.objects.directory


def test_export_is_independent_and_shared_owner_is_relocatable(tmp_path):
    root = JournalDirectory(tmp_path / "round")
    child = JournalDirectory(root.directory / "data", objects=root.objects)
    ref = child.objects.put(b"durable evidence")
    child.journal("phase").append((), "finished", {}, (ref,))
    head, closure = child.seal("phase")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish_owned(child.objects, closure)
    moved = tmp_path / "relocated"
    root.directory.rename(moved)
    reopened = JournalDirectory(moved / "data")
    assert reopened.journal("phase").load()[0].artifacts == (ref,)
    assert reopened.objects.read(ref) == exported.read(ref) == b"durable evidence"
    assert exported.read(head) == reopened.objects.read(head)


def test_legacy_phase_is_not_repointed_or_modified(tmp_path):
    root = JournalDirectory(tmp_path / "round")
    old = JournalDirectory(root.directory / "data")
    ref = old.objects.put(b"legacy")
    old.journal("phase").append((), "finished", {}, (ref,))
    reopened = JournalDirectory(old.directory, objects=root.objects)
    assert reopened.objects.directory == old.objects.directory
    assert not (old.directory / "objects.owner.json").exists()
    assert reopened.journal("phase").load()[0].artifacts == (ref,)


def test_legacy_journal_with_missing_objects_is_not_silently_rebound(tmp_path):
    root = JournalDirectory(tmp_path / "round")
    child = root.directory / "data"
    (child / "phase").mkdir(parents=True)
    reopened = JournalDirectory(child, objects=root.objects)
    assert reopened.objects.directory == child / "objects"
    assert not (child / "objects.owner.json").exists()


def test_other_ancestor_cannot_replace_a_bound_owner(tmp_path):
    root = JournalDirectory(tmp_path / "round")
    middle = JournalDirectory(root.directory / "batch")
    middle.objects.put(b"middle")
    child = JournalDirectory(middle.directory / "data", objects=middle.objects)
    with pytest.raises(RecordError, match="another object owner"):
        JournalDirectory(child.directory, objects=root.objects)


@pytest.mark.parametrize("owner", ["objects", "/tmp/objects", "../sibling/objects", "../wrong", "..\\objects"])
def test_invalid_owner_cannot_redirect_reads(tmp_path, owner):
    phase = tmp_path / "round/data"
    phase.mkdir(parents=True)
    (phase / "objects.owner.json").write_bytes(canonical_json(
        {"schema": "behave.object-store-owner/1", "owner": owner}))
    with pytest.raises(RecordError):
        DirectoryReader(phase / "objects")


def test_owner_receipt_symlink_is_rejected(tmp_path):
    phase = tmp_path / "round/data"
    phase.mkdir(parents=True)
    payload = tmp_path / "external.json"
    payload.write_bytes(canonical_json({"schema": "behave.object-store-owner/1", "owner": "../objects"}))
    (phase / "objects.owner.json").symlink_to(payload)
    with pytest.raises(OSError):
        DirectoryReader(phase / "objects")


def test_shared_payload_still_checks_size_and_digest(tmp_path):
    root = JournalDirectory(tmp_path / "round")
    child = JournalDirectory(root.directory / "data", objects=root.objects)
    ref = child.objects.put(b"good")
    path = root.objects.directory / ref.sha256
    path.chmod(0o644)
    path.write_bytes(b"evil")
    with pytest.raises(RecordError, match="digest"):
        DirectoryReader(child.directory / "objects").read(ref)
