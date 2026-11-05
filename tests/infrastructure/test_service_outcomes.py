"""Execution causes survive cleanup and persisted-owner round trips."""

import os
import sys
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.agent import job_worker, jobs
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.isolation import service
from tests.infrastructure.test_jobs import job


@pytest.mark.parametrize("status", ["exited", "timeout", "cancelled", "launch-failed", "output-limit"])
def test_execution_and_complete_cleanup_remain_separate(status):
    result = service.ServiceResult(status, 0, b"out", b"err", 1.0)
    assert result.execution_status == status
    assert result.cleanup_state is CleanupState.COMPLETE


@pytest.mark.parametrize("status", ["cleanup-incomplete", "supervisor-error", "future-status"])
def test_a_legacy_or_missing_execution_cause_is_not_guessed(status):
    result = service.ServiceResult(status, None, b"", b"", 1.0)
    assert result.execution_status is None
    assert not result.cleanup_state.quiescent


@pytest.mark.parametrize("cause", [True, {}, [], "cleanup-incomplete", "guessed-timeout"])
def test_execution_cause_is_not_a_free_form_cleanup_label(cause):
    with pytest.raises(ValueError, match="execution status"):
        service.ServiceResult("cleanup-incomplete", None, b"", b"", 1.0, execution_status=cause)


def test_complete_execution_cannot_carry_a_contradictory_original_status():
    with pytest.raises(ValueError, match="execution status"):
        service.ServiceResult("exited", 0, b"", b"", 1.0, execution_status="timeout")


@pytest.mark.parametrize("cause", ["exited", "timeout", "cancelled"])
def test_job_cold_restore_retains_original_cause_without_authorizing_reuse(tmp_path, monkeypatch, cause):
    control, key = job(tmp_path)
    original = service.ServiceResult("cleanup-incomplete", 17, b"stdout", b"stderr", 1.0,
                                     execution_status=cause)
    monkeypatch.setattr(job_worker, "run_service", lambda *a, **k: original)
    outcome = job_worker.execute(control.run.directory, key)
    restored = jobs.JobControl(control.run.directory).inspect(key)
    assert restored.result == outcome.result == original
    assert restored.cleanup is CleanupState.INCOMPLETE
    assert restored.as_dict()["result"]["execution_status"] == cause

    # Old journal bytes contain no original execution status. Reading them is
    # supported, but cannot recover the cause from a return code or timing.
    _, events, request = control._load(key)
    payload = thaw_json(events[-1].payload)
    assert payload.pop("schema") == "behave.job-result/3"
    payload.pop("execution_status")
    payload.pop("cleanup_error")
    legacy = jobs._result(replace(events[-1], payload=payload), control.run, request)
    assert legacy.status == "cleanup-incomplete" and legacy.execution_status is None
    assert legacy.cleanup_state is CleanupState.INCOMPLETE


def test_job_reader_rejects_contradictory_execution_metadata(tmp_path, monkeypatch):
    control, key = job(tmp_path)
    monkeypatch.setattr(job_worker, "run_service", lambda *a, **k: service.ServiceResult("exited", 0, b"", b"", 1.0))
    job_worker.execute(control.run.directory, key)
    _, events, request = control._load(key)
    payload = thaw_json(events[-1].payload)
    payload["execution_status"] = "timeout"
    with pytest.raises(RecordError, match="execution status"):
        jobs._result(replace(events[-1], payload=payload), control.run, request)


def test_launch_failure_retains_its_original_diagnostic(tmp_path, monkeypatch):
    diagnostic = "launcher missing: " + "x" * 2048 + " original suffix"
    monkeypatch.setattr(service.socket, "socketpair", lambda *a: (
        SimpleNamespace(close=lambda: None), SimpleNamespace(close=lambda: None, fileno=lambda: 0)))

    def unavailable(*a, **k):
        raise OSError(diagnostic)

    monkeypatch.setattr(service.subprocess, "Popen", unavailable)
    result = service.run_service((sys.executable, "-c", "pass"), cwd=tmp_path, environment={},
                                 deadline=time.monotonic() + 5)
    assert result.execution_status == result.status == "launch-failed"
    assert diagnostic.encode() in result.stderr


