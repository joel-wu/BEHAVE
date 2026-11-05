"""Coordinator leases, cancellation and replay without paid provider calls."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from behave.agent import AttemptControl, AttemptInterrupted, AttemptJournal, AttemptSpec, run_attempt
from behave.agent.admission import AdmissionPolicy, ResourcePool, shared_pools
from behave.agent.model import ModelFailure, ModelReply
from behave.agent.trajectory import trajectory
from behave.core.storage import DirectoryStore


def eventually(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("worker failed to reach its synchronization point")
        time.sleep(.005)


def test_stage_aliases_share_capacity_and_quarantine():
    from behave.core.lifecycle import CleanupState

    policy = AdmissionPolicy("eda", 1)
    pools = shared_pools(check=policy, development=policy, model=None)
    assert pools["check"] is pools["development"] and pools["model"] is None
    with pools["check"].acquire():
        assert pools["development"].counts == (1, 0)
    pools["check"].quarantine(CleanupState.UNKNOWN)
    with pytest.raises(AttemptInterrupted, match="resource-cleanup"), pools["development"].acquire():
        pytest.fail("another stage must not evade quarantined capacity")
    with pytest.raises(ValueError, match="conflicting"):
        shared_pools(check=policy, development=AdmissionPolicy("eda", 2))


def test_fifo_waiters_cancel_without_ghosts_and_release_on_baseexception():
    pool = ResourcePool(AdmissionPolicy("test", 1))
    cancel = Event()
    order = []

    def waiting(index):
        with pool.acquire(cancelled=cancel.is_set if index == 0 else lambda: False):
            order.append(index)

    with ThreadPoolExecutor(max_workers=3) as workers:
        with pool.acquire():
            first = workers.submit(waiting, 0)
            eventually(lambda: pool.counts == (1, 1))
            second = workers.submit(waiting, 1)
            eventually(lambda: pool.counts == (1, 2))
            third = workers.submit(waiting, 2)
            eventually(lambda: pool.counts == (1, 3))
            cancel.set()
            with pytest.raises(AttemptInterrupted, match="resource-cancelled"):
                first.result(timeout=5)
            assert pool.counts == (1, 2)
        second.result(timeout=5)
        third.result(timeout=5)
    assert order == [1, 2] and pool.counts == (0, 0)
    with pytest.raises(KeyboardInterrupt), pool.acquire():
        raise KeyboardInterrupt
    assert pool.counts == (0, 0)


def test_rate_window_and_deadline_are_separate_from_concurrency():
    pool = ResourcePool(AdmissionPolicy("test", 1, starts_per_window=1, window_seconds=1))
    with pool.acquire():
        pass
    with pytest.raises(AttemptInterrupted, match="resource-wait-expired"), pool.acquire(deadline=time.monotonic() + .05):
        pytest.fail("rate limit admitted a second start in the same window")
    assert pool.counts == (0, 0)
    with pool.acquire():
        assert pool.counts == (1, 0)


@pytest.mark.parametrize("wait_seconds,deadline,stop", [(3, 10., 3.), (10, 3., 3.), (None, 3., 3.)])
def test_explicit_queue_budget_and_caller_deadline_use_the_earlier_stop(
        monkeypatch, wait_seconds, deadline, stop):
    from behave.agent import admission
    from behave.core import runtime_defaults as defaults

    clock = SimpleNamespace(value=0.)
    waits = []
    policy = AdmissionPolicy("test", 1, wait_seconds=wait_seconds)
    assert AdmissionPolicy(**policy.as_dict()) == policy
    pool = ResourcePool(policy)
    monkeypatch.setattr(admission, "time", SimpleNamespace(monotonic=lambda: clock.value))
    monkeypatch.setattr(defaults, "ADMISSION_POLL_SECONDS", 0.2)

    def wait(seconds):
        waits.append(seconds)
        clock.value += seconds

    monkeypatch.setattr(pool._condition, "wait", wait)
    with pool.acquire():
        with pytest.raises(AttemptInterrupted, match="resource-wait-expired"), pool.acquire(deadline=deadline):
            pytest.fail("occupied pool must not admit the waiter")
        assert pool.counts == (1, 0)
    assert clock.value == pytest.approx(stop)
    assert waits[0] == 0.2 and all(0 < seconds <= 0.2 for seconds in waits)
    assert pool.counts == (0, 0)


def test_queued_attempt_has_no_model_dispatch_bill_or_zero_and_cold_replay_does_not_wait(tmp_path):
    pool = ResourcePool(AdmissionPolicy("model", 1))
    cancel = Event()
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    spec = AttemptSpec("queued", "test", {"admission": pool.policy.identity})

    class Backend:
        def generate(self, request):
            pytest.fail("cancelled waiter must not call provider")

    def run():
        return run_attempt(journal=journal, backend=Backend(), spec=spec, initial_messages=(),
                           model_admission=pool, control=AttemptControl(cancelled=cancel.is_set))

    with ThreadPoolExecutor(max_workers=1) as workers:
        with pool.acquire():
            result = workers.submit(run)
            eventually(lambda: pool.counts == (1, 1))
            cancel.set()
            with pytest.raises(AttemptInterrupted, match="resource-cancelled"):
                result.result(timeout=5)
        cancel.clear()
        with pool.acquire(), pytest.raises(AttemptInterrupted, match="resource-cancelled"):
            run()
    kinds = [event.kind for event in journal.load()]
    assert "model_requested" not in kinds and "terminal" not in kinds
    assert trajectory(journal).unpriced_dispatches == 0
    assert trajectory(journal).unresolved_dispatches == 0


def test_request_releases_local_lease_and_completed_replay_never_reacquires(tmp_path):
    pool = ResourcePool(AdmissionPolicy("model", 1))
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    spec = AttemptSpec("done", "test", {"admission": pool.policy.identity})

    class Backend:
        calls = 0

        def generate(self, request):
            self.calls += 1
            assert pool.counts == (1, 0)
            return ModelReply("done")

    backend = Backend()
    kwargs = {"journal": journal, "backend": backend, "spec": spec, "initial_messages": (), "model_admission": pool}
    result = run_attempt(**kwargs)
    assert pool.counts == (0, 0)
    with pool.acquire():
        assert run_attempt(**kwargs) == result
    assert backend.calls == 1


def test_retry_backoff_is_durable_cancellable_and_not_another_logical_turn(tmp_path):
    cancel = Event()
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))

    class Backend:
        calls = 0

        def generate(self, request):
            self.calls += 1
            cancel.set()
            return ModelFailure("retryable", "busy", retry_after_seconds=10)

    backend = Backend()
    kwargs = {"journal": journal, "spec": AttemptSpec("retry", "test", {}), "backend": backend,
              "initial_messages": (), "control": AttemptControl(cancelled=cancel.is_set)}
    with pytest.raises(AttemptInterrupted, match="attempt-cancelled"):
        run_attempt(**kwargs)
    cancel.clear()
    with pytest.raises(AttemptInterrupted, match="attempt-cancelled"):
        run_attempt(**kwargs)
    assert backend.calls == 1
    assert [event.kind for event in journal.load()].count("model_backoff") == 1


@pytest.mark.parametrize("options", [{"concurrency": 0}, {"wait_seconds": False}, {"starts_per_window": 1},
                                     {"starts_per_window": 0, "window_seconds": 1}])
def test_invalid_admission_policy(options):
    with pytest.raises(ValueError):
        AdmissionPolicy(**{"resource": "test", "concurrency": 1, **options})
