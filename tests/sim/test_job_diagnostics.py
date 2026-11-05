"""Failure transport retains the cause; cleanup never replaces that cause."""

import io
import json
import os
import subprocess
import threading
import time
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest

from behave.isolation import IsolationRefusal, IsolationStatus
from behave.sim import job_isolation, job_session, worker
from behave.sim.core.refusal import Refused


def test_session_retains_short_flushed_diagnostic_before_worker_exit():
    """An open pipe must not hide a stage until 4096 bytes or EOF arrive."""
    received = threading.Event()

    class ObservedTail(bytearray):
        def extend(self, chunk):
            super().extend(chunk)
            received.set()

    read_fd, write_fd = os.pipe()
    reader, writer = os.fdopen(read_fd, 'rb'), os.fdopen(write_fd, 'wb')
    session = job_session.JobSession.__new__(job_session.JobSession)
    session._stderr_tail = ObservedTail()
    session._stderr_bytes = 0
    session._stderr_lock = threading.Lock()
    drain = threading.Thread(target=session._drain, args=(SimpleNamespace(stderr=reader),))
    drain.start()
    try:
        message = b'ANALYSIS_STAGE solver.push started\n'
        writer.write(message)
        writer.flush()
        assert received.wait(2), 'a flushed stage is still buffered in the parent'
        assert drain.is_alive(), 'the worker pipe is deliberately still open'
        assert session._stderr_diagnostic() == {
            'bytes': len(message), 'truncated': False, 'tail': message.decode()}
    finally:
        writer.close()
        drain.join(2)
        reader.close()
    assert not drain.is_alive()


@pytest.mark.parametrize('size', [0, 31, 32, 33, 8193])
def test_session_diagnostic_tail_stays_bounded(monkeypatch, size):
    monkeypatch.setattr(job_session.defaults, 'PYTHON_STDERR_BYTES', 32)
    session = job_session.JobSession.__new__(job_session.JobSession)
    session._stderr_tail = bytearray()
    session._stderr_bytes = 0
    session._stderr_lock = threading.Lock()
    message = b'x' * size
    session._drain(SimpleNamespace(stderr=io.BytesIO(message)))
    assert session._stderr_diagnostic() == {
        'bytes': size, 'truncated': size > 32, 'tail': message[-32:].decode()}


@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('broken_diagnostic', [False, True])
def test_shared_session_records_diagnostic_without_reclassifying(monkeypatch, failure, broken_diagnostic):
    message = 'latest worker phase'
    reply = {'version': 'test/1', 'id': 0, 'wall_ms': 1, 'ok': not failure}
    reply.update({'refusal': {'kind': 'test-refusal', 'why': 'original cause'}}
                 if failure else {'result': {'value': 7}})
    process = SimpleNamespace(stdin=io.BytesIO(),
        stdout=io.BytesIO(json.dumps(reply).encode() + b'\n'),
        stderr=io.BytesIO(), close=lambda: None)
    monkeypatch.setattr(job_session, 'reserve_cpus', lambda count: nullcontext(1))
    monkeypatch.setattr(job_session, '_freeze_inputs', lambda name, payload: (payload, ()))
    monkeypatch.setattr(job_session, '_job_spec', lambda *args, **kwargs: object())
    monkeypatch.setattr(job_session, 'start', lambda spec: process)
    def diagnostic(self):
        if broken_diagnostic:
            raise OSError('diagnostic unavailable')
        return {'bytes': len(message), 'truncated': False, 'tail': message}

    monkeypatch.setattr(job_session.JobSession, '_stderr_diagnostic', diagnostic)
    timings = []
    if failure:
        with pytest.raises(Refused) as caught:
            job_session.JobSession('echo', {}, module='test', version='test/1',
                timeout_s=2, wall_s=2, exchanges=None, timings=timings)
        assert caught.value.refusal.kind == 'test-refusal'
        assert caught.value.refusal.why == 'original cause'
    else:
        session = job_session.JobSession('echo', {}, module='test', version='test/1',
            timeout_s=2, wall_s=2, exchanges=None, timings=timings)
        assert session.opened == {'value': 7}
        session.close()
    if broken_diagnostic:
        assert 'stderr' not in timings[0]
    else:
        assert timings[0]['stderr']['tail'] == message


@pytest.mark.parametrize("exception", [worker.WorkerFailed, job_isolation.IsolatedJobFailure])
def test_failure_detail_is_not_a_private_display_excerpt(exception):
    message = "original diagnostic context " * 100 + "the actual cause"
    error = exception("crashed", message, code="original-code")
    assert error.detail == message
    assert str(error) == "crashed: " + message
    assert error.code == "original-code"


