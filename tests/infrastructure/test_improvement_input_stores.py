"""Syn resolves only requested immutable inputs, never inventories whole stores."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.improve.input_stores import Inputs


def test_input_lookup_never_lists_directories_and_caches_only_requested_owners(tmp_path, monkeypatch):
    first = DirectoryStore(tmp_path / "first")
    second = DirectoryStore(tmp_path / "second")
    a, b = first.put(b"a"), second.put(b"b")
    second.put(b"a")
    first.put(b"unrelated history")

    def no_scan(*args, **kwargs):
        pytest.fail("input lookup must not enumerate a store")

    monkeypatch.setattr(os, "listdir", no_scan)
    monkeypatch.setattr(os, "scandir", no_scan)
    calls = []
    is_file = Path.is_file

    def probe(path):
        calls.append(path)
        return is_file(path)

    monkeypatch.setattr(Path, "is_file", probe)
    inputs = Inputs(first, second)
    assert calls == [] and inputs.owners == {}
    assert inputs.owner(a) is first and inputs.owner(b) is second
    assert calls == [first.directory / a.sha256, first.directory / b.sha256, second.directory / b.sha256]
    calls.clear()
    assert inputs.read(a) == b"a" and inputs.read(b) == b"b"
    assert calls == []
    assert inputs.owners == {a.sha256: first, b.sha256: second}


def test_input_publication_preserves_source_precedence_order_and_duplicates(tmp_path):
    first, second = DirectoryStore(tmp_path / "first"), DirectoryStore(tmp_path / "second")
    a, b = first.put(b"a"), second.put(b"b")
    second.put(b"a")
    inputs = Inputs(first, second, workers=2)
    calls = []
    target = SimpleNamespace(publish_owned=lambda source, refs, **kw: calls.append((source, refs, kw)))
    inputs.publish(target, iter((b, a, b, a)))
    assert calls == [(second, (b, b), {"workers": 2}), (first, (a, a), {"workers": 2})]


def test_missing_input_fails_before_any_transfer_and_can_be_retried(tmp_path):
    source = DirectoryStore(tmp_path / "source")
    known, missing = source.put(b"known"), BlobRef.of(b"later")
    inputs = Inputs(source)
    target = SimpleNamespace(publish_owned=lambda *a, **kw: pytest.fail("partial partition published"))
    with pytest.raises(FileNotFoundError, match=missing.sha256):
        inputs.publish(target, (known, missing))
    assert missing.sha256 not in inputs.owners
    source.put(b"later")
    destination = DirectoryStore(tmp_path / "destination")
    inputs.publish(destination, (known, missing))
    inputs.publish(destination, (known, missing))
    assert destination.read(known) == b"known" and destination.read(missing) == b"later"


def test_reference_lookup_does_not_hide_a_corrupt_preferred_copy(tmp_path):
    first, second = DirectoryStore(tmp_path / "first"), DirectoryStore(tmp_path / "second")
    reference = first.put(b"good")
    second.put(b"good")
    path = first.directory / reference.sha256
    path.chmod(0o600)
    path.write_bytes(b"evil")
    with pytest.raises(RecordError, match="digest"):
        Inputs(first, second).read(reference)
