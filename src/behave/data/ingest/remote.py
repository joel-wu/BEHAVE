"""Supervised, replayable public HTTPS acquisition into the existing source store."""

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from behave.agent.lifetime import current_window
from behave.agent.run_store import JournalDirectory
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, thaw_json
from behave.runtime.records import publish_json

from . import fetch_staging
from .fetch_evidence import FetchResult, receipt_result, trace_result, verify_acquisition
from .fetch_records import FetchPlan, FetchPolicy


def _dispatch(plan: FetchPlan, policy: FetchPolicy, local: JournalDirectory,
              cancelled: Callable[[], bool]) -> None:
    from behave.isolation.service import run_service

    deadline = time.monotonic() + policy.timeout_seconds
    window = current_window()
    if window is not None:
        deadline = min(deadline, time.monotonic() + window.remaining())
        parent_cancelled = cancelled
        cancelled = lambda: parent_cancelled() or window.control.cancelled()
    scratch = local.directory
    plan_ref = local.objects.put(plan.to_bytes())
    policy_ref = local.objects.put(policy.to_bytes())
    bootstrap = ("import sys; sys.path.insert(0, sys.argv.pop(1)); "
                 "from behave.data.ingest.fetch_worker import main; main()")
    argv = (sys.executable, "-I", "-B", "-c", bootstrap, str(Path(__file__).resolve().parents[3]),
            str(scratch), plan_ref.sha256, str(plan_ref.size_bytes), policy_ref.sha256, str(policy_ref.size_bytes))
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "TMPDIR": str(scratch), "PYTHONDONTWRITEBYTECODE": "1"}
    started_at = time.time()
    cleanup = CleanupState.UNKNOWN
    try:
        got = run_service(argv, cwd=scratch, environment=environment, deadline=deadline,
                          output_bytes=defaults.LOG_RETAIN_BYTES, network="host", cancelled=cancelled)
        cleanup = got.cleanup_state
        status, returncode, stdout, stderr = got.status, got.returncode, got.stdout, got.stderr
        execution_status = got.execution_status
    except Exception as error:  # noqa: BLE001 -- no exception is a cleanup witness
        status, returncode = "supervision-failed", None
        execution_status = None
        stdout, stderr = canonical_json({"error_type": type(error).__name__}), b""
    logs = (local.objects.put(stdout), local.objects.put(stderr))
    # Keep the lifetime owner's result before any archive/settlement operation.
    # A crash before this record remains an ambiguous dispatch, not a retry.
    publish_json(scratch / 'service.json', {"schema": "behave.source-fetch-service/2",
        "plan": plan_ref.as_dict(), "policy": policy_ref.as_dict(), "status": status, "returncode": returncode,
        "execution_status": execution_status,
        "cleanup": cleanup.value, "started_at": started_at, "stdout": logs[0].as_dict(), "stderr": logs[1].as_dict()})


def _publish(local, run, plan, policy):
    saved = fetch_staging.read(local.directory / 'service.json')
    inputs = {'plan': BlobRef.of(plan.to_bytes()).as_dict(), 'policy': BlobRef.of(policy.to_bytes()).as_dict()}
    if any(saved.get(name) != reference for name, reference in inputs.items()):
        raise RecordError('fetch-run-mismatch', 'retained service inputs changed')
    service = local.objects.put(canonical_json(saved))
    # Unconfirmed services never authorize reading worker traces or releasing scratch.
    trace = None
    if CleanupState(saved['cleanup']).quiescent:
        trace, _ = local.seal('trace')
        trace_result(local.objects, trace, plan, policy)
    receipt = local.objects.put(canonical_json({'schema': 'behave.source-fetch-receipt/1', **inputs,
        'trace': None if trace is None else trace.as_dict(), 'service': service.as_dict()}))
    _, selected, closure = receipt_result(local.objects, receipt)
    source = None if selected is None else local.objects.put(selected.to_bytes())
    closure = (*closure, *(() if source is None else (source,)))
    run.objects.publish(local.objects, closure)
    return {'receipt': receipt.as_dict(), 'source': None if source is None else source.as_dict()}, closure


def acquire_source(plan: FetchPlan, policy: FetchPolicy, *, run: JournalDirectory,
                   cancelled: Callable[[], bool] = lambda: False) -> FetchResult:
    """One fixed dispatch. Replay and interrupted publication are offline.

    An explicit new run is required for a later retrieval, even after a clean
    rejection. Policy belongs to the trusted supervisor, not an agent tool call.
    An ambiguous dispatch is never retried. Same-node publication recovery needs
    its saved service result; the SourceRecord feeds ScopeInputs unchanged.
    """
    if type(policy) is not FetchPolicy or type(run) is not JournalDirectory or not callable(cancelled):
        raise TypeError("acquisition requires a fixed policy, owned run and cancellation predicate")
    policy.check(plan)
    expected = {"plan": BlobRef.of(plan.to_bytes()).as_dict(), "policy": BlobRef.of(policy.to_bytes()).as_dict()}
    with run.owner():
        journal = run.journal("acquisition")
        events = journal.load()
        if events and (events[0].kind != "fetch_started" or thaw_json(events[0].payload) != expected):
            raise RecordError("fetch-run-mismatch", "acquisition inputs changed across recovery")
        if len(events) > 1:
            head, _ = run.seal("acquisition")
            result = verify_acquisition(run.objects, head)
            fetch_staging.release(run, expected, result)
            return result
        if events:
            local = fetch_staging.reopen(run, expected)
        else:
            refs = (run.objects.put(plan.to_bytes()), run.objects.put(policy.to_bytes()))
            events = (journal.append((), "fetch_started", expected, refs),)
            local = fetch_staging.prepare(run, expected)
            _dispatch(plan, policy, local, cancelled)
        payload, closure = _publish(local, run, plan, policy)
        journal.append(events, "fetch_settled", payload, closure)
        head, _ = run.seal("acquisition")
        result = verify_acquisition(run.objects, head)
        fetch_staging.release(run, expected, result)
        return result
