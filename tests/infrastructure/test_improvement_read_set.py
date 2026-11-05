"""Track the verified inputs once; transfer only from an explicit owned source."""

from collections import Counter
from types import SimpleNamespace

import pytest

from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.improve.input_stores import ReadSet
from behave.improve.round_data import _ReadSet


def test_read_set_retains_compatibility_and_order_without_caching(tmp_path):
    assert _ReadSet is ReadSet
    source = DirectoryStore(tmp_path / "source")
    a, b = source.put(b"a"), source.put(b"b")
    reads = []

    def read(reference):
        reads.append(reference)
        return source.read(reference)

    tracked = ReadSet(SimpleNamespace(read=read))
    for ref in (b, a, b):
        read_verified(tracked, ref)
    missing = BlobRef.of(b"missing")
    with pytest.raises(RecordError, match="cannot read content"):
        tracked.read(missing)
    assert tuple(tracked.references) == (b, a)
    assert reads == [b, a, b, missing]
    calls = []
    destination = SimpleNamespace(publish=lambda reader, refs: calls.append((reader, refs)))
    tracked.publish(destination)
    assert calls == [(tracked.reader, (b, a))]


def test_arbitrary_reader_still_gets_verified_publication(tmp_path):
    ref = BlobRef.of(b"good")
    payload = b"good"
    tracked = ReadSet(SimpleNamespace(read=lambda _: payload))
    assert read_verified(tracked, ref) == payload
    payload = b"evil"
    destination = DirectoryStore(tmp_path / "destination")
    with pytest.raises(RecordError):
        tracked.publish(destination)
    assert not (destination.directory / ref.sha256).exists()


def test_owned_transfer_does_not_reread_and_survives_source_release(tmp_path):
    source, target = DirectoryStore(tmp_path / "source"), DirectoryStore(tmp_path / "target")
    refs = (source.put(b"a"), source.put(b"b"))
    reads = Counter()

    def read(reference):
        reads[reference] += 1
        return source.read(reference)

    tracked = ReadSet(SimpleNamespace(read=read))
    for ref in refs:
        read_verified(tracked, ref)
    tracked.publish(target, transfer=lambda destination, refs: destination.publish_owned(source, refs))
    assert reads == Counter({ref: 1 for ref in refs})
    for ref in refs:
        (source.directory / ref.sha256).unlink()
    assert [target.read(ref) for ref in refs] == [b"a", b"b"]


def test_failed_transfer_retains_inventory_for_explicit_retry(tmp_path):
    source, target = DirectoryStore(tmp_path / "source"), DirectoryStore(tmp_path / "target")
    refs = (source.put(b"a"), source.put(b"b"))
    tracked = ReadSet(source)
    for ref in refs:
        tracked.read(ref)

    def interrupted(destination, references):
        destination.publish_owned(source, references[:1])
        raise OSError("transfer interrupted")

    with pytest.raises(OSError, match="transfer interrupted"):
        tracked.publish(target, transfer=interrupted)
    assert tuple(tracked.references) == refs
    tracked.publish(target, transfer=lambda destination, refs: destination.publish_owned(source, refs))
    assert [target.read(ref) for ref in refs] == [b"a", b"b"]
