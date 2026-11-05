"""A cleanup syscall failure does not replace the service's execution receipt."""

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from behave.core.lifecycle import CleanupState
from behave.core.records import parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.isolation import service


@pytest.mark.isolation
@pytest.mark.parametrize("ending", ["exited", "timeout", "cancelled"])
@pytest.mark.parametrize("cleanup_kind", [OSError, subprocess.TimeoutExpired])
def test_cleanup_error_retains_execution_receipt(tmp_path, monkeypatch, ending, cleanup_kind):
    finish = service._finish_service

    def reap_then_fail(*args):
        # Reap real children first. Fault injection must not leak a namespace.
        assert finish(*args).quiescent
        error = (OSError("cleanup syscall failed") if cleanup_kind is OSError
                 else subprocess.TimeoutExpired("reap", 7))
        error.add_note("original cleanup detail")
        raise error

    monkeypatch.setattr(service, "_finish_service", reap_then_fail)
    program = ("import sys; print('native diagnostic',file=sys.stderr,flush=True); sys.exit(17)"
               if ending == "exited" else
               "import time; open('started','w').close(); time.sleep(60)")
    result = service.run_service(
        (sys.executable, "-I", "-B", "-c", program), cwd=tmp_path,
        environment={"PATH": os.defpath},
        deadline=time.monotonic() + (1 if ending == "timeout" else 5),
        cancelled=(lambda: (tmp_path / "started").exists()) if ending == "cancelled" else None)
    assert result.status == "cleanup-incomplete"
    assert result.execution_status == ending
    assert result.cleanup_state is CleanupState.INCOMPLETE
    assert cleanup_kind.__name__ in result.cleanup_error
    assert "original cleanup detail" in result.cleanup_error
    if ending == "exited":
        assert result.returncode == 17
        assert result.stderr == b"native diagnostic\n"


@pytest.mark.parametrize("failing", ["poll", "kill", "wait", "pidfd", "owner", "stdout", "stderr"])
def test_failed_cleanup_action_does_not_skip_other_owned_resources(monkeypatch, failing):
    calls = []

    def action(name, result=None):
        def run(*args, **kwargs):
            calls.append(name)
            if name == failing:
                raise OSError(f"{name} failed")
            return result
        return run

    process = SimpleNamespace(
        pid=123, poll=action("poll"), wait=action("wait"),
        stdout=SimpleNamespace(close=action("stdout")),
        stderr=SimpleNamespace(close=action("stderr")))
    owner = SimpleNamespace(close=action("owner"))
    monkeypatch.setattr(service.os, "killpg", action("kill"))
    monkeypatch.setattr(service.os, "close", action("pidfd"))

    class Selector:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def register(self, *args):
            pass

        def select(self, timeout):
            return [object()]

    monkeypatch.setattr(service.selectors, "DefaultSelector", Selector)
    with pytest.raises(OSError, match=f"{failing} failed"):
        service._finish_service(process, owner, 99, time.monotonic() + 5)
    assert calls == ["poll", "kill", "wait", "pidfd", "owner", "stdout", "stderr"]


def test_cleanup_failures_retain_each_cause_and_share_the_deadline(monkeypatch):
    waits = []
    first = OSError("signal failed")

    def signal_failed(*args):
        raise first

    def wait_failed(*, timeout):
        waits.append(timeout)
        raise subprocess.TimeoutExpired("owned service", timeout)

    def close_failed():
        raise OSError("stdout close failed")

    process = SimpleNamespace(pid=123, poll=lambda: None, wait=wait_failed,
                              stdout=SimpleNamespace(close=close_failed),
                              stderr=SimpleNamespace(close=lambda: None))
    owner = SimpleNamespace(fileno=lambda: -1, close=lambda: None)
    monkeypatch.setattr(service.os, "killpg", signal_failed)
    monkeypatch.setattr(service.time, "monotonic", lambda: 19.)
    with pytest.raises(OSError) as caught:
        service._finish_service(process, owner, None, 20.)
    assert caught.value is first
    assert waits == [1.]
    assert any("TimeoutExpired" in note for note in first.__notes__)
    assert any("stdout close failed" in note for note in first.__notes__)


def test_uncapped_cleanup_waits_for_namespace_without_a_hidden_deadline(monkeypatch):
    timeouts = []
    process = SimpleNamespace(pid=123, poll=lambda: 0,
        wait=lambda *, timeout: timeouts.append(timeout),
        stdout=SimpleNamespace(close=lambda: None), stderr=SimpleNamespace(close=lambda: None))
    owner = SimpleNamespace(close=lambda: None)

    class Selector:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def register(self, *args):
            pass

        def select(self, timeout):
            timeouts.append(timeout)
            return [object()]

    monkeypatch.setattr(service.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(service.os, "close", lambda fd: None)
    assert service._finish_service(process, owner, 99, None) is CleanupState.COMPLETE
    assert timeouts == [None, None]


@pytest.mark.parametrize("kind", ["judge", "testbench", "ppa"])
def test_public_worker_records_cleanup_error_separately(tmp_path, monkeypatch, kind):
    from behave.task_execution._worker_transport import exchange

    original = service.ServiceResult("cleanup-incomplete", 17, b"out", b"native error", 1.,
                                     execution_status="exited", cleanup_error="reap failed")
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: original)
    objects = DirectoryStore(tmp_path / "objects")
    response = exchange(kind, b"request", workdir=tmp_path, objects=objects,
                        deadline=time.monotonic() + 5, timeout_seconds=5, log_bytes=1024)
    receipt = parse_json(objects.read(response.evidence[0]))
    assert receipt["schema"] == f"behave.{kind}-worker-receipt/4"
    assert receipt["execution_status"] == "exited" and receipt["returncode"] == 17
    assert receipt["cleanup_error"] == "reap failed"
    assert response.cleanup is CleanupState.INCOMPLETE
    assert objects.read(response.evidence[-1]) == b"native error"


def test_job_restore_retains_cleanup_error_and_reads_older_receipts(tmp_path, monkeypatch):
    from dataclasses import replace

    from behave.agent import job_worker, jobs
    from tests.infrastructure.test_jobs import job

    control, key = job(tmp_path)
    original = service.ServiceResult("cleanup-incomplete", 17, b"out", b"native error", 1.,
                                     execution_status="exited", cleanup_error="reap failed")
    monkeypatch.setattr(job_worker, "run_service", lambda *a, **kw: original)
    job_worker.execute(control.run.directory, key)
    restored = jobs.JobControl(control.run.directory).inspect(key)
    assert restored.result == original
    assert restored.as_dict()["result"]["cleanup_error"] == "reap failed"
    _, events, request = control._load(key)
    payload = thaw_json(events[-1].payload)
    payload["schema"] = "behave.job-result/2"
    payload.pop("cleanup_error")
    old = jobs._result(replace(events[-1], payload=payload), control.run, request)
    assert old.execution_status == "exited" and old.cleanup_error is None