def test_timed_out_tool_command_leaves_no_child_running(tmp_path, monkeypatch):
    """Exercise the actual process-group supervisor, without a probe wrapper."""
    from pathlib import Path

    from behave.sim.rtl import _tool_job as guest

    monkeypatch.setattr(guest, "Path", lambda path: tmp_path if path == "/work" else Path(path))
    monkeypatch.setattr(guest, "_LOG_ROOT", tmp_path)
    row = guest._command({
        "id": "timeout",
        "argv": ["/bin/bash", "-c", "sleep 20 & printf '%s\\n' \"$!\"; wait"],
        "cwd": ".",
        "environment": {},
        "timeout_s": 1,
    }, "timeout", cleanup_seconds=7)
    assert row["timed_out"] is True
    assert "cleanup_error" not in row
    pid = int((tmp_path / row["stdout_file"]).read_text().strip())
    deadline = time.monotonic() + 1
    while True:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            break
        if state in {"Z", "X"}:
            break
        assert time.monotonic() < deadline, f"command child {pid} is still running"
        time.sleep(0.01)


def test_generation_probe_retains_complete_reason_without_changing_local_scope():
    from behave.sim.generation_protocol import Outcome, ProbeResult, Scope

    message = "original query context " * 100 + "actual solver failure"
    result = ProbeResult(Outcome.UNKNOWN, Scope.PATH_PREFIX, detail=message)
    image = result.as_dict()
    assert image["detail"] == message
    assert image["outcome"] == "unknown" and image["scope"] == "path-prefix"


def test_formula_fallback_retains_the_complete_atom_name():
    import z3

    from behave.sim.symbolic.exact_formula import ExactFormula, SliceMode

    atom = z3.Bool("original_atom_" + "context_" * 100 + "cause")
    sliced = ExactFormula().slice(atom)
    assert sliced.mode is SliceMode.FULL
    assert atom.sexpr() in sliced.fallback.detail


def test_malformed_exact_identity_keeps_full_diagnostic():
    from behave.sim.command import _exact_capability_image
    from behave.sim.core.catalogue import CatalogueManifest

    evidence = {"malformed": "identity context " * 100 + "actual defect"}
    provider = SimpleNamespace(
        domain=SimpleNamespace(digest=lambda: "domain"),
        analysis_identity=lambda: "analysis", evidence_identity=lambda: evidence,
        instance=SimpleNamespace(catalogue_manifest=lambda: CatalogueManifest(())))
    with pytest.raises(Refused) as caught:
        _exact_capability_image(provider)
    assert caught.value.refusal.kind == "sim-command-exact-capability-identity-invalid"
    assert caught.value.refusal.detail["evidence"] == repr(evidence)


def test_named_job_document_retains_complete_exception(monkeypatch):
    message = "job context " * 100 + "actual job failure"

    def fail(payload):
        raise ValueError(message)

    monkeypatch.setitem(worker.JOBS, "diagnostic-test", fail)
    answer, encoded = worker._execute_document({"job": "diagnostic-test", "payload": {}})
    assert answer == {"ok": False, "error": "ValueError: " + message}
    assert json.loads(encoded) == answer


def test_named_job_parent_retains_complete_error(monkeypatch):
    message = "worker context " * 100 + "actual worker failure"
    monkeypatch.setattr(job_isolation, "_freeze_inputs", lambda name, payload: (payload, ()))
    monkeypatch.setattr(job_isolation, "_job_spec", lambda *args, **kwargs: object())
    monkeypatch.setattr(job_isolation, "run", lambda spec, stdin: SimpleNamespace(
        status=IsolationStatus.COMPLETED,
        stdout=json.dumps({"ok": False, "error": message}).encode(),
    ))
    with pytest.raises(job_isolation.IsolatedJobFailure) as caught:
        job_isolation._run_admitted_job("echo", {}, timeout_s=1, cpus=1)
    assert caught.value.detail == message


def test_crashed_worker_retains_stderr_head_and_tail():
    message = "first cause\n" + "worker context " * 100 + "\nlast cause"
    error = job_isolation._failure_of(SimpleNamespace(
        status=IsolationStatus.EXITED, stderr=message.encode(),
    ))
    assert error.detail == "isolated worker exited: " + message


