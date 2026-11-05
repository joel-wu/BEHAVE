"""Shared immutable input publication is distinct from mutable status writes."""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from behave.core.records import canonical_json
from behave.runtime.records import publish_bytes, publish_json


def test_matching_json_reuses_bytes_inode_and_timestamp(tmp_path):
    target = tmp_path / "record.json"
    value = {"label": "训练", "count": 2}
    publish_json(target, value)
    before = target.stat()
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda _: publish_json(target, value), range(16)))
    assert target.read_bytes() == canonical_json(value)
    assert (target.stat().st_ino, target.stat().st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    with pytest.raises(ValueError, match="different experiment record"):
        publish_json(target, {"count": 3})
    assert target.read_bytes() == canonical_json(value)
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("kind", ["symlink", "dangling", "directory", "fifo"])
def test_existing_nonregular_record_is_not_followed_or_replaced(tmp_path, kind):
    target, outside = tmp_path / "record", tmp_path / "outside"
    if kind == "symlink":
        outside.write_bytes(b"retained")
        target.symlink_to(outside)
    elif kind == "dangling":
        target.symlink_to(outside)
    elif kind == "directory":
        target.mkdir()
    else:
        os.mkfifo(target)
    before = target.lstat()
    with pytest.raises((OSError, ValueError)):
        publish_bytes(target, b"retained")
    assert target.lstat().st_ino == before.st_ino
    if kind == "symlink":
        assert outside.read_bytes() == b"retained"
    elif kind == "dangling":
        assert not outside.exists()


def test_parent_fsync_failure_can_resume_the_already_linked_record(tmp_path, monkeypatch):
    import stat

    from behave.core import storage

    target = tmp_path / "record"
    fsync = storage.os.fsync

    def interrupted(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("fixture directory fsync interrupted")
        fsync(fd)

    with monkeypatch.context() as fault:
        fault.setattr(storage.os, "fsync", interrupted)
        with pytest.raises(OSError, match="fsync interrupted"):
            publish_bytes(target, b"retained")
    assert target.read_bytes() == b"retained"
    before = target.stat()
    publish_bytes(target, b"retained")
    assert target.stat().st_ino == before.st_ino
    assert list(tmp_path.iterdir()) == [target]
