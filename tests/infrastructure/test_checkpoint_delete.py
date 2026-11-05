"""Opt-in deletion tests use only tiny, test-owned temporary checkpoint files."""

import json
from pathlib import Path

import pytest
from test_native_updates import save_fixture
from test_slime_application import configured

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.train.checkpoint_storage import (
    checkpoint_delete_plan,
    delete_checkpoint_files,
    retain_checkpoints,
    retention_policy,
)
from behave.train.slime_data import ScoredDataSource

DELETE = {"keep_full": 1, "mode": "delete", "retire_parent": True}


def source_with_policy(tmp_path, policy=None):
    source = configured(tmp_path)
    source.path.write_bytes(canonical_json({**source.binding, "checkpoint_retention": policy or DELETE}))
    source.args.behave_rollout_binding_sha256 = BlobRef.of(source.path.read_bytes()).sha256
    return ScoredDataSource(source.args)


def test_delete_after_commit_keeps_hf_uncommitted_files_and_cold_recovery(tmp_path):
    source = source_with_policy(tmp_path)
    save_fixture(source, 0)
    root = Path(source.args.save)
    hf = root / "hf_0"
    hf.mkdir()
    (hf / "model.safetensors").write_bytes(b"HF stays")
    stray = root / "iter_0000999"
    stray.mkdir()
    (stray / "state.pt").write_bytes(b"uncommitted stays")
    save_fixture(source, 1)
    assert not (root / "iter_0000000").exists()
    assert (hf / "model.safetensors").read_bytes() == b"HF stays"
    assert (stray / "state.pt").read_bytes() == b"uncommitted stays"
    ScoredDataSource(source.args).load(1)
    done, = (root / "retention_receipts").glob("*.done.json")
    assert json.loads(done.read_bytes())["directory"] == str(root / "iter_0000000")
    assert retain_checkpoints(root, source.journal.load(), DELETE) is None


def test_failed_replacement_cannot_delete_the_last_checkpoint(tmp_path):
    source = source_with_policy(tmp_path)
    save_fixture(source, 0)
    source.journal.append(source.journal.load(), "round_scored", {"round": 1, "binding": source.identity.as_dict()})
    with pytest.raises(ValueError, match="not completed"):
        source.save(1)
    assert (Path(source.args.save) / "iter_0000000/state.pt").is_file()


@pytest.mark.parametrize("pin", ["marker", "policy", None])
def test_parent_retirement_only_after_child_save_with_explicit_fork_protection(tmp_path, pin):
    before_dir, after_dir = tmp_path / "before", tmp_path / "after"
    before_dir.mkdir()
    after_dir.mkdir()
    before = configured(before_dir)
    head, closure = save_fixture(before, 0)
    parent_dir = Path(before.args.save) / "iter_0000000"
    policy = dict(DELETE)
    if pin == "marker":
        (parent_dir.parent / "retention_pins.json").write_text('{"steps": [0]}')
    if pin == "policy":
        policy["protected_paths"] = [str(parent_dir)]
    after = source_with_policy(after_dir, policy)
    DirectoryStore(after.binding["objects"]).publish(before.run.objects, closure)
    after.path.write_bytes(canonical_json({**after.binding, "schema": "behave.slime-rollout-binding/3",
                                           "parent_checkpoint": head.as_dict()}))
    after.args.behave_rollout_binding_sha256 = BlobRef.of(after.path.read_bytes()).sha256
    after.args.load = before.args.save
    resumed = ScoredDataSource(after.args)
    resumed.load(0)
    assert parent_dir.exists()
    save_fixture(resumed, 1)
    assert parent_dir.exists() == (pin is not None)
    resumed.args.load = resumed.args.save
    ScoredDataSource(resumed.args).load(1)
    assert before.journal.load()[-1].kind == "checkpoint_saved"  # Evidence survives.


def fixture_files(tmp_path):
    directory = tmp_path / "checkpoints/iter_0000000"
    directory.mkdir(parents=True)
    for name in ("a.pt", "b.pt"):
        (directory / name).write_bytes(b"test weights")
    files = [{"path": name, "size_bytes": 12} for name in ("a.pt", "b.pt")]
    return directory, files, tmp_path / "receipts"


def test_cleanup_can_resume_only_its_own_partially_completed_file_manifest(tmp_path, monkeypatch):
    directory, files, receipts = fixture_files(tmp_path)
    unlink = Path.unlink
    def interrupt(path, *args, **kwargs):
        if path.name == "b.pt":
            raise OSError("simulated interruption")
        return unlink(path, *args, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", interrupt)
        with pytest.raises(OSError, match="interruption"):
            delete_checkpoint_files(directory, files, receipts)
    assert not (directory / "a.pt").exists() and (directory / "b.pt").is_file()
    delete_checkpoint_files(directory, files, receipts)
    assert not directory.exists()
    delete_checkpoint_files(directory, files, receipts)  # Receipt makes retries idempotent.


@pytest.mark.parametrize("change", ["missing", "extra", "link", "size", "directory_link", "empty_directory"])
def test_cleanup_refuses_unexpected_or_changed_entries_before_deleting_anything(tmp_path, change):
    directory, files, receipts = fixture_files(tmp_path)
    if change == "missing":
        (directory / "b.pt").unlink()
    elif change == "extra":
        (directory / "unknown").write_bytes(b"do not delete")
    elif change == "link":
        (directory / "b.pt").unlink()
        (directory / "b.pt").symlink_to(directory / "a.pt")
    elif change == "size":
        (directory / "b.pt").write_bytes(b"modified")
    elif change == "directory_link":
        (directory / "linked").symlink_to(tmp_path, target_is_directory=True)
    else:
        (directory / "unknown").mkdir()
    with pytest.raises(ValueError):
        delete_checkpoint_files(directory, files, receipts)
    assert (directory / "a.pt").read_bytes() == b"test weights"
    assert not list(receipts.glob("*.done.json"))


def test_deleted_file_replaced_after_a_receipted_interruption_is_rejected(tmp_path):
    from behave.core.storage import install_exclusive

    directory, files, receipts = fixture_files(tmp_path)
    receipt, record = checkpoint_delete_plan(directory, files, receipts)
    install_exclusive(receipts, receipt.name, canonical_json(record))
    # Keep the old inode allocated so this tests replacement deterministically,
    # even on filesystems with coarse timestamps and immediate inode reuse.
    (directory / "b.pt").rename(tmp_path / "replaced-b.pt")
    (directory / "b.pt").write_bytes(b"same length!")
    with pytest.raises(ValueError, match="changed"):
        delete_checkpoint_files(directory, files, receipts)
    assert (directory / "a.pt").is_file()


def test_replacement_inventory_rechecked_before_retiring_old_weights(tmp_path):
    source = configured(tmp_path)
    save_fixture(source, 0)
    save_fixture(source, 1)
    root = Path(source.args.save)
    (root / "iter_0000001/state.pt").write_bytes(b"corrupt replacement")
    with pytest.raises(ValueError, match="replacement checkpoint"):
        retain_checkpoints(root, source.journal.load(), DELETE)
    assert (root / "iter_0000000/state.pt").is_file()


@pytest.mark.parametrize("policy", [dict(DELETE, keep_full=0), dict(DELETE, retire_parent="yes"),
    dict(DELETE, protected_paths=["relative/iter_0000000"]), dict(DELETE, mode="unsafe")])
def test_delete_policy_rejects_unsafe_options(policy):
    with pytest.raises(ValueError):
        retention_policy(policy)
