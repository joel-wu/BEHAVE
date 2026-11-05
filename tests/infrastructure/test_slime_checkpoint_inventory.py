"""Bounded concurrent checkpoint hashing preserves the ordered byte inventory."""

import os
import threading

import pytest

from behave.core.artifacts import BlobRef
from behave.train import slime_checkpoint as slime_checkpoint_module


def checkpoint(tmp_path):
    (tmp_path / "latest_checkpointed_iteration.txt").write_text("0")
    directory = tmp_path / "iter_0000000"
    directory.mkdir()
    return directory


def test_parallel_shards_are_bounded_and_keep_canonical_order(tmp_path, monkeypatch):
    directory = checkpoint(tmp_path)
    for i in range(8):
        (directory / str(i)).write_bytes(bytes([i]) * 100)
    barrier = threading.Barrier(4)
    original = slime_checkpoint_module._file_reference

    def together(*args):
        barrier.wait(timeout=5)
        return original(*args)

    monkeypatch.setattr(slime_checkpoint_module, "_file_reference", together)
    result = slime_checkpoint_module.checkpoint_files(tmp_path, 0)
    assert [file.path for file in result] == [f"iter_0000000/{i}" for i in range(8)]
    assert [file.content for file in result] == [BlobRef.of(bytes([i]) * 100) for i in range(8)]


def test_native_checkpoint_has_no_shard_count_quota(tmp_path):
    directory = checkpoint(tmp_path)
    for i in range(4097):
        (directory / f"shard-{i:05d}").write_bytes(b"state")
    files = slime_checkpoint_module.checkpoint_files(tmp_path, 0)
    assert len(files) == 4097
    assert all(file.content == BlobRef.of(b"state") for file in files)


@pytest.mark.parametrize("kind", ("symlink", "fifo"))
def test_special_files_rejected_before_hashing(tmp_path, kind):
    directory = checkpoint(tmp_path)
    if kind == "symlink":
        (directory / "state").symlink_to(tmp_path / "latest_checkpointed_iteration.txt")
    else:
        os.mkfifo(directory / "state")
    with pytest.raises(ValueError, match="regular"):
        slime_checkpoint_module.checkpoint_files(tmp_path, 0)


def test_shard_replacement_after_inventory_is_rejected(tmp_path, monkeypatch):
    directory = checkpoint(tmp_path)
    (directory / "state").write_bytes(b"before")
    original = slime_checkpoint_module._file_reference

    def replace_shard(root, path, expected):
        replacement = directory / "replacement"
        replacement.write_bytes(b"after!")
        replacement.replace(path)
        return original(root, path, expected)

    monkeypatch.setattr(slime_checkpoint_module, "_file_reference", replace_shard)
    with pytest.raises(ValueError, match="changed"):
        slime_checkpoint_module.checkpoint_files(tmp_path, 0)
