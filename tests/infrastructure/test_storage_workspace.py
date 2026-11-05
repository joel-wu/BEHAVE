from __future__ import annotations

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from behave.agent.journal import AttemptJournal
from behave.agent.run_store import JournalDirectory
from behave.agent.workspace import Workspace, WorkspaceSnapshot
from behave.core.artifacts import BlobRef, FileData, FileRef
from behave.core.records import RecordError, canonical_json, metadata
from behave.core.storage import DirectoryStore, install_exclusive


def test_journal_owner_name_does_not_conflict_with_lock_file(tmp_path):
    run = JournalDirectory(tmp_path / "run")
    with run.owner():
        journal = run.journal("owner")
        event = journal.append((), "initialized", {})
        assert journal.load() == (event,)
        assert (run.directory / "owner.lock").is_file()
        assert journal.directory.is_dir()
    with pytest.raises(ValueError, match="reserved"):
        run.journal("objects")


def test_run_owner_releases_on_body_failure_without_replacing_lock(tmp_path):
    run = JournalDirectory(tmp_path / "run")
    with pytest.raises(RuntimeError, match="fixture failure"), run.owner():
        inode = (run.directory / "owner.lock").stat().st_ino
        raise RuntimeError("fixture failure")
    with JournalDirectory(run.directory).owner():
        assert (run.directory / "owner.lock").stat().st_ino == inode


def test_seal_preserves_current_prefix_without_marking_it_terminal(tmp_path):
    from behave.agent.journal import load_chain

    run = JournalDirectory(tmp_path / "run")
    assert not run.directory.exists()  # Constructing the view does not start a run.
    assert run.seal("solver") == (None, ())
    journal = run.journal("solver")
    artifact = run.objects.put(b"input")
    first = journal.append((), "initialized", {}, (artifact,))
    head, closure = run.seal("solver")
    assert head == BlobRef.of(first.to_bytes())
    assert closure == (artifact, head)
    second = journal.append((first,), "terminal", {})
    assert journal.load() == (first, second)
    copied = DirectoryStore(tmp_path / "export")
    copied.publish(run.objects, closure)
    assert load_chain(copied, head) == (first,)


def test_existing_content_is_synced_and_verified_without_rewriting(tmp_path, monkeypatch):
    store = DirectoryStore(tmp_path / "objects")
    ref = store.put(b"existing weight chunk")
    opened, synced = [], []
    original_open, original_sync = os.open, os.fsync

    def observed_open(path, flags, *args, **kwargs):
        opened.append(flags)
        return original_open(path, flags, *args, **kwargs)

    def observed_sync(fd):
        synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        return original_sync(fd)

    monkeypatch.setattr(os, "open", observed_open)
    monkeypatch.setattr(os, "fsync", observed_sync)
    assert store.put(b"existing weight chunk") == ref
    store.publish(store, (ref,))
    assert not any(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT) for flags in opened)
    assert synced == [True, True]  # Includes a concurrent writer's newly linked name.
    target = store.directory / ref.sha256
    target.chmod(0o600)
    target.write_bytes(b"corrupt! weight chunk")
    with pytest.raises(RecordError, match="differs"):
        store.put(b"existing weight chunk")
    assert target.read_bytes() == b"corrupt! weight chunk"


@pytest.mark.parametrize("kind", ["fifo", "directory", "dangling-link"])
def test_existing_noncontent_is_never_replaced(tmp_path, kind):
    store = DirectoryStore(tmp_path)
    target = tmp_path / BlobRef.of(b"content").sha256
    if kind == "fifo":
        os.mkfifo(target)
    elif kind == "directory":
        target.mkdir()
    else:
        target.symlink_to(tmp_path / "absent")
    before = target.lstat()
    with pytest.raises(RecordError):
        store.put(b"content")
    assert target.lstat().st_ino == before.st_ino
    assert not tuple(tmp_path.glob(".pending-*"))