def test_command_persistence_failure_keeps_the_full_cause_and_preceding_error():
    from behave.sim.command import _persistence_failure

    message = "store failure context " * 100 + "final filesystem cause"
    preceding = {"kind": "original-run-failed"}
    diagnostic = _persistence_failure("save", OSError(message), preceding=preceding)
    assert diagnostic["error"] == "OSError: " + message
    assert diagnostic["preceding"] == preceding


def test_secondary_pool_retains_error_without_reinterpreting_the_score(monkeypatch):
    from behave.sim import witness_pool

    message = "witness reduction context " * 100 + "final reduction cause"

    def fail(evaluation):
        raise ValueError(message)

    monkeypatch.setattr(witness_pool, "minimize_witnesses", fail)
    report = witness_pool.secondary_pool_report(object())
    assert report["status"] == "refused"
    assert report["refusal"]["detail"]["error"] == "ValueError: " + message
    assert "passed" not in report


def test_exact_construction_retains_typed_frontend_cause():
    from behave.sim.behavior.analysis_job import _construction_failure

    class FrontendError(RuntimeError):
        code = "test-frontend-failed"

    message = "frontend context " * 100 + "final construction cause"
    refusal = _construction_failure(FrontendError(message))
    assert refusal.kind == FrontendError.code
    assert refusal.detail["error"] == "FrontendError: " + message


@pytest.mark.parametrize("record_kind", ["encoder", "plan", "tier", "cache"])
def test_structured_diagnostics_are_not_display_excerpts(record_kind):
    from behave.sim.symbolic.encoder import Fault
    from behave.sim.symbolic.plan import Unresolved
    from behave.sim.behavior.tiers import Tier
    from behave.sim.generation_cache import GenerationCacheEvent, GenerationCacheKind

    message = "diagnostic context Ω " * 100 + "the final cause"
    records = {
        "encoder": (Fault("resource", message), "message"),
        "plan": (Unresolved("resource", message), "what"),
        "tier": (Tier("concrete", stopped_by="resource", detail=message), "detail"),
        "cache": (GenerationCacheEvent(GenerationCacheKind.LAYOUT, "error", "key", detail=message), "detail"),
    }
    record, field = records[record_kind]
    assert json.loads(json.dumps(record.as_dict()))[field] == message


@pytest.mark.parametrize("domain", ["behavior", "rtl"])
def test_exact_unavailable_retains_the_worker_failure(domain):
    from behave.sim.behavior.exact_service import BehaviorExactJobContext
    from behave.sim.rtl.exact_job_context import RTLExactJobContext

    message = "worker context " * 100 + "actual failure"
    error = job_isolation.IsolatedJobFailure("timeout", message, code="test-timeout")
    context = BehaviorExactJobContext if domain == "behavior" else RTLExactJobContext
    refusal = context.unavailable("open", error)
    assert refusal.detail["detail"] == message
    assert refusal.detail["failure"] == "timeout"


@pytest.mark.parametrize("stage", ["layout", "source", "probe"])
@pytest.mark.parametrize("how", ["timeout", "crashed", "no-answer", "refused"])
def test_rtl_worker_timeout_is_not_a_source_capability_failure(stage, how):
    from behave.sim.rtl.exact_job_context import RTLExactJobContext

    error = job_isolation.IsolatedJobFailure(how, "original worker diagnostic")
    error.add_note("Generation session cleanup unconfirmed: original cleanup cause")
    refusal = RTLExactJobContext.unavailable(stage, error)
    suffix = "timeout" if how == "timeout" else "unavailable"
    assert refusal.kind == f"rtl-exact-isolated-{stage}-{suffix}"
    assert refusal.detail["failure"] == how
    assert refusal.detail["detail"] == error.detail
    assert tuple(refusal.detail["cleanup_notes"]) == tuple(error.__notes__)
    if how == "timeout":
        assert "deadline" in refusal.why


def test_timeout_word_in_an_internal_error_does_not_change_its_classification():
    from behave.sim.rtl.exact_job_context import RTLExactJobContext

    refusal = RTLExactJobContext.unavailable("source", ValueError("timeout in malformed input"))
    assert refusal.kind == "rtl-exact-isolated-source-unavailable"
    assert refusal.detail["failure"] == "ValueError"


def test_guidance_preparation_retains_the_original_failure_without_a_score():
    from behave.sim.production_generation import _construction_refusal

    message = "preparation context " * 100 + "actual capability failure"
    refusal = _construction_refusal("test-unavailable", "candidate", ValueError(message))
    assert dict(refusal.detail) == {"error": "ValueError: " + message}
    assert refusal.kind == "test-unavailable"


