"""Parent adoption and cold recovery use native save metadata, never models."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_slime_application import configured

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.train.slime_checkpoint import checkpoint_files
from behave.train.slime_data import ScoredDataSource
from behave.train.slime_launch import import_parent
from behave.train.trainer import NativeAnchor


def save_fixture(source, step):
    events = source.journal.load()
    source.journal.append(events, "round_scored", {"round": step, "binding": source.identity.as_dict()})
    root = Path(source.args.save)
    directory = root / f"iter_{step:07d}"
    directory.mkdir(parents=True)
    (root / "latest_checkpointed_iteration.txt").write_text(str(step))
    (directory / "state.pt").write_bytes(f"opaque native state at {step}".encode())
    source.save(step)
    return source.journal.seal()


def test_new_task_pool_adopts_parent_without_relaxing_same_run_identity(tmp_path):
    before_dir, after_dir = tmp_path / "before", tmp_path / "after"
    before_dir.mkdir()
    after_dir.mkdir()
    before = configured(before_dir)
    head, closure = save_fixture(before, 0)
    after = configured(after_dir)
    store = DirectoryStore(after.binding["objects"])
    store.publish(before.run.objects, closure)
    binding = {**after.binding, "schema": "behave.slime-rollout-binding/3", "parent_checkpoint": head.as_dict()}
    after.path.write_bytes(canonical_json(binding))
    after.args.behave_rollout_binding_sha256 = BlobRef.of(after.path.read_bytes()).sha256
    after.args.load = before.args.save
    resumed = ScoredDataSource(after.args)
    assert resumed.native.first_step == 1
    resumed.load(0)
    resumed.native.require_next((), 1, None)
    with pytest.raises(ValueError, match="stopping round"):
        resumed.native.require_next((), 0, None)
    with pytest.raises(ValueError, match="parent"):
        resumed.load(-1)
    # A parent is a separate transition, not permission to reuse its live run.
    foreign_events = before.journal.load()
    with pytest.raises(ValueError, match="another input binding"):
        resumed.native.require_next(foreign_events, 1, None)
    save_fixture(resumed, 1)
    resumed.args.load = resumed.args.save
    ScoredDataSource(resumed.args).load(1)
    assert before.native.first_step == 0


@pytest.mark.parametrize("change", ["not_saved", "wrong_batch", "wrong_step", "wrong_binding", "missing_files"])
def test_parent_requires_a_committed_matching_learning_batch(tmp_path, change):
    source = configured(tmp_path)
    save_fixture(source, 0)
    events = source.journal.load()
    if change == "not_saved":
        with pytest.raises(RecordError, match="committed"):
            NativeAnchor.load(source.run.objects, BlobRef.of(events[-2].to_bytes()))
        return
    row = thaw_json(events[-1].payload)
    if change == "wrong_batch":
        row["score"] = BlobRef.of(b"other batch").as_dict()
    elif change == "wrong_step":
        row["round"] = 1
    elif change == "wrong_binding":
        row["binding"] = BlobRef.of(b"other binding").as_dict()
    else:
        row["files"] = []
    forged = source.run.objects.put(replace(events[-1], payload=row).to_bytes())
    with pytest.raises(RecordError):
        NativeAnchor.load(source.run.objects, forged)


def test_native_anchor_checks_file_inventory_and_not_a_saved_host_path(tmp_path):
    source = configured(tmp_path)
    head, _ = save_fixture(source, 0)
    anchor = NativeAnchor.load(source.run.objects, head)
    assert anchor.step == 0
    source.native.anchor = anchor
    source.native._check_files(source.args.save, 0, [file.as_dict() for file in anchor.files])
    (Path(source.args.save) / "iter_0000000/state.pt").write_bytes(b"different weights")
    with pytest.raises(ValueError, match="file inventory differs"):
        source.native._check_files(source.args.save, 0, [file.as_dict() for file in anchor.files])


def test_parent_cannot_switch_between_raw_and_task_lineages(tmp_path):
    source = configured(tmp_path)
    head, _ = save_fixture(source, 0)
    copied = DirectoryStore(tmp_path / "copied")
    assert import_parent(source.run.objects, head, copied, route="task").route == "task"
    assert NativeAnchor.load(copied, head).step == 0
    with pytest.raises(ValueError, match="other learning route"):
        import_parent(source.run.objects, head, copied, route="raw")


def test_native_save_and_resume_never_read_state_contents(tmp_path, monkeypatch):
    import os

    source = configured(tmp_path)
    original = os.open

    def no_state_reads(path, flags, *args, **kwargs):
        if str(path).endswith("state.pt"):
            pytest.fail("native save/resume reread state contents")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr("behave.train.slime_checkpoint.os.open", no_state_reads)
    head, _ = save_fixture(source, 0)
    source.load(0)
    anchor = NativeAnchor.load(source.run.objects, head)
    assert [file.as_dict() for file in anchor.files] == [
        {"path": "iter_0000000/state.pt", "size_bytes": len(b"opaque native state at 0")}]


def test_legacy_hash_receipt_resumes_without_automatic_byte_audit(tmp_path, monkeypatch):
    source = configured(tmp_path)
    save_fixture(source, 0)
    events = source.journal.load()
    saved = events[-1]
    root = Path(source.args.save)
    hashed = checkpoint_files(root, 0)
    row = {**thaw_json(saved.payload), "files": [file.as_dict() for file in hashed]}
    legacy = source.run.objects.put(replace(saved, payload=row).to_bytes())
    anchor = NativeAnchor.load(source.run.objects, legacy)
    assert anchor.files[0].size_bytes == hashed[0].content.size_bytes

    # Ordinary recovery intentionally does not prove byte identity. An explicit
    # offline audit still detects a same-size replacement.
    state = root / "iter_0000000/state.pt"
    state.write_bytes(b"x" * state.stat().st_size)
    assert hashed != checkpoint_files(root, 0)
    monkeypatch.setattr("behave.train.slime_checkpoint._file_reference",
                        lambda *args: pytest.fail("automatic SHA256 audit"))
    source.native._check_files(root, 0, row["files"])