def test_concurrent_first_writers_still_use_exclusive_link(tmp_path, monkeypatch):
    writers, barrier = 4, Barrier(4)
    original = os.link

    def simultaneous_link(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(os, "link", simultaneous_link)
    store = DirectoryStore(tmp_path / "objects")
    with ThreadPoolExecutor(max_workers=writers) as pool:
        refs = tuple(pool.map(store.put, (b"same chunk",) * writers))
    assert len(set(refs)) == 1 and store.read(refs[0]) == b"same chunk"
    assert len(tuple(store.directory.iterdir())) == 1


def test_existing_content_sync_failure_is_not_success(tmp_path, monkeypatch):
    store = DirectoryStore(tmp_path / "objects")
    ref = store.put(b"existing chunk")

    def failed_sync(_fd):
        raise OSError("fixture fsync failure")

    monkeypatch.setattr(os, "fsync", failed_sync)
    with pytest.raises(OSError, match="fixture fsync failure"):
        store.put(b"existing chunk")
    assert store.read(ref) == b"existing chunk"


def test_publication_closes_directory_even_if_temp_cleanup_fails(tmp_path, monkeypatch):
    descriptors = []

    def failed_unlink(name, *, dir_fd):
        descriptors.append(dir_fd)
        raise PermissionError("fixture cleanup failed")

    monkeypatch.setattr(os, "unlink", failed_unlink)
    with pytest.raises(PermissionError, match="fixture cleanup failed"):
        install_exclusive(tmp_path / "objects", "result", b"content")
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert (tmp_path / "objects/result").read_bytes() == b"content"


def test_owned_publication_links_durable_objects_without_readback(tmp_path, monkeypatch):
    source, target = DirectoryStore(tmp_path / "source"), DirectoryStore(tmp_path / "target")
    ref = source.put(b"producer-owned data")
    monkeypatch.setattr(source, "read", lambda *_: pytest.fail("owned publication reread payload"))
    target.publish_owned(source, (ref, ref))
    target.publish_owned(source, (ref,))
    assert (source.directory / ref.sha256).stat().st_ino == (target.directory / ref.sha256).stat().st_ino
    source.directory.rename(tmp_path / "retained-source")
    assert target.read(ref) == b"producer-owned data"


def test_owned_publication_uses_explicit_io_workers(tmp_path, monkeypatch):
    from threading import Barrier

    source, target = DirectoryStore(tmp_path / "source"), DirectoryStore(tmp_path / "target")
    refs = tuple(source.put(value) for value in (b"first", b"second"))
    rendezvous = Barrier(2)
    original = os.link

    def together(*args, **kwargs):
        rendezvous.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(os, "link", together)
    target.publish_owned(source, refs, workers=2)
    assert tuple(target.read(ref) for ref in refs) == (b"first", b"second")


@pytest.mark.parametrize("workers", [0, -1, True, 1.5, "4"])
def test_publication_requires_positive_integer_worker_budget(tmp_path, workers):
    store = DirectoryStore(tmp_path)
    with pytest.raises(ValueError, match="positive integer"):
        store.publish(store, (), workers=workers)


def test_content_publication_is_exclusive_idempotent_and_verified(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    with ThreadPoolExecutor(max_workers=8) as pool:
        references = tuple(pool.map(store.put, [b"same bytes"] * 32))
    assert len(set(references)) == 1
    assert store.read(references[0]) == b"same bytes"
    assert list(store.directory.iterdir()) == [store.directory / references[0].sha256]
    target = store.directory / references[0].sha256
    target.chmod(0o600)
    target.write_bytes(b"wrongbytes")
    with pytest.raises(RecordError, match="differs"):
        store.put(b"same bytes")
    assert target.read_bytes() == b"wrongbytes"


def test_exclusive_install_and_copy_retries_never_overwrite(tmp_path):
    assert install_exclusive(tmp_path / "head", "result", b"one")
    assert not install_exclusive(tmp_path / "head", "result", b"two")
    assert (tmp_path / "head/result").read_bytes() == b"one"
    source, target = DirectoryStore(tmp_path / "local"), DirectoryStore(tmp_path / "persistent")
    refs = tuple(source.put(f"file {i}".encode()) for i in range(40))
    target.publish(source, refs[:20], workers=4)
    target.publish(source, refs, workers=8)
    assert tuple(target.read(ref) for ref in refs) == tuple(source.read(ref) for ref in refs)
    with pytest.raises(ValueError):
        target.publish(source, refs, workers=0)


def test_content_writer_refuses_symlinks_and_oversized_objects(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    (tmp_path / "link").symlink_to(actual, target_is_directory=True)
    with pytest.raises(OSError):
        DirectoryStore(tmp_path / "link").put(b"no")
    store = DirectoryStore(actual)
    victim = tmp_path / "victim"
    victim.write_bytes(b"old")
    (actual / BlobRef.of(b"new").sha256).symlink_to(victim)
    with pytest.raises(RecordError):
        store.put(b"new")
    assert victim.read_bytes() == b"old"
    ref = store.put(b"larger than the former four-byte test quota")
    assert store.read(ref) == b"larger than the former four-byte test quota"


@pytest.mark.parametrize("custom_root", [False, True])
def test_workspace_history_is_immutable_and_materializes_on_this_nodes_tmp(tmp_path, monkeypatch, custom_root):
    store = DirectoryStore(tmp_path / "nfs-objects")
    workspace = Workspace(store)
    workspace.write("nested/model.py", b"VALUE = 1\n")
    original, dependencies = workspace.checkpoint()
    workspace.write("nested/model.py", b"VALUE = 2\n")
    workspace.write("other.bin", b"\x00\xff")
    workspace.restore(original)
    assert workspace.read("nested/model.py") == b"VALUE = 1\n"
    assert len(workspace.snapshot().files) == 1
    monkeypatch.setenv("TMPDIR", str(tmp_path / "must-not-use"))
    monkeypatch.delenv("BEHAVE_SCRATCH_DIR", raising=False)
    expected_root = Path("/tmp")
    if custom_root:
        expected_root = tmp_path / "local"
        expected_root.mkdir()
        monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(expected_root))
    with workspace.materialize() as path:
        assert path.parent == expected_root
        assert (path / "nested/model.py").read_bytes() == b"VALUE = 1\n"
        # Even a trusted caller changing a materialization cannot rewrite capture.
        (path / "nested/model.py").write_bytes(b"different")
    assert not path.exists()
    assert workspace.read("nested/model.py") == b"VALUE = 1\n"
    assert dependencies == (original, BlobRef.of(b"VALUE = 1\n"))
    clone = Workspace(store)
    clone.restore(original)
    assert clone.snapshot() == workspace.snapshot()


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "x/../y", "x//y", "x\\y"])
def test_workspace_has_no_host_path_mutation(tmp_path, path):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    with pytest.raises(RecordError):
        workspace.write(path, b"no")
    assert not workspace.snapshot().files