def test_session_server_retains_complete_error():
    message = "session context " * 100 + "actual dispatch failure"
    reader = io.BytesIO(json.dumps({
        "version": "test/1", "id": 0, "op": "open", "payload": {},
    }).encode() + b"\n")
    writer = io.BytesIO()

    def fail(op, payload):
        raise ValueError(message)

    job_session.serve(reader, writer, version="test/1", dispatch=fail)
    assert json.loads(writer.getvalue())["error"] == "ValueError: " + message


@pytest.mark.parametrize("frame", [b"", b'{"ok":'])
@pytest.mark.parametrize("status,code", [
    ("memory-limit", -9), ("timed-out", -9), ("signaled", -11),
    ("exited", 1), ("completed", 0), ("refused", -9),
])
def test_session_eof_preserves_supervisor_cause_before_cleanup(monkeypatch, frame, status, code):
    events = []
    refusal = (IsolationRefusal("isolation-supervisor-failed", "missing child status",
                               {"error": "original cause"})
               if status == "refused" else None)

    def wait(timeout):
        assert 0 <= timeout <= 2
        events.append("wait")
        return code

    def close():
        events.append("close")
        process.final_status = "signaled"  # Cleanup must not replace the cause.

    process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(frame),
                              stderr=io.BytesIO(), wait=wait, close=close,
                              final_status=status, status_refusal=refusal)
    monkeypatch.setattr(job_session, "reserve_cpus", lambda count: nullcontext(1))
    monkeypatch.setattr(job_session, "_freeze_inputs", lambda name, payload: (payload, ()))
    monkeypatch.setattr(job_session, "_job_spec", lambda *args, **kwargs: object())
    monkeypatch.setattr(job_session, "start", lambda spec: process)
    timings = []
    with pytest.raises(job_isolation.IsolatedJobFailure) as caught:
        job_session.JobSession("echo", {}, module="test", version="test/1",
                               timeout_s=2, wall_s=2, exchanges=None, timings=timings)
    terminal = {"status": status, "returncode": code}
    if refusal is not None:
        terminal["refusal"] = refusal.as_dict()
    assert timings[0]["worker_exit"] == terminal
    assert json.dumps(terminal, sort_keys=True) in caught.value.detail
    assert caught.value.how == "no-answer"  # No solver answer or candidate failure.
    assert events == ["wait", "close"]


def test_session_eof_does_not_wait_beyond_exchange_deadline(monkeypatch):
    events = []

    def wait(timeout):
        events.append(("wait", timeout))
        raise subprocess.TimeoutExpired("worker", timeout)

    process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO(),
                              wait=wait, close=lambda: events.append(("close", None)))
    monkeypatch.setattr(job_session, "reserve_cpus", lambda count: nullcontext(1))
    monkeypatch.setattr(job_session, "_freeze_inputs", lambda name, payload: (payload, ()))
    monkeypatch.setattr(job_session, "_job_spec", lambda *args, **kwargs: object())
    monkeypatch.setattr(job_session, "start", lambda spec: process)
    timings = []
    with pytest.raises(job_isolation.IsolatedJobFailure, match="not confirmed"):
        job_session.JobSession("echo", {}, module="test", version="test/1",
                               timeout_s=2, wall_s=2, exchanges=None, timings=timings)
    assert [op for op, _ in events] == ["wait", "close"]
    assert 0 <= events[0][1] <= 2
    assert "worker_exit" not in timings[0]


def test_session_eof_wait_keeps_search_expiry_as_normal_stop(monkeypatch):
    from behave.sim.search_policy import SearchStopped

    expired = False
    events = []

    def wait(timeout):
        nonlocal expired
        expired = True
        raise subprocess.TimeoutExpired("worker", timeout)

    def check():
        if expired:
            raise SearchStopped("time-budget-exhausted")

    process = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO(),
                              wait=wait, close=lambda: events.append("close"))
    monkeypatch.setattr(job_session, "check_execution", check)
    monkeypatch.setattr(job_session, "reserve_cpus", lambda count: nullcontext(1))
    monkeypatch.setattr(job_session, "_freeze_inputs", lambda name, payload: (payload, ()))
    monkeypatch.setattr(job_session, "_job_spec", lambda *args, **kwargs: object())
    monkeypatch.setattr(job_session, "start", lambda spec: process)
    with pytest.raises(SearchStopped):
        job_session.JobSession("echo", {}, module="test", version="test/1",
                               timeout_s=2, wall_s=2, exchanges=None)
    assert events == ["close"]


