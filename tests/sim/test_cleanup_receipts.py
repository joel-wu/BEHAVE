"""Terminal cleanup evidence must survive language/tool wrappers and CPU release."""

import io
import os
import threading
from types import SimpleNamespace

import pytest

from behave.core import runtime_defaults as defaults
from behave.isolation import IsolationRefusal, IsolationResult, IsolationStatus, linux
from behave.sim import api, cpu_admission, job_isolation, worker
from behave.sim.rtl import tool_isolation, tool_protocol, tool_runtime
from tests.isolation.test_policy import _spec
from tests.sim.test_cpu_admission import eventually
from tests.sim.test_rtl_tool_isolation import _runtime


def _refusal(unconfirmed):
    detail = {"execution_status": "timed-out", "original": "compiler diagnostic " * 200}
    if unconfirmed:
        detail["cleanup"] = {"state": "unconfirmed", "why": "waitpid failed"}
    return IsolationRefusal("original-timeout", "original execution failed", detail)


@pytest.mark.parametrize("unconfirmed", [False, True])
@pytest.mark.parametrize("launcher", ["named-job", "rtl-tool"])
def test_terminal_receipt_controls_cpu_release(tmp_path, monkeypatch, unconfirmed, launcher):
    refusal = _refusal(unconfirmed)
    result = IsolationResult(IsolationStatus.REFUSED, "policy", "backend", refusal=refusal)
    quarantines = []
    quarantine = lambda: quarantines.append(True)
    monkeypatch.setattr(cpu_admission, "cpu_cleanup_unconfirmed", quarantine)
    monkeypatch.setattr(job_isolation, "cpu_cleanup_unconfirmed", quarantine)
    if launcher == "named-job":
        monkeypatch.setattr(job_isolation, "_freeze_inputs", lambda *a: ({}, ()))
        monkeypatch.setattr(job_isolation, "_job_spec", lambda *a, **kw: object())
        monkeypatch.setattr(job_isolation, "run", lambda *a, **kw: result)
        with pytest.raises(job_isolation.IsolatedJobFailure):
            job_isolation.run_isolated_job("echo", {}, timeout_s=1)
    else:
        monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime"))
        frozen = tool_runtime.FrozenToolRuntime("eda", *_runtime(tmp_path))
        monkeypatch.setattr(tool_isolation, "run", lambda *a, **kw: result)
        execution = tool_isolation.run_workspace(
            tmp_path, (tool_protocol.ToolGroup((tool_protocol.ToolCommand(
                "compile", ("/runtime/python",)),)),), role="compile", frozen_runtime=frozen)
        assert execution.refusal is refusal
    assert quarantines == ([True] if unconfirmed else [])


def test_named_job_failure_retains_the_complete_refusal_detail():
    refusal = _refusal(True)
    result = IsolationResult(IsolationStatus.REFUSED, "policy", "backend", refusal=refusal)
    failure = job_isolation._failure_of(result)
    assert failure.code == refusal.code
    assert refusal.why in failure.detail
    assert repr(dict(refusal.detail)) in failure.detail


def test_public_worker_preserves_cleanup_notes(monkeypatch):
    failure = job_isolation.IsolatedJobFailure("timeout", "original timeout", code="original")
    failure.add_note("Cleanup wait failed")
    failure.add_note("Retained child 1234")

    def fail(*a, **kw):
        raise failure

    monkeypatch.setattr(job_isolation, "run_isolated_job", fail)
    with worker._testing_job_runner(None), pytest.raises(worker.WorkerFailed) as caught:
        worker.run_job_strict("echo", {}, timeout_s=1)
    assert caught.value.how == failure.how
    assert caught.value.code == failure.code
    assert caught.value.detail == failure.detail
    assert caught.value.__notes__ == failure.__notes__


@pytest.mark.isolation
@pytest.mark.parametrize("launcher", ["named-job", "rtl-tool"])
def test_unconfirmed_terminal_receipt_quarantines_the_real_cpu_pool(tmp_path, monkeypatch, launcher):
    refusal = _refusal(True)
    result = IsolationResult(IsolationStatus.REFUSED, "policy", "backend", refusal=refusal)
    monkeypatch.setattr(job_isolation, "_freeze_inputs", lambda *a: ({}, ()))
    monkeypatch.setattr(job_isolation, "_job_spec", lambda *a, **kw: object())
    monkeypatch.setattr(job_isolation, "run", lambda *a, **kw: result)
    monkeypatch.setattr(tool_isolation, "run", lambda *a, **kw: result)
    frozen = tool_runtime.FrozenToolRuntime("eda", *_runtime(tmp_path))
    with api.CpuPool(1) as pool, api.cpu_scope(pool.endpoint) as usage:
        if launcher == "named-job":
            with pytest.raises(job_isolation.IsolatedJobFailure) as caught:
                job_isolation.run_isolated_job("echo", {}, timeout_s=1)
            assert refusal.why in caught.value.detail
        else:
            with pytest.raises(api.FailureRaised) as caught, cpu_admission.reserve_cpus(1):
                execution = tool_isolation.run_workspace(
                    tmp_path, (tool_protocol.ToolGroup((tool_protocol.ToolCommand(
                        "compile", ("/runtime/python",)),)),), role="compile", frozen_runtime=frozen)
                assert execution.refusal is refusal
            assert caught.value.failure.kind == "cpu-cleanup-unconfirmed"
        eventually(lambda: pool.snapshot()["quarantined"])
        reservations = usage.as_dict()["reservations"]
        assert len(reservations) == 1
        assert reservations[0]["status"] == "cpu-cleanup-unconfirmed"
        assert not reservations[0]["released"]


@pytest.mark.parametrize("consumer", ["status-reader", "run"])
def test_supervisor_wait_covers_the_configured_cleanup(monkeypatch, consumer):
    monkeypatch.setattr(defaults, "SERVICE_CLEANUP_SECONDS", 11)
    monkeypatch.setattr(defaults, "ISOLATION_COMPLETION_POLL_SECONDS", 0.25)
    spec = _spec()
    waits = []
    if consumer == "status-reader":
        descriptor, writer = os.pipe()
        os.close(writer)
        process = object.__new__(linux.SandboxProcess)
        process._spec = spec
        process._status_reader = linux._StatusReader(descriptor)
        process._done = threading.Event()
        process._status_lock = threading.Lock()
        monkeypatch.setattr(linux._StatusReader, "read_line", lambda reader, timeout: (
            waits.append(timeout) or b'{"kind":"finished","status":"completed","exit_code":0}'))
        process._finish_status()
        assert process._done.is_set()
    else:
        process = SimpleNamespace(
            stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO(),
            capabilities=SimpleNamespace(identity="backend"), status_refusal=None,
            final_status="completed", wait=lambda timeout: waits.append(timeout),
            poll=lambda: 0, close=lambda **kw: None)
        monkeypatch.setattr(linux, "start", lambda *a, **kw: process)
        assert linux.run(spec).status is IsolationStatus.COMPLETED
    assert waits == [spec.limits.wall_seconds + 11 + 0.25]