@pytest.mark.parametrize("kind", ["judge", "testbench", "ppa"])
@pytest.mark.parametrize("cause", ["exited", "timeout", "cancelled"])
def test_public_worker_evidence_retains_original_cause_without_retry(tmp_path, monkeypatch, kind, cause):
    from behave.task_execution._worker_transport import exchange

    original = service.ServiceResult("cleanup-incomplete", 17, b"stdout", b"original failure", 1.,
                                     execution_status=cause)
    monkeypatch.setattr(service, "run_service", lambda *a, **k: original)
    objects = DirectoryStore(tmp_path / "objects")
    response = exchange(kind, b"request", workdir=tmp_path, objects=objects,
                        deadline=time.monotonic() + 5, timeout_seconds=5, log_bytes=1024)
    assert response.code == f"{kind}-worker-cleanup-incomplete"
    assert response.result is None and not response.retryable
    assert response.cleanup is CleanupState.INCOMPLETE
    receipt = parse_json(objects.read(response.evidence[0]))
    assert receipt["execution_status"] == cause and receipt["returncode"] == 17
    assert objects.read(response.evidence[-1]) == original.stderr


@pytest.mark.isolation
@pytest.mark.parametrize("ending", ["exited", "timeout", "cancelled"])
def test_real_execution_cause_survives_unconfirmed_cleanup(tmp_path, monkeypatch, ending):
    finish = service._finish_service

    def reap_but_report_unconfirmed(*args):
        # No real process is leaked by the fault injection. Only the cleanup
        # receipt is withheld, which must never erase the command's evidence.
        assert finish(*args).quiescent
        return CleanupState.INCOMPLETE

    monkeypatch.setattr(service, "_finish_service", reap_but_report_unconfirmed)
    if ending == "exited":
        program = "import sys; print('native diagnostic',file=sys.stderr,flush=True); sys.exit(17)"
    else:
        program = "import time; open('started','w').close(); time.sleep(60)"
    result = service.run_service((sys.executable, "-I", "-B", "-c", program), cwd=tmp_path,
        environment={"PATH": os.defpath}, deadline=time.monotonic() + (1 if ending == "timeout" else 5),
        cancelled=(lambda: (tmp_path / "started").exists()) if ending == "cancelled" else None)
    assert result.status == "cleanup-incomplete"
    assert result.execution_status == ending
    assert result.cleanup_state is CleanupState.INCOMPLETE
    if ending == "exited":
        assert result.returncode == 17 and b"native diagnostic" in result.stderr


@pytest.mark.isolation
@pytest.mark.parametrize("interruption", [RuntimeError("supervisor read failed"), KeyboardInterrupt("cancelled")])
def test_service_cleanup_exception_does_not_replace_active_failure(tmp_path, monkeypatch, interruption):
    finish = service._finish_service

    def reap_then_raise(*args):
        assert finish(*args).quiescent
        error = OSError("cleanup reporting failed")
        error.add_note("namespace exit receipt was not published")
        raise error

    def stop_after_start():
        if (tmp_path / "started").exists():
            raise interruption
        return False

    monkeypatch.setattr(service, "_finish_service", reap_then_raise)
    with pytest.raises(type(interruption)) as caught:
        service.run_service((sys.executable, "-I", "-B", "-c",
                             "import time; open('started','w').close(); time.sleep(60)"),
                            cwd=tmp_path, environment={"PATH": os.defpath},
                            deadline=time.monotonic() + 5, cancelled=stop_after_start)
    assert caught.value is interruption
    assert any("cleanup reporting failed" in note for note in caught.value.__notes__)
    assert any("namespace exit receipt" in note for note in caught.value.__notes__)