@pytest.mark.parametrize("existing,conflict", [("a", "a/b"), ("a/b", "a")])
def test_workspace_path_conflicts_are_atomic(tmp_path, existing, conflict):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    workspace.write(existing, b"original")
    first = workspace.snapshot()
    with pytest.raises(RecordError, match="overlap"):
        workspace.write(conflict, b"new")
    assert workspace.snapshot() == first
    workspace.write("b", b"new")
    workspace.delete("b")
    assert workspace.snapshot() == first
    with pytest.raises(RecordError):
        workspace.delete("missing")


def test_workspace_storage_failure_does_not_replace_live_index(tmp_path, monkeypatch):
    store = DirectoryStore(tmp_path / "objects")
    workspace = Workspace(store)
    workspace.write("a", b"1234")
    first = workspace.snapshot()

    def fail(data):
        raise OSError("fixture: storage unavailable")

    monkeypatch.setattr(store, "put", fail)
    for path in ("a", "b"):
        with pytest.raises(OSError, match="storage unavailable"):
            workspace.write(path, b"replacement")
        assert workspace.snapshot() == first


def test_workspace_seed_write_restore_and_materialize_without_source_quotas(tmp_path, monkeypatch):
    from behave.data.drafts import load_workspace

    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(tmp_path))
    store = DirectoryStore(tmp_path / "objects")
    workspace = Workspace(store)
    large = b"a" * (17 << 20)
    tiny = FileData(FileRef("small/0.py", BlobRef.of(b"x")), b"x")
    workspace.seed((FileData(FileRef("large/0.py", BlobRef.of(large)), large),
                    *(FileData(FileRef(f"small/{i}.py", tiny.reference.content), tiny.data) for i in range(1024))))
    for i in range(1, 4):
        workspace.write(f"large/{i}.py", bytes([97 + i]) * len(large))
    snapshot = workspace.snapshot()
    assert len(snapshot.files) > 1024
    assert sum(f.content.size_bytes for f in snapshot.files) > 64 << 20
    root, _ = workspace.checkpoint()
    clone = Workspace(store)
    clone.restore(root)
    assert clone.snapshot() == snapshot
    assert tuple(f.reference for f in load_workspace(store, root)) == snapshot.files
    with clone.materialize() as directory:
        assert (directory / "large/0.py").read_bytes() == large
        assert len(list(directory.rglob("*.py"))) == len(snapshot.files)
    assert not directory.exists()


