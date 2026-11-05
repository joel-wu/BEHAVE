"""Publication faults never authorize deletion of the hot checkpoint."""

import json
import shutil
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event, current_thread

import pytest

from behave.runtime import models as archive


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / "model"
    source.mkdir()
    (source / "weights").write_bytes(b"weights")
    files = [{"path": "weights", "size_bytes": 7}]
    return source, files, {"checkpoint": {"sha256": "saved"},
                           "staging": tmp_path / "stage", "destination": tmp_path / "cold"}


def check_result(inputs, result):
    source, files, options = inputs
    assert (source / "weights").read_bytes() == b"weights"
    assert not list(options["staging"].iterdir())
    assert archive.digest(result["archive"]) == (result["sha256"], result["bytes"])
    with tarfile.open(result["archive"]) as packet:
        assert packet.extractfile("model/weights").read() == b"weights"
    assert archive.publish_model(source, files, **options) == result


def test_interrupted_pack_restarts(inputs, monkeypatch):
    source, files, options = inputs
    original = tarfile.TarFile.add

    def interrupted(tar, *args, **kwargs):
        original(tar, *args, **kwargs)
        raise OSError("packing interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(tarfile.TarFile, "add", interrupted)
        with pytest.raises(OSError, match="packing interrupted"):
            archive.publish_model(source, files, **options)
    assert not list(options["destination"].glob("*.json"))
    check_result(inputs, archive.publish_model(source, files, **options))


def test_interrupted_cold_copy_can_retry_sequentially(inputs, monkeypatch):
    source, files, options = inputs

    def interrupted(src, dst, length=0):
        dst.write(src.read(31))
        raise OSError("copy interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copyfileobj", interrupted)
        with pytest.raises(OSError, match="copy interrupted"):
            archive.publish_model(source, files, **options)
    assert not list(options["destination"].glob("*.json"))
    check_result(inputs, archive.publish_model(source, files, **options))


def test_retry_publishes_the_current_hot_bytes(inputs, monkeypatch):
    source, files, options = inputs
    original = archive.digest

    def interrupted(path):
        if Path(path).parent == options["destination"]:
            raise OSError("verification interrupted")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(archive, "digest", interrupted)
        with pytest.raises(OSError, match="verification interrupted"):
            archive.publish_model(source, files, **options)
    (source / "weights").write_bytes(b"changed")
    result = archive.publish_model(source, files, **options)
    with tarfile.open(result["archive"]) as packet:
        assert packet.extractfile("model/weights").read() == b"changed"


@pytest.mark.parametrize("after_commit", [False, True])
def test_receipt_commit_is_atomic_and_retry_does_not_recopy(inputs, monkeypatch, after_commit):
    source, files, options = inputs
    replace = Path.replace

    def interrupted(path, target):
        if Path(target).parent == options["destination"] and Path(target).suffix == ".json":
            if after_commit:
                replace(path, target)
            raise OSError("receipt interrupted")
        return replace(path, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", interrupted)
        with pytest.raises(OSError, match="receipt interrupted"):
            archive.publish_model(source, files, **options)
    receipts = list(options["destination"].glob("*.json"))
    assert len(receipts) == int(after_commit)
    if receipts:
        assert json.loads(receipts[0].read_text())["state"] == "verified"
    with monkeypatch.context() as patch:
        if after_commit:
            patch.setattr(shutil, "copyfileobj", lambda *a, **kw: pytest.fail("committed copy must be reused"))
        result = archive.publish_model(source, files, **options)
    check_result(inputs, result)


def test_packet_left_after_commit_is_removed_on_retry(inputs, monkeypatch):
    source, files, options = inputs
    unlink = Path.unlink

    def interrupted(path, *args, **kwargs):
        committed = list(options["destination"].glob("*.json"))
        if path.parent == options["staging"] and path.suffix == ".tar" and committed:
            raise OSError("release interrupted")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(OSError, match="release interrupted"):
            archive.publish_model(source, files, **options)
    assert list(options["staging"].glob("*.tar"))
    check_result(inputs, archive.publish_model(source, files, **options))


def test_concurrent_publishers_share_one_commit(inputs):
    source, files, options = inputs
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: archive.publish_model(source, files, **options), range(2)))
    assert results[0] == results[1]
    check_result(inputs, results[0])


