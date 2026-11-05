"""Cleanup evidence for serving commands; process ownership stays in isolation."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event, Lock
from typing import TYPE_CHECKING

from behave.core.lifecycle import CleanupState

if TYPE_CHECKING:
    from behave.isolation.service import ServiceResult


class ServingFailure(RuntimeError):
    def __init__(self, code: str, *, cleanup_state: CleanupState = CleanupState.COMPLETE):
        if type(cleanup_state) is not CleanupState:
            raise TypeError("serving failures require explicit cleanup evidence")
        self.code, self.cleanup_state = code, cleanup_state
        super().__init__(code)


def start_sessions(sessions, *, cancelled=None):
    """Start one owned engine group, cancelling peers after a startup failure.

    Observe completions rather than input order: an uncapped first engine must
    not hide another engine's failure. Join all startup calls before returning
    to the owner, which still closes every session and retains cleanup receipts.
    No engine, serving budget, retry or process supervisor is added here.
    """
    sessions = tuple(sessions)
    if not sessions or len({id(session) for session in sessions}) != len(sessions):
        raise ValueError("serving startup requires a nonempty group of distinct sessions")
    stopped, failure_lock, failures = Event(), Lock(), []

    def interrupted():
        return stopped.is_set() or (cancelled is not None and cancelled())

    def start(session):
        try:
            return session.start(cancelled=interrupted)
        except BaseException as error:
            # Retain the initiating failure, not a peer's subsequent
            # startup-cancelled error. Publish it before signalling peers.
            with failure_lock:
                if not failures:
                    failures.append(error)
            stopped.set()
            raise

    with ThreadPoolExecutor(max_workers=len(sessions)) as pool:
        try:
            futures = [pool.submit(start, session) for session in sessions]
            for future in as_completed(futures):
                error = future.exception()
                if error is not None:
                    with failure_lock:
                        failure = failures[0] if failures else error
                    raise failure
            if interrupted():
                raise ServingFailure("startup-cancelled")
            return tuple(future.result() for future in futures)
        except BaseException:
            # Also covers an interruption of the coordinator itself. Session
            # startup/probe loops observe cancellation before the pool joins.
            stopped.set()
            raise


def close_sessions(sessions, *, record, primary_error=None):
    """Retain every engine's cleanup outcome before propagating a failure.

    A failed close is not a quiescence witness. Healthy receipts keep their
    original fields; failed/malformed results explicitly remain unknown or
    incomplete. The caller supplies its existing record/archive writer. An
    active startup/evaluation error remains primary even if cleanup also fails.
    """
    sessions = tuple(sessions)
    if len({id(session) for session in sessions}) != len(sessions):
        raise ValueError("serving cleanup requires distinct sessions")

    def close(session):
        try:
            receipt = session.close()
            if type(receipt) is not dict or type(receipt.get("cleanup_complete")) is not bool:
                raise ServingFailure("cleanup-receipt-invalid", cleanup_state=CleanupState.UNKNOWN)
            state = CleanupState(receipt.get("cleanup_state",
                "complete" if receipt["cleanup_complete"] else "unknown"))
            if state.quiescent != receipt["cleanup_complete"]:
                raise ServingFailure("cleanup-receipt-invalid", cleanup_state=CleanupState.UNKNOWN)
            return receipt, state, None
        except BaseException as error:  # noqa: BLE001 -- record all owners before re-raising below
            state = (CleanupState.INCOMPLETE if isinstance(error, ServingFailure)
                     and error.cleanup_state is CleanupState.INCOMPLETE else CleanupState.UNKNOWN)
            failure = error.code if isinstance(error, ServingFailure) else type(error).__name__
            return {"schema": "behave.serving-close-failure/1", "failure": failure,
                    "error_type": type(error).__name__, "cleanup_complete": False,
                    "cleanup_state": state.value}, state, error

    if sessions:
        with ThreadPoolExecutor(max_workers=len(sessions)) as pool:
            outcomes = list(pool.map(close, sessions))
    else:
        outcomes = []
    state = CleanupState.combine(*(item[1] for item in outcomes))
    report = {"services": [item[0] for item in outcomes], "cleanup_complete": state.quiescent,
              "cleanup_state": state.value}
    try:
        record(report)
    except BaseException as error:
        if primary_error is None:
            if isinstance(error, ServingFailure):
                error.cleanup_state = CleanupState.combine(error.cleanup_state, state, CleanupState.UNKNOWN)
            raise
        primary_error.add_note(f"Serving cleanup receipt could not be recorded: {type(error).__name__}")
        if isinstance(primary_error, ServingFailure):
            primary_error.cleanup_state = CleanupState.combine(primary_error.cleanup_state, state, CleanupState.UNKNOWN)
        return report
    if primary_error is not None:
        if not state.quiescent:
            primary_error.add_note(f"Serving group cleanup remains {state.value}")
            if isinstance(primary_error, ServingFailure):
                primary_error.cleanup_state = CleanupState.combine(primary_error.cleanup_state, state)
        return report
    failure = next((item[2] for item in outcomes if item[2] is not None), None)
    if failure is not None:
        if isinstance(failure, ServingFailure):
            failure.cleanup_state = state
        raise failure
    if not state.quiescent:
        raise ServingFailure(f"cleanup-{state.value}", cleanup_state=state)
    return report


def quiescent_call(owner: str, call: Callable[[], ServiceResult]) -> ServiceResult:
    """A failed command can be clean; a lost supervisor return cannot prove it.

    Used for short preparation/probe commands. It neither retries nor supervises
    a process itself, and preserves the shared supervisor's cleanup witness.
    """
    try:
        result = call()
        cleanup = result.cleanup_state
    except BaseException as error:  # No acknowledged cleanup after a supervisor interruption.
        raise ServingFailure(f"{owner}-supervisor-error", cleanup_state=CleanupState.UNKNOWN) from error
    if not cleanup.quiescent:
        raise ServingFailure(f"{owner}-cleanup-{cleanup.value}", cleanup_state=cleanup)
    return result
