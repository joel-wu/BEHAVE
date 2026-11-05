"""Publication lookup must retain the input overlay's semantics at any scale."""

import os
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.storage import DirectoryStore
from behave.improve.generation_batch import _Inputs


@pytest.mark.parametrize("as_iterator", [False, True])
def test_publication_retains_local_preference_reference_order_and_duplicates(tmp_path, as_iterator):
    local, remote = DirectoryStore(tmp_path / "local"), DirectoryStore(tmp_path / "remote")
    a, b, c = local.put(b"a"), remote.put(b"b"), local.put(b"c")
    remote.put(b"a")
    calls = []
    destination = SimpleNamespace(publish_owned=lambda source, refs: calls.append(("local", source, refs)))
    inputs = _Inputs(local, remote, lambda target, refs: calls.append(("external", target, refs)))
    refs = (b, a, c, b, a)
    inputs.publish(destination, iter(refs) if as_iterator else refs)
    assert calls == [("local", local, (a, c, a)), ("external", destination, (b, b))]
    assert inputs.read(a) == b"a" and inputs.read(b) == b"b"


def test_small_publication_delegates_lookup_to_shared_bounded_mapper(monkeypatch):
    from behave.core.storage import publication_map
    from behave.improve import generation_batch
    seen = []

    def observe(operation, values):
        for result in publication_map(operation, values):
            seen.append(result[0])
            yield result

    monkeypatch.setattr(generation_batch, "publication_map", observe)
    refs = tuple(BlobRef.of(str(i).encode()) for i in range(97))
    calls = []
    inputs = _Inputs(object(), object(), None)
    inputs.owned = lambda ref: False
    target = SimpleNamespace(publish_owned=lambda source, refs: calls.append(("local", refs)),
                             publish=lambda source, refs: calls.append(("remote", refs)))
    inputs.publish(target, refs)
    assert tuple(seen) == refs
    assert calls == [("local", ()), ("remote", refs)]


def test_ownership_checks_actually_run_concurrently_without_exceeding_budget():
    budget = defaults.PUBLICATION_WORKERS
    refs = tuple(BlobRef.of(str(i).encode()) for i in range(budget))
    barrier, lock = Barrier(budget), Lock()
    active = peak = 0

    def owned(ref):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        with lock:
            active -= 1
        return True

    inputs = _Inputs(object(), object(), lambda *args: pytest.fail("no external references"))
    inputs.owned = owned
    calls = []
    inputs.publish(SimpleNamespace(publish_owned=lambda source, refs: calls.append(refs)), refs)
    assert peak == budget and active == 0 and calls == [refs]


def test_failed_ownership_lookup_does_not_publish_a_partial_partition():
    refs = tuple(BlobRef.of(str(i).encode()) for i in range(40))
    inputs = _Inputs(object(), object(), lambda *args: pytest.fail("must not publish"))

    def owned(ref):
        if ref == refs[-1]:
            raise OSError("NFS observation failed")
        return True

    inputs.owned = owned
    target = SimpleNamespace(publish_owned=lambda *args: pytest.fail("must not publish"))
    with pytest.raises(OSError, match="NFS observation failed"):
        inputs.publish(target, refs)


def _target(calls):
    return SimpleNamespace(publish_owned=lambda owner, refs: calls.append(("local", refs)),
                           publish=lambda reader, refs: calls.append(("external", refs)))


@pytest.mark.parametrize("as_iterator", [False, True])
def test_large_closure_preserves_order_duplicates_and_local_precedence(tmp_path, monkeypatch, as_iterator):
    local, remote = DirectoryStore(tmp_path / "local"), DirectoryStore(tmp_path / "remote")
    a, b = local.put(b"a"), remote.put(b"b")
    remote.put(b"a")
    refs = (b, a, b, a) * 40
    calls, inventories = [], []
    original = os.scandir

    def scan(path):
        inventories.append(path)
        return original(path)

    monkeypatch.setattr(os, "scandir", scan)
    inputs = _Inputs(local, remote, lambda dest, refs: calls.append(("external", refs)))
    inputs.owned = lambda ref: pytest.fail("must not issue one stat per reference")
    inputs.publish(_target(calls), iter(refs) if as_iterator else refs)
    assert calls == [("local", (a,) * 80), ("external", (b,) * 80)]
    assert inventories == [local.directory]
    del inputs.owned
    assert inputs.read(a) == b"a"


@pytest.mark.parametrize("layout", ["missing", "file", "digest_directory"])
def test_bulk_lookup_does_not_treat_non_files_as_owned(tmp_path, layout):
    local = DirectoryStore(tmp_path / "local")
    ref = BlobRef.of(b"missing")
    if layout == "file":
        local.directory.write_bytes(b"not a directory")
    elif layout == "digest_directory":
        (local.directory / ref.sha256).mkdir(parents=True)
    calls = []
    _Inputs(local, object(), None).publish(_target(calls), (ref,) * 128)
    assert calls == [("local", ()), ("external", (ref,) * 128)]


def test_inventory_is_not_stale_across_publications(tmp_path):
    local = DirectoryStore(tmp_path / "local")
    ref = BlobRef.of(b"new")
    calls = []
    inputs = _Inputs(local, object(), None)
    inputs.publish(_target(calls), (ref,) * 128)
    local.put(b"new")
    calls.clear()
    inputs.publish(_target(calls), (ref,) * 128)
    assert calls == [("local", (ref,) * 128)]
    assert inputs.read(ref) == b"new"


def test_small_closure_does_not_scan_large_local_store(tmp_path, monkeypatch):
    local, remote = DirectoryStore(tmp_path / "local"), DirectoryStore(tmp_path / "remote")
    a, b = local.put(b"a"), remote.put(b"b")
    monkeypatch.setattr(os, "scandir", lambda path: pytest.fail("small closure must not list store"))
    calls = []
    _Inputs(local, remote, None).publish(_target(calls), (a, b))
    assert calls == [("local", (a,)), ("external", (b,))]


def test_bulk_lookup_error_does_not_publish_partial_partition(tmp_path, monkeypatch):
    local = DirectoryStore(tmp_path / "local")

    def fail(path):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "scandir", fail)
    calls = []
    with pytest.raises(PermissionError, match="denied"):
        _Inputs(local, object(), None).publish(_target(calls), (BlobRef.of(b"x"),) * 128)
    assert calls == []


def test_large_publication_copies_local_and_external_payloads(tmp_path):
    local, remote = DirectoryStore(tmp_path / "local"), DirectoryStore(tmp_path / "remote")
    refs = tuple((local if i % 2 else remote).put(str(i).encode()) for i in range(128))
    destination = DirectoryStore(tmp_path / "destination")
    _Inputs(local, remote, None).publish(destination, iter(refs))
    assert [destination.read(ref) for ref in refs] == [str(i).encode() for i in range(128)]