def test_workspace_read_only_inputs_remain_protected(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    original = FileData(FileRef("spec.md", BlobRef.of(b"original")), b"original")
    workspace = Workspace(store, read_only_paths=("spec.md",))
    workspace.seed((original,))
    root, _ = workspace.checkpoint()
    for action in (lambda: workspace.write("spec.md", b"changed"), lambda: workspace.delete("spec.md")):
        with pytest.raises(RecordError, match="cannot"):
            action()
    for files in ((), (FileRef("spec.md", store.put(b"changed")),)):
        altered = store.put(WorkspaceSnapshot(files).to_bytes())
        with pytest.raises(RecordError, match="protected"):
            workspace.restore(altered)
        assert workspace.read("spec.md") == original.data
    workspace.write("answer.py", b"pass")
    workspace.restore(root)
    assert workspace.snapshot().files == (original.reference,)


def test_large_workspace_index_is_not_capped_as_a_syn_control_record(tmp_path):
    from behave.data.drafts import load_workspace

    store = DirectoryStore(tmp_path / "objects")
    body = store.put(b"x")
    snapshot = WorkspaceSnapshot(tuple(FileRef(f"files/{i:05d}_{'x' * 100}.py", body) for i in range(5000)))
    payload = snapshot.to_bytes()
    assert len(payload) > 1 << 20
    root = store.put(payload)
    workspace = Workspace(store)
    workspace.restore(root)
    assert workspace.snapshot() == snapshot
    assert tuple(f.reference for f in load_workspace(store, root)) == snapshot.files


def test_snapshot_rejects_corruption_before_replacing_live_index(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    workspace = Workspace(store)
    workspace.write("a", b"original")
    root, _ = workspace.checkpoint()
    workspace.write("a", b"current")
    old = store.directory / BlobRef.of(b"original").sha256
    old.chmod(0o600)
    old.write_bytes(b"damaged!")
    with pytest.raises(RecordError):
        workspace.restore(root)
    assert workspace.read("a") == b"current"
    with pytest.raises(RecordError):
        WorkspaceSnapshot.from_bytes(b'{"schema":"behave.workspace/1", "files":[]}')


def test_journal_audit_checks_closure_and_append_detects_conflict(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "events", store)
    missing = BlobRef.of(b"not published")
    event = journal.append((), "initialized", {"config": missing.as_dict()}, (missing,))
    with pytest.raises(RecordError):
        journal.load()
    store.put(b"not published")
    assert journal.load() == (event,)
    with pytest.raises(RecordError) as error:
        journal.append((), "initialized", {"different": True})
    assert error.value.code == "journal-conflict"
    other = AttemptJournal(journal.directory, store)
    second = other.append((event,), "terminal", {"done": True})
    assert second.parent == BlobRef.of(event.to_bytes())
    assert journal.load() == (event, second)


@pytest.mark.parametrize("damage", ["gap", "payload", "artifact", "symlink", "extra"])
def test_journal_cold_recovery_checks_chain_and_artifacts(tmp_path, damage):
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "events", store)
    ref = store.put(b"artifact")
    first = journal.append((), "initialized", {}, (ref,))
    journal.append((first,), "terminal", {})
    target = journal.directory / "000000.json"
    if damage == "gap":
        target.rename(journal.directory / ".pending-interrupted")
    elif damage == "payload":
        target.chmod(0o600)
        target.write_bytes(canonical_json({"unknown": True}))
    elif damage == "artifact":
        artifact = store.directory / ref.sha256
        artifact.chmod(0o600)
        artifact.write_bytes(b"bad")
    elif damage == "symlink":
        target.rename(tmp_path / "saved")
        target.symlink_to(tmp_path / "saved")
    else:
        (journal.directory / "unexpected").write_bytes(b"bad")
    with pytest.raises((RecordError, OSError)):
        journal.load()


def test_journal_conflicting_coordinators_do_not_mix_records(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    journals = [AttemptJournal(tmp_path / "events", store) for _ in range(8)]

    def append(item):
        index, journal = item
        try:
            journal.append((), "initialized", {"writer": index})
            return True
        except RecordError as error:
            assert error.code == "journal-conflict"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(append, enumerate(journals)))
    assert sum(outcomes) == 1
    assert len(journals[0].load()) == 1
    with pytest.raises(TypeError):
        journals[0].load()[0].payload["writer"] = 99
    assert metadata({"x": [1]})["x"] == (1,)
