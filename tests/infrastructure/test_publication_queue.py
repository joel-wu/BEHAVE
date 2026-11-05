"""Object I/O uses one bounded queue, independent of closure size or callers."""

import pytest

from behave.core import storage
from behave.core.artifacts import BlobRef


class RecordedPool:
    def __init__(self, max_workers):
        self.workers = max_workers
        self.futures = []
        self.peak = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def submit(self, operation, value):
        future = RecordedFuture(operation, value)
        self.futures.append(future)
        self.peak = max(self.peak, sum(not f.settled for f in self.futures))
        return future


class RecordedFuture:
    def __init__(self, operation, value):
        self.operation, self.value = operation, value
        self.settled = self.cancelled = False

    def result(self):
        self.settled = True
        return self.operation(self.value)

    def cancel(self):
        self.cancelled = self.settled = True


@pytest.fixture
def pool(monkeypatch):
    pool = RecordedPool(2)
    monkeypatch.setattr(storage, "ThreadPoolExecutor", lambda max_workers: pool)
    return pool


def test_publication_maps_in_order_with_bounded_backpressure(pool):
    result = storage.publication_map(lambda value: value * 2, iter(range(257)), workers=2)
    assert pool.futures == []  # No work until consumption begins.
    assert list(result) == [i * 2 for i in range(257)]
    assert pool.peak == 16
    assert len(pool.futures) == 257


def test_failure_does_not_enqueue_entire_closure_and_cancels_pending_work(pool):
    def operation(value):
        if value == 2:
            raise OSError("storage unavailable")
        return value

    with pytest.raises(OSError, match="storage unavailable"):
        list(storage.publication_map(operation, range(10000), workers=2))
    assert len(pool.futures) == 18
    assert sum(f.cancelled for f in pool.futures) == 15
    assert all(f.settled for f in pool.futures)


def test_closing_mapper_cancels_queued_work(pool):
    result = storage.publication_map(lambda value: value, range(10000), workers=2)
    assert next(result) == 0
    result.close()
    assert len(pool.futures) == 16
    assert sum(f.cancelled for f in pool.futures) == 15


@pytest.mark.parametrize("workers", [0, -1, True, 1.5, "4"])
def test_owned_and_verified_publication_validate_budget_before_writing(tmp_path, workers):
    store = storage.DirectoryStore(tmp_path / "not-created")
    for publish in (store.publish, store.publish_owned):
        with pytest.raises(ValueError, match="positive integer"):
            publish(store, (), workers=workers)
    assert not store.directory.exists()


@pytest.mark.parametrize("references", [[], ("not-a-ref",)])
def test_owned_and_verified_publication_validate_references_before_writing(tmp_path, references):
    store = storage.DirectoryStore(tmp_path / "not-created")
    for publish in (store.publish, store.publish_owned):
        with pytest.raises(TypeError, match="tuple of BlobRef"):
            publish(store, references)
    assert not store.directory.exists()


@pytest.mark.parametrize("method", ["publish", "publish_owned"])
def test_both_store_transfers_use_shared_bounded_queue(tmp_path, monkeypatch, method):
    source = storage.DirectoryStore(tmp_path / "source")
    destination = storage.DirectoryStore(tmp_path / "destination")
    reference = source.put(b"verified fixture")
    calls = []
    original = storage.publication_map

    def observe(operation, values, *, workers):
        values = tuple(values)
        calls.append((values, workers))
        yield from original(operation, values, workers=workers)

    monkeypatch.setattr(storage, "publication_map", observe)
    getattr(destination, method)(source, (reference, reference), workers=2)
    assert calls == [((reference,), 2)]
    assert BlobRef.of(destination.read(reference)) == reference
