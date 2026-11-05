"""Concurrency is proved with events and limits, not wall-time speed assertions."""

import gc
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar

import pytest

from behave.sim.replay_parallel import ordered_map, replay_workers
from behave.sim.scoring_policy import ScoringStopped


@pytest.mark.parametrize("count,expected", [(0, []), (1, [1]), (2, [2]), (8, [4])])
def test_known_workload_reserves_only_usable_lanes(monkeypatch, count, expected):
    from behave.sim import replay_parallel

    reservations = []

    @contextmanager
    def reserve(lanes):
        reservations.append(lanes)
        yield lanes

    monkeypatch.setattr(replay_parallel, "reserve_cpus", reserve)
    assert ordered_map(lambda value: value + 1, list(range(count)), 4) == list(range(1, count + 1))
    assert reservations == expected


def test_scheduler_bounds_submission_and_preserves_order_and_context():
    context = ContextVar("replay-test-context", default="missing")
    context.set("invocation")
    release = threading.Event()
    seen, active, maximum = [], 0, 0
    lock = threading.Lock()

    def work(value):
        nonlocal active, maximum
        assert context.get() == "invocation"
        with lock:
            active += 1
            maximum = max(maximum, active)
            seen.append(value)
        try:
            if value == 0:
                assert release.wait(10), "later completed jobs must release the next slot"
            elif value == 2:
                # Job2 must start even though job0 has not completed. Ordered
                # collection must not introduce head-of-line submission blocking.
                release.set()
            context.set("private change")
            return value * 2
        finally:
            with lock:
                active -= 1

    assert ordered_map(work, range(10), 2) == list(range(0, 20, 2))
    assert maximum == 2 and active == 0
    assert sorted(seen) == list(range(10))
    assert context.get() == "invocation"


def test_only_the_active_window_is_consumed_from_the_input_iterator():
    entered = threading.Barrier(3)
    release = threading.Event()
    consumed = []

    def values():
        for value in range(20):
            consumed.append(value)
            yield value

    def work(value):
        if value < 2:
            entered.wait(timeout=10)
            assert release.wait(10)
        return value

    with ThreadPoolExecutor(max_workers=1) as outer:
        future = outer.submit(ordered_map, work, values(), 2)
        try:
            entered.wait(timeout=10)
            assert consumed == [0, 1]
        finally:
            release.set()
        assert future.result(timeout=10) == list(range(20))


def test_worker_error_has_no_retry_and_joins_active_cleanup():
    entered = threading.Barrier(2)
    cleaned, calls = set(), []

    def work(value):
        calls.append(value)
        try:
            entered.wait(timeout=10)
            raise LookupError("one service failure")
        finally:
            cleaned.add(value)

    with pytest.raises(LookupError, match="service failure"):
        ordered_map(work, range(12), 2)
    assert sorted(calls) == [0, 1]
    assert cleaned == {0, 1}


@pytest.mark.parametrize("failure_type", [LookupError, ScoringStopped])
def test_worker_error_releases_completed_records_without_cyclic_gc(failure_type):
    class Record:
        pass

    references = []
    failures = []

    def work(value):
        if value >= 4:
            raise failure_type()
        record = Record()
        references.append(weakref.ref(record))
        return record

    # A dead Future -> traceback -> scheduler -> Future cycle can retain every
    # completed trace until interpreter shutdown. This is an ownership check,
    # not a wall-time assertion or a request to change the GC policy.
    enabled = gc.isenabled()
    gc.disable()
    try:
        try:
            ordered_map(work, range(12), 2)
        except failure_type as error:
            import traceback

            failures.extend(traceback.extract_tb(error.__traceback__))
        assert any(frame.name == "work" for frame in failures)
        assert len(references) == 4
        assert all(reference() is None for reference in references)
    finally:
        if enabled:
            gc.enable()


def test_negative_result_stops_dispatch_but_joins_every_started_worker():
    entered = threading.Barrier(2)
    stopped = threading.Event()
    calls, cleaned = [], []

    def work(value):
        calls.append(value)
        try:
            entered.wait(timeout=10)
            if value == 0:
                assert stopped.wait(10)
            return value
        finally:
            cleaned.append(value)

    def stop(value):
        if value == 1:
            stopped.set()
        return stopped.is_set()

    # Completion order is reversed. No third input may be consumed, and the
    # first worker must finish cleanup before the caller receives its records.
    assert ordered_map(work, range(20), 2, stop_when=stop) == [0, 1]
    assert sorted(calls) == sorted(cleaned) == [0, 1]


def test_serial_stop_does_not_consume_the_rest_of_the_iterator():
    seen = []

    def values():
        for value in range(10):
            seen.append(value)
            yield value

    assert ordered_map(lambda value: value, values(), 1,
                       stop_when=lambda value: value == 2) == [0, 1, 2]
    assert seen == [0, 1, 2]


@pytest.mark.parametrize("value", (0, -1, True, 1.5, "2"))
def test_invalid_explicit_worker_budget_is_not_coerced(value):
    with pytest.raises(ValueError, match="positive exact integer"):
        replay_workers(value)


def test_default_worker_budget_respects_cpu_affinity_and_explicit_override(monkeypatch):
    from behave.sim import replay_parallel

    monkeypatch.delenv("BEHAVE_REPLAY_WORKERS", raising=False)
    monkeypatch.setattr(replay_parallel.os, "sched_getaffinity", lambda _pid: {160, 161})
    assert replay_workers() == 2
    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", "3")
    assert replay_workers() == 3
    assert replay_workers(1) == 1
    for value in ("0", "-1", "eight", "1.5"):
        monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", value)
        with pytest.raises(ValueError):
            replay_workers()


def test_reference_live_identity_claim_is_atomic(monkeypatch):
    from tests.sim.test_api_path import _ref

    bundle = _ref()
    shared = bundle.new_model()
    # Simulate a faulty factory after load-time validation. It must not hand
    # the same live model to concurrent runs, even when they arrive together.
    entered = threading.Barrier(2)

    def factory():
        entered.wait(timeout=10)
        return shared

    object.__setattr__(bundle, "_factory", factory)

    def acquire(_value):
        from behave.sim.core.refusal import Refused

        try:
            bundle.new_model()
        except Refused as exc:
            return exc.refusal.kind
        return "accepted"

    assert ordered_map(acquire, range(2), 2) == ["reference-loader-reused-a-live-model"] * 2


def test_legacy_in_process_reference_does_not_qualify_for_parallel_evaluation():
    from behave.sim.evaluation import _parallel_reference
    from tests.sim.test_api_path import _ref

    assert not _parallel_reference(_ref())