@pytest.mark.parametrize("stage", ["open", "probe"])
@pytest.mark.parametrize("kind", ["error", "refusal"])
def test_session_cleanup_preserves_failure_before_releasing_cpu(monkeypatch, stage, kind):
    message = "original query context " * 100 + "actual query failure"
    cleanup = RuntimeError("could not reap owned process")
    cleanup.add_note("namespace child 123 still holds its CPU lease")
    held, events = [], []

    @contextmanager
    def reserve(workers):
        held.append(workers)
        try:
            yield workers
        finally:
            held.pop()

    def close():
        events.append(("close", bool(held)))
        raise cleanup

    def reply(sequence, failed):
        result = {"version": "test/1", "id": sequence, "wall_ms": 0, "ok": not failed}
        if not failed:
            result["result"] = {}
        elif kind == "error":
            result["error"] = message
        else:
            result["refusal"] = {"kind": "original-refusal", "why": message}
        return json.dumps(result).encode() + b"\n"

    process = SimpleNamespace(stdin=io.BytesIO(), stderr=io.BytesIO(), close=close,
                              stdout=io.BytesIO(reply(0, stage == "open")))
    monkeypatch.setattr(job_session, "reserve_cpus", reserve)
    monkeypatch.setattr(job_session, "cpu_cleanup_unconfirmed",
                        lambda: events.append(("quarantine", bool(held))))
    monkeypatch.setattr(job_session, "_freeze_inputs", lambda name, payload: (payload, ()))
    monkeypatch.setattr(job_session, "_job_spec", lambda *args, **kwargs: object())
    monkeypatch.setattr(job_session, "start", lambda spec: process)
    error_type = job_isolation.IsolatedJobFailure if kind == "error" else Refused
    with pytest.raises(error_type) as caught:
        session = job_session.JobSession("echo", {}, module="test", version="test/1",
                                         timeout_s=1, wall_s=1, exchanges=None)
        process.stdout = io.BytesIO(reply(1, True))
        session._exchange("probe", {}, 1)

    assert events == [("close", True), ("quarantine", True)]
    original = caught.value
    assert (original.detail if kind == "error" else original.refusal.why) == message
    assert original.__notes__ == [
        ("Generation session cleanup unconfirmed: RuntimeError: could not reap owned process\n"
         "namespace child 123 still holds its CPU lease"),
    ]


def test_session_cleanup_alone_still_fails(monkeypatch):
    cleanup = RuntimeError("could not reap owned process")
    quarantined = []

    def close():
        raise cleanup

    session = job_session.JobSession.__new__(job_session.JobSession)
    session._child = SimpleNamespace(close=close)
    session._closed = False
    session._cleanup_error = None
    child = session._child
    monkeypatch.setattr(job_session, "cpu_cleanup_unconfirmed", lambda: quarantined.append(True))
    with pytest.raises(RuntimeError) as caught:
        session.close()
    assert caught.value is cleanup
    assert quarantined == [True]
    assert session._child is child  # No successful ownership-release receipt.
    with pytest.raises(RuntimeError) as repeated:
        session.close()
    assert repeated.value is cleanup
    assert quarantined == [True]  # No fresh cleanup allowance or retry.
    with pytest.raises(RuntimeError) as unwinding:
        try:
            session.close()
        finally:
            session.close()
    assert unwinding.value is cleanup
    assert not getattr(cleanup, "__notes__", ())  # No recursive self-description.


def test_failed_session_close_cannot_resume_transport(monkeypatch):
    calls = []
    child = SimpleNamespace(stdin=io.BytesIO())

    def close():
        calls.append("close")
        raise RuntimeError("owned child was not reaped")

    child.close = close
    session = job_session.JobSession.__new__(job_session.JobSession)
    session._child = child
    session._closed = False
    session._cleanup_error = None
    monkeypatch.setattr(job_session, "cpu_cleanup_unconfirmed", lambda: calls.append("quarantine"))
    monkeypatch.setattr(job_session, "check_execution", lambda: None)
    primary = ValueError("original query failed")
    session.close(failure=primary)
    session.close(failure=primary)  # Nested failure/finally paths share the receipt.
    with pytest.raises(ValueError) as caught:
        try:
            raise primary
        finally:
            session.close()  # Public context exits need not pass the error again.
    assert caught.value is primary
    assert calls == ["close", "quarantine"]
    assert len(primary.__notes__) == 1
    assert session._child is child
    with pytest.raises(job_isolation.IsolatedJobFailure, match="session is closed"):
        session._exchange_admitted("probe", {}, 1)
    assert child.stdin.getvalue() == b""
