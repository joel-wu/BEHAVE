"""Group shutdown records every owner without manufacturing quiescence."""

import json
from threading import Barrier
from types import SimpleNamespace

import pytest

from behave.core.lifecycle import CleanupState
from behave.serving import ServingFailure, close_sessions


def test_cleanup_is_parallel_ordered_and_retains_session_receipts():
    barrier = Barrier(2, timeout=3)
    rows = [{"engine": i, "cleanup_complete": True, "cleanup_state": "complete"} for i in range(2)]
    closed, recorded = [], []

    def close(index):
        barrier.wait()
        closed.append(index)
        return rows[index]

    def record(report):
        assert sorted(closed) == [0, 1]
        recorded.append(report)

    sessions = [SimpleNamespace(close=lambda i=i: close(i)) for i in range(2)]
    result = close_sessions(sessions, record=record)
    assert recorded == [result]
    assert result == {"services": rows, "cleanup_complete": True, "cleanup_state": "complete"}


@pytest.mark.parametrize("bad", [0, 1])
@pytest.mark.parametrize("typed", [False, True])
def test_one_failed_close_does_not_lose_other_receipts(bad, typed):
    error = (ServingFailure("cleanup-incomplete", cleanup_state=CleanupState.INCOMPLETE)
             if typed else RuntimeError("private supervisor information"))
    closed, recorded = [], []

    def close(index):
        closed.append(index)
        if index == bad:
            raise error
        return {"cleanup_complete": True, "engine": index}

    sessions = [SimpleNamespace(close=lambda i=i: close(i)) for i in range(2)]
    with pytest.raises(type(error)) as caught:
        close_sessions(sessions, record=recorded.append)
    assert caught.value is error and sorted(closed) == [0, 1]
    report, = recorded
    assert report["services"][1 - bad] == {"cleanup_complete": True, "engine": 1 - bad}
    assert report["services"][bad]["cleanup_complete"] is False
    assert report["cleanup_complete"] is False
    assert report["cleanup_state"] == ("incomplete" if typed else "unknown")
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize("receipt", [None, {}, {"cleanup_complete": 1},
    {"cleanup_complete": "yes"}, {"cleanup_complete": True, "cleanup_state": "unknown"},
    {"cleanup_complete": False, "cleanup_state": "complete"}])
def test_malformed_receipt_never_becomes_complete(receipt):
    recorded = []
    with pytest.raises(ServingFailure, match="cleanup-receipt-invalid"):
        close_sessions([SimpleNamespace(close=lambda: receipt)], record=recorded.append)
    assert recorded[0]["cleanup_state"] == "unknown"
    assert recorded[0]["cleanup_complete"] is False


def test_uncertain_receipt_is_not_a_successful_return():
    recorded = []
    with pytest.raises(ServingFailure, match="cleanup-unknown") as caught:
        close_sessions([SimpleNamespace(close=lambda: {"cleanup_complete": False})], record=recorded.append)
    assert caught.value.cleanup_state is CleanupState.UNKNOWN
    assert recorded[0]["services"] == [{"cleanup_complete": False}]


@pytest.mark.parametrize("other_state", [CleanupState.COMPLETE, CleanupState.INCOMPLETE])
def test_raised_failure_carries_group_evidence_not_default_complete(other_state):
    failure = ServingFailure("close-failed")
    recorded = []

    def close():
        raise failure

    sessions = [SimpleNamespace(close=close), SimpleNamespace(close=lambda: {
        "cleanup_complete": other_state.quiescent, "cleanup_state": other_state.value})]
    with pytest.raises(ServingFailure) as caught:
        close_sessions(sessions, record=recorded.append)
    expected = CleanupState.combine(CleanupState.UNKNOWN, other_state)
    assert caught.value is failure and failure.cleanup_state is expected
    assert recorded[0]["cleanup_state"] == expected.value


def test_worker_interrupt_is_recorded_before_propagation():
    failure = KeyboardInterrupt()
    recorded = []

    def close():
        raise failure

    with pytest.raises(KeyboardInterrupt) as caught:
        close_sessions([SimpleNamespace(close=close)], record=recorded.append)
    assert caught.value is failure
    assert recorded[0]["cleanup_state"] == "unknown"


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("record_failure", [False, True])
def test_active_failure_survives_cleanup_and_recording_errors(typed, record_failure):
    primary = ServingFailure("startup-failed") if typed else RuntimeError("evaluation failed")
    closed, recorded = [], []

    def close():
        closed.append(True)
        raise ServingFailure("cleanup-incomplete", cleanup_state=CleanupState.INCOMPLETE)

    def record(report):
        recorded.append(report)
        if record_failure:
            raise OSError("private storage information")

    with pytest.raises(type(primary)) as caught:
        try:
            raise primary
        finally:
            close_sessions([SimpleNamespace(close=close)], record=record, primary_error=primary)
    assert caught.value is primary and closed == [True]
    assert recorded[0]["cleanup_complete"] is False
    assert primary.__notes__ and "private" not in " ".join(primary.__notes__)
    if typed:
        assert primary.cleanup_state is CleanupState.INCOMPLETE


@pytest.mark.parametrize("typed", [False, True])
def test_recording_failure_is_not_silently_successful_without_primary_error(typed):
    error = ServingFailure("record-unavailable") if typed else OSError("record unavailable")

    def record(_):
        raise error

    with pytest.raises(type(error)) as caught:
        close_sessions([SimpleNamespace(close=lambda: {"cleanup_complete": True})], record=record)
    assert caught.value is error
    if typed:
        assert error.cleanup_state is CleanupState.UNKNOWN


def test_empty_group_has_no_unobserved_engine():
    recorded = []
    assert close_sessions([], record=recorded.append) == {
        "services": [], "cleanup_complete": True, "cleanup_state": "complete"}