@pytest.mark.parametrize("shared_locks", [False, True])
def test_different_hot_copies_cannot_replace_a_committed_archive(inputs, monkeypatch, shared_locks):
    import os

    source, files, options = inputs
    if shared_locks:
        options = {**options, "lock_directory": source.parent / "shared-locks"}
    second_source = source.with_name("second-model")
    second_source.mkdir()
    (second_source / "weights").write_bytes(b"weights")
    # Identical weights, but distinct tar metadata, as on two staging nodes.
    os.utime(source / "weights", (1700000000, 1700000000))
    os.utime(second_source / "weights", (1700000100, 1700000100))
    second_options = {**options, "staging": source.parent / "second-stage"}
    ready, release = Event(), Event()
    new_file = archive._new_file

    @contextmanager
    def paused(path):
        if path.parent == options["destination"] and current_thread().name.endswith("_0"):
            ready.set()
            assert release.wait(10), "test did not release the first publisher"
        with new_file(path) as stream:
            yield stream

    monkeypatch.setattr(archive, "_new_file", paused)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="publisher") as pool:
        first = pool.submit(archive.publish_model, source, files, **options)
        try:
            assert ready.wait(5), "first publisher never reached cold publication"
            second = pool.submit(archive.publish_model, second_source, files, **second_options)
            try:
                second.result(timeout=1)
            except (TimeoutError, ValueError):
                pass
        finally:
            release.set()
        result = first.result(timeout=5)
        with pytest.raises(ValueError, match="cold receipt differs"):
            second.result(timeout=5)
    check_result((source, files, options), result)
    assert (second_source / "weights").read_bytes() == b"weights"


def test_unavailable_publication_lock_never_falls_back_to_unlocked_writes(inputs, monkeypatch):
    import errno

    source, files, options = inputs
    lock = archive.fcntl.flock
    calls = 0

    def unavailable(stream, operation):
        nonlocal calls
        calls += 1
        if calls == 2:  # Source lock works; the destination lock is unsupported.
            raise OSError(errno.EOPNOTSUPP, "shared publication lock unavailable")
        return lock(stream, operation)

    monkeypatch.setattr(archive.fcntl, "flock", unavailable)
    with pytest.raises(OSError, match="shared publication lock unavailable"):
        archive.publish_model(source, files, **options)
    assert (source / "weights").read_bytes() == b"weights"
    assert not list(options["destination"].glob("*.tar"))
    assert not list(options["destination"].glob("*.json"))


def test_publication_lock_cannot_live_inside_model(inputs):
    source, files, options = inputs
    with pytest.raises(ValueError, match="outside the model"):
        archive.publish_model(source, files, lock_directory=source / "locks", **options)
    assert not (source / "locks").exists()


def test_cold_io_is_sequential_without_scans_hardlinks_or_big_renames(inputs, monkeypatch):
    import os

    source, files, options = inputs
    replace, open_file = Path.replace, Path.open
    reads = []

    def metadata_rename(path, target):
        assert path.suffix != ".tar" and Path(target).suffix != ".tar"
        return replace(path, target)

    def observed_open(path, mode="r", *args, **kwargs):
        if path.parent == options["destination"] and mode == "rb":
            reads.append(path)
        return open_file(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        for method in ("iterdir", "rglob", "glob"):
            patch.setattr(Path, method, lambda *a, **kw: pytest.fail("publication must use its inventory"))
        patch.setattr(os, "link", lambda *a, **kw: pytest.fail("cold filesystem need not support hardlinks"))
        patch.setattr(Path, "replace", metadata_rename)
        patch.setattr(Path, "open", observed_open)
        result = archive.publish_model(source, files, **options)
    assert reads == [Path(result["archive"])]
    check_result(inputs, result)


def test_abrupt_process_exit_during_copy_is_resumable(inputs):
    source, files, options = inputs
    script = '''
import os, pathlib, shutil, sys
from behave.runtime.models import publish_model
def abrupt_copy(src, dst, length=0):
    dst.write(src.read(31))
    dst.flush()
    os.fsync(dst.fileno())
    os._exit(91)
shutil.copyfileobj = abrupt_copy
publish_model(pathlib.Path(sys.argv[1]), [{'path': 'weights', 'size_bytes': 7}],
              checkpoint={'sha256': 'saved'}, staging=pathlib.Path(sys.argv[2]),
              destination=pathlib.Path(sys.argv[3]))
'''
    child = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(source),
                            str(options["staging"]), str(options["destination"])],
                           cwd=source.parent, capture_output=True, timeout=20, check=False)
    assert child.returncode == 91, child.stderr.decode(errors="replace")
    assert not list(options["destination"].glob("*.json"))
    check_result(inputs, archive.publish_model(source, files, **options))


def test_separate_destinations_publish_independently(inputs):
    source, files, options = inputs
    first = archive.publish_model(source, files, **options)
    changed = {**options, "destination": options["destination"].with_name("second-cold")}
    second = archive.publish_model(source, files, **changed)
    assert first["archive"] != second["archive"]
    check_result(inputs, first)
    check_result((source, files, changed), second)
