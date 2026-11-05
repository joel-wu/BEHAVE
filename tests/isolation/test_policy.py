from __future__ import annotations

import io
import json
import multiprocessing
import os
import subprocess
import threading
import time
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from behave.isolation import (
    Binary,
    IsolationRefusal,
    IsolationResult,
    IsolationSpec,
    IsolationStatus,
    IsolationUnavailable,
    ReadOnlyMount,
    ResourceLimits,
    WritableRoot,
    content_attestation,
    content_digest,
    linux,
    read_only,
)
from behave.isolation import _linux_child as linux_child
from behave.isolation import content as isolation_content

_A = "0" * 64
_B = "1" * 64


def _attest_in_fresh_process(arguments):
    cache, source = arguments
    content_attestation._CACHE_ROOT = Path(cache)
    content_attestation._METADATA_SETTLE_NS = 0
    content_attestation._MEMORY.clear()
    return read_only(
        source, "/runtime/tree", reuse_attestation=True).sha256


def _spec(source: str = "/host/tool") -> IsolationSpec:
    return IsolationSpec(
        argv=("/bin/tool", "arg"),
        binaries=(Binary(source, "/bin/tool", _A),),
        read_only=(ReadOnlyMount("/host/input", "/input/data", _B),),
        writable=(WritableRoot("/work", 1024),),
        environment=(("ONLY", "declared"),),
        limits=ResourceLimits(file_bytes=1024),
    )


@pytest.mark.parametrize("original", [None, "isolation-supervisor-failed"])
def test_run_retains_original_result_when_cleanup_is_unconfirmed(monkeypatch, original):
    from types import SimpleNamespace

    refusal = (None if original is None else
               IsolationRefusal(original, "original diagnostic", {"cause": "status-pipe"}))

    class Process:
        stdin = io.BytesIO()
        stdout = io.BytesIO(b"collected-output")
        stderr = io.BytesIO(b"collected-diagnostic")
        capabilities = SimpleNamespace(identity="test-backend")
        status_refusal = refusal
        final_status = "completed"

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

        def close(self, *, deadline):
            error = linux.IsolationCleanupError("unconfirmed owner; retained /tmp/test")
            error.add_note("namespace child 123 still owns the workspace")
            raise error

    monkeypatch.setattr(linux, "start", lambda *a, **kw: Process())
    result = linux.run(_spec(), stdin=b"input")
    assert result.status is IsolationStatus.REFUSED
    assert result.stdout == b"collected-output"
    assert result.stderr == b"collected-diagnostic"
    assert result.refusal.detail["cleanup"]["state"] == "unconfirmed"
    assert "namespace child 123" in result.refusal.detail["cleanup"]["why"]
    if original is not None:
        assert result.refusal.code == original
        assert result.refusal.why == "original diagnostic"
        assert result.refusal.detail["cause"] == "status-pipe"
    else:
        assert result.refusal.code == "isolation-cleanup-unconfirmed"
        assert result.refusal.detail["execution_status"] == "completed"
        assert result.refusal.detail["exit_code"] == 0


def test_run_cleanup_cannot_replace_an_active_exception(monkeypatch):
    from types import SimpleNamespace

    failure = ValueError("original collection failure")

    class Process:
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        capabilities = SimpleNamespace(identity="test-backend")

        def wait(self, timeout):
            raise failure

        def close(self, *, deadline):
            error = linux.IsolationCleanupError("retained owner workspace")
            error.add_note("namespace child 123 still owns the workspace")
            raise error

    monkeypatch.setattr(linux, "start", lambda *a, **kw: Process())
    with pytest.raises(ValueError) as caught:
        linux.run(_spec())
    assert caught.value is failure
    assert "retained owner workspace" in caught.value.__notes__[0]
    assert "namespace child 123" in caught.value.__notes__[0]


@pytest.mark.parametrize("original", [None, "isolation-supervisor-failed"])
def test_supervisor_timeout_survives_stop_failure_and_shares_cleanup_budget(monkeypatch, original):
    from types import SimpleNamespace

    deadlines = []
    joins = []
    clock = [10.0]

    class Reader:
        def __init__(self, *, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

        def join(self, timeout):
            joins.append(timeout)
            clock[0] += 1.0

        def is_alive(self):
            return False

    class Process:
        stdin = io.BytesIO()
        stdout = io.BytesIO(b"output before timeout")
        stderr = io.BytesIO(b"original stderr")
        capabilities = SimpleNamespace(identity="test-backend")
        status_refusal = (None if original is None else
                          IsolationRefusal(original, "original diagnostic", {"cause": "status-pipe"}))

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("supervisor", timeout)

        def kill(self, *, deadline):
            deadlines.append(deadline)
            clock[0] += 2.0
            raise OSError("stop failed")

        def close(self, *, deadline):
            deadlines.append(deadline)
            raise linux.IsolationCleanupError("could not confirm reap")

    monkeypatch.setattr(linux.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(linux.defaults, "SERVICE_CLEANUP_SECONDS", 7)
    monkeypatch.setattr(linux.threading, "Thread", Reader)
    monkeypatch.setattr(linux, "start", lambda *a, **kw: Process())
    result = linux.run(_spec())

    assert deadlines == [17.0, 17.0]
    assert joins == [5.0, 4.0]
    assert result.refusal.code == (original or "isolation-supervisor-timeout")
    if original is not None:
        assert result.refusal.detail["cause"] == "status-pipe"
    assert result.refusal.detail["stop_error"] == "OSError: stop failed"
    assert result.refusal.detail["cleanup"]["state"] == "unconfirmed"
    assert result.stdout == b"output before timeout"
    assert result.stderr == b"original stderr"


@pytest.mark.parametrize("terminal", [None, "completed", "exited", "timed-out", "refused"])
@pytest.mark.parametrize("clean", [False, True])
def test_late_terminal_receipt_is_collected_after_supervisor_wait_expires(monkeypatch, terminal, clean):
    from types import SimpleNamespace

    class Process:
        stdin = io.BytesIO()
        stdout = io.BytesIO(b"complete output")
        stderr = io.BytesIO()
        capabilities = SimpleNamespace(identity="test-backend")
        status_refusal = None
        final_status = None

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("supervisor", timeout)

        def kill(self, *, deadline):
            # A reader which lost the scheduling race publishes during reaping.
            self.final_status = terminal
            if terminal == "refused":
                self.status_refusal = IsolationRefusal("terminal-invalid", "invalid status")

        def poll(self):
            return 7 if terminal == "exited" else 0

        def close(self, *, deadline):
            if not clean:
                raise linux.IsolationCleanupError("launcher is still live")

    monkeypatch.setattr(linux, "start", lambda *a, **kw: Process())
    result = linux.run(_spec())
    assert result.stdout == b"complete output"
    if clean and terminal not in (None, "refused"):
        assert result.status is IsolationStatus(terminal)
        assert result.refusal is None
        assert result.exit_code == (7 if terminal == "exited" else 0)
    else:
        assert result.status is IsolationStatus.REFUSED
        if terminal is None:
            assert result.refusal.code == "isolation-supervisor-timeout"
        elif terminal == "refused":
            assert result.refusal.code == "terminal-invalid"
        if not clean:
            assert not result.cleanup_confirmed


@pytest.mark.parametrize("stalled", ["stdout", "stderr"])
@pytest.mark.parametrize("original", [None, "isolation-supervisor-failed"])
def test_run_reports_unfinished_capture_without_losing_original_failure(
    monkeypatch, stalled, original,
):
    from types import SimpleNamespace

    refusal = (None if original is None else
               IsolationRefusal(original, "original diagnostic", {"cause": "status-pipe"}))
    joins = []

    class Reader:
        def __init__(self, *, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            if self.args != (stalled,):
                self.target(*self.args)

        def join(self, timeout):
            joins.append(timeout)

        def is_alive(self):
            return self.args == (stalled,)

    class Process:
        stdin = io.BytesIO()
        stdout = io.BytesIO(b"stdout evidence")
        stderr = io.BytesIO(b"stderr evidence")
        capabilities = SimpleNamespace(identity="test-backend")
        status_refusal = refusal
        final_status = "completed"

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

        def close(self, *, deadline):
            assert deadline == 15.0

    # No real sleeping/thread race: model a reader that cannot finish within
    # the existing runtime allowance, including time consumed by the first join.
    ticks = iter((10.0, 11.0, 14.0))
    monkeypatch.setattr(linux.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(linux.defaults, "SERVICE_CLEANUP_SECONDS", 5)
    monkeypatch.setattr(linux.threading, "Thread", Reader)
    monkeypatch.setattr(linux, "start", lambda *a, **kw: Process())
    result = linux.run(_spec())

    assert joins == [4.0, 1.0]
    assert result.status is IsolationStatus.REFUSED
    assert result.refusal.detail["capture"]["incomplete_streams"] == [stalled]
    other = "stderr" if stalled == "stdout" else "stdout"
    assert getattr(result, other) == f"{other} evidence".encode()
    if original is not None:
        assert result.refusal.code == original
        assert result.refusal.why == "original diagnostic"
        assert result.refusal.detail["cause"] == "status-pipe"
    else:
        assert result.refusal.code == "isolation-output-capture-failed"
        assert result.refusal.detail["execution_status"] == "completed"
        assert result.refusal.detail["exit_code"] == 0


def test_policy_is_immutable_canonical_and_host_path_independent():
    first = _spec("/host-a/tool")
    second = _spec("/host-b/tool")

    assert first.digest == second.digest
    assert first.backend == "linux-namespace/8"
    assert first.identity_document()["environment"] == [["ONLY", "declared"]]
    with pytest.raises(FrozenInstanceError):
        first.cwd = "/elsewhere"


def test_runtime_attestation_strategy_does_not_change_byte_policy_identity():
    plain_mount = ReadOnlyMount("/host/input", "/input/data", _B)
    reused_mount = ReadOnlyMount(
        "/host/input", "/input/data", _B, reuse_attestation=True)
    plain_binary = Binary("/host/tool", "/bin/tool", _A)
    reused_binary = Binary(
        "/host/tool", "/bin/tool", _A, reuse_attestation=True)

    assert plain_mount.identity() == reused_mount.identity()
    assert plain_binary.identity() == reused_binary.identity()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"argv": ("/not-declared",)}, r"argv\[0\]"),
        ({"environment": (("X", "1"), ("X", "2"))}, "unique"),
        ({"cwd": "/not-writable"}, "writable root"),
        ({"writable": (WritableRoot("/work", 2048),)}, "file budget"),
    ],
)
def test_policy_rejects_ambient_or_conflicting_authority(change, message):
    fields = {
        "argv": ("/bin/tool",),
        "binaries": (Binary("/host/tool", "/bin/tool", _A),),
        "writable": (WritableRoot("/work", 1024),),
        "limits": ResourceLimits(file_bytes=1024),
    }
    fields.update(change)
    with pytest.raises(ValueError, match=message):
        IsolationSpec(**fields)


def test_guest_paths_cannot_alias_through_a_double_leading_slash():
    with pytest.raises(ValueError, match="exactly one slash"):
        WritableRoot("//work", 1024)


def test_capability_refusal_never_invokes_a_direct_fallback(monkeypatch):
    refusal = IsolationRefusal("isolation-capability-missing", "namespaces are disabled")
    monkeypatch.setattr(linux, "_backend_requirements", lambda: refusal)
    monkeypatch.setattr(
        linux.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("a refused backend must not launch anything"),
    )

    with pytest.raises(IsolationUnavailable) as caught:
        linux.start(_spec())
    assert caught.value.refusal is refusal
    result = linux.run(_spec())
    assert result.status is IsolationStatus.REFUSED
    assert result.refusal is refusal


def test_capability_probe_launch_race_is_a_typed_refusal(monkeypatch):
    from behave.isolation import BackendCapabilities

    monkeypatch.setattr(
        linux,
        "_backend_requirements",
        lambda: BackendCapabilities(
            "linux-namespace/8", "kernel", ("user-namespace",)),
    )
    monkeypatch.setattr(
        linux,
        "_seccomp_path",
        lambda: "/host/libseccomp.so",
    )
    monkeypatch.setattr(linux, "_verify_source", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(linux, "capabilities", lambda: pytest.fail(
        "a real launch must not first spawn a diagnostic namespace"))
    monkeypatch.setattr(
        linux.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("unshare became unavailable")),
    )

    with pytest.raises(IsolationUnavailable) as caught:
        linux.start(_spec())

    assert caught.value.refusal.code == "isolation-launch-failed"
    assert "unshare became unavailable" in caught.value.refusal.detail["error"]


@pytest.fixture
def capability_probe(monkeypatch):
    from behave.isolation import BackendCapabilities

    available = BackendCapabilities("linux-namespace/8", "kernel", ("user-namespace",))
    monkeypatch.setattr(linux, "_backend_requirements", lambda: available)
    monkeypatch.setattr(linux, "binary", lambda source, target: Binary(source, target, _A))
    monkeypatch.setattr(linux, "native_runtime", lambda files: ())
    return available


@pytest.mark.parametrize("raised", [False, True])
def test_capability_diagnostic_keeps_the_complete_original_cause(
    monkeypatch, capability_probe, raised,
):
    diagnostic = "setup context " * 100 + "original namespace failure"
    original = IsolationRefusal("isolation-setup-failed", diagnostic,
                                {"cleanup": {"state": "unconfirmed"}})

    def check(spec):
        if raised:
            raise OSError(diagnostic)
        return IsolationResult(IsolationStatus.REFUSED, spec.digest, "unavailable", refusal=original)

    monkeypatch.setattr(linux, "run", check)
    refusal = linux.capabilities.__wrapped__()
    if raised:
        assert refusal.code == "isolation-capability-probe-failed"
        assert refusal.detail == {"error": "OSError: " + diagnostic}
    else:
        assert refusal is original


def test_capability_diagnostic_uses_the_real_execution_policy(monkeypatch, capability_probe):
    dependency = ReadOnlyMount("/host/libc.so", "/lib/libc.so", _B)
    calls = []

    def runtime(files):
        assert files == ("/bin/true",)
        return (dependency,)

    def check(spec):
        calls.append(spec)
        assert spec.argv == ("/bin/true",)
        assert spec.binaries == (Binary("/bin/true", "/bin/true", _A),)
        assert spec.read_only == (dependency,)
        assert spec.writable == (WritableRoot("/work", 4096),)
        assert spec.environment == ()
        assert spec.network.value == "denied"
        assert spec.limits.processes == 1
        assert spec.limits.wall_seconds == linux.defaults.ISOLATION_CAPABILITY_PROBE_SECONDS
        return IsolationResult(IsolationStatus.COMPLETED, spec.digest, capability_probe.identity,
                               exit_code=0)

    monkeypatch.setattr(linux, "native_runtime", runtime)
    monkeypatch.setattr(linux, "run", check)
    linux.capabilities.cache_clear()
    try:
        assert linux.capabilities() is capability_probe
        assert linux.capabilities() is capability_probe
        assert len(calls) == 1
    finally:
        linux.capabilities.cache_clear()


def test_capability_diagnostic_cannot_run_without_installed_requirements(monkeypatch):
    original = IsolationRefusal("isolation-capability-missing", "missing libseccomp")
    monkeypatch.setattr(linux, "_backend_requirements", lambda: original)
    monkeypatch.setattr(linux, "binary", lambda *_: pytest.fail("must not prepare a refused probe"))
    monkeypatch.setattr(linux, "run", lambda *_: pytest.fail("must not run a refused probe"))
    assert linux.capabilities.__wrapped__() is original


def test_capability_diagnostic_preserves_real_start_refusal(monkeypatch, capability_probe):
    original = IsolationRefusal("isolation-setup-failed", "procfs mount denied")

    def denied(*args, **kwargs):
        raise IsolationUnavailable(original)

    monkeypatch.setattr(linux, "start", denied)
    assert linux.capabilities.__wrapped__() is original


@pytest.mark.parametrize("status", [status for status in IsolationStatus
                                   if status is not IsolationStatus.REFUSED])
def test_capability_diagnostic_requires_completed_execution(monkeypatch, capability_probe, status):
    # Even a receipt labelled completed is insufficient without the successful exit.
    code = 1 if status is IsolationStatus.EXITED else None
    signal = 9 if status is IsolationStatus.SIGNALED else None
    diagnostic = b"probe failure " * 200 + b"\xff"
    monkeypatch.setattr(linux, "run", lambda spec: IsolationResult(
        status, spec.digest, capability_probe.identity,
        exit_code=code, signal=signal, stderr=diagnostic))
    refusal = linux.capabilities.__wrapped__()
    assert refusal.code == "isolation-capability-probe-failed"
    assert refusal.detail == {
        "status": status.value, "exit_code": code, "signal": signal,
        "diagnostic": diagnostic.decode("utf-8", errors="replace"),
    }


@pytest.mark.parametrize("operation", ["binary", "native_runtime", "run"])
@pytest.mark.parametrize("exception", [OSError, ValueError, RuntimeError, subprocess.SubprocessError])
def test_capability_diagnostic_reports_preparation_or_execution_failure(
    monkeypatch, capability_probe, operation, exception,
):
    def fail(*args, **kwargs):
        raise exception("original cause")

    monkeypatch.setattr(linux, operation, fail)
    refusal = linux.capabilities.__wrapped__()
    assert refusal.code == "isolation-capability-probe-failed"
    assert refusal.detail == {"error": f"{exception.__name__}: original cause"}


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, linux.PreparationCancelled])
def test_capability_diagnostic_does_not_swallow_cancellation(
    monkeypatch, capability_probe, exception,
):
    def stop(spec):
        raise exception()

    monkeypatch.setattr(linux, "run", stop)
    with pytest.raises(exception):
        linux.capabilities.__wrapped__()


@pytest.mark.parametrize("limit_status", [None, "output-limit", "protocol-limit"])
def test_terminal_status_read_keeps_the_complete_original_cause(monkeypatch, limit_status):
    import threading
    from types import SimpleNamespace

    diagnostic = "status context " * 100 + "original status failure"
    descriptor, writer = os.pipe()
    os.close(writer)
    process = linux.SandboxProcess.__new__(linux.SandboxProcess)
    process._spec = _spec()
    process._status_reader = linux._StatusReader(descriptor)
    process._limit_status = limit_status
    process._status_refusal = None
    process._done = threading.Event()
    process._status_lock = threading.Lock()
    process._popen = SimpleNamespace(
        pid=12345, poll=lambda: None,
        wait=lambda **kwargs: pytest.fail("the status reader must not own a reap deadline"),
    )
    kills = []

    def fail(*args):
        raise ValueError(diagnostic)

    monkeypatch.setattr(linux._StatusReader, "read_line", fail)
    monkeypatch.setattr(linux.os, "killpg", lambda *args: kills.append(args))
    process._finish_status()
    assert process._done.is_set()
    assert kills == [(12345, linux.signal.SIGKILL)]
    if limit_status is None:
        assert process._status_refusal.detail == {"error": "ValueError: " + diagnostic}
    else:
        assert process._status_refusal is None
        assert process.final_status == limit_status


@pytest.mark.parametrize("returncode", [None, 0])
@pytest.mark.parametrize("channel", ["output", "protocol"])
def test_limit_callbacks_do_not_start_another_cleanup_allowance(monkeypatch, returncode, channel):
    from types import SimpleNamespace

    process = object.__new__(linux.SandboxProcess)
    process._popen = SimpleNamespace(
        pid=12345, poll=lambda: returncode,
        wait=lambda **kwargs: pytest.fail("stream callbacks must not wait for teardown"),
    )
    kills = []
    monkeypatch.setattr(linux.os, "killpg", lambda *args: kills.append(args))
    if channel == "output":
        process._output_limited("stdout")
    else:
        process._protocol_limited()

    assert process.final_status == channel + "-limit"
    assert kills == ([(12345, linux.signal.SIGKILL)] if returncode is None else [])


def test_a_helper_dying_before_setup_status_is_a_typed_refusal(
    tmp_path, monkeypatch,
):
    from behave.isolation import BackendCapabilities

    class DeadHelper:
        pid = 1
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def poll(self):
            return 1

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(
        linux,
        "_backend_requirements",
        lambda: BackendCapabilities(
            "linux-namespace/8", "kernel", ("user-namespace",)),
    )
    seccomp = tmp_path / "libseccomp.so"
    seccomp.write_bytes(b"library")
    monkeypatch.setattr(linux, "_seccomp_path", lambda: str(seccomp))
    monkeypatch.setattr(linux, "_verify_source", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(linux.subprocess, "Popen", lambda *_args, **_kwargs: DeadHelper())
    monkeypatch.setattr(linux._StatusReader, "read_line", lambda *_args, **_kwargs: b"")

    with pytest.raises(IsolationUnavailable) as caught:
        linux.start(_spec())

    assert caught.value.refusal.code == "isolation-launch-failed"


@pytest.mark.parametrize("setup_refused", [False, True])
@pytest.mark.parametrize("cleanup_failure", ["timeout", "poll", "signal", "wait"])
def test_launch_cleanup_timeout_preserves_original_failure_and_workspace(
    tmp_path, monkeypatch, setup_refused, cleanup_failure,
):
    from behave.core import runtime_defaults as defaults
    from behave.isolation import BackendCapabilities

    waits, kills, child_fds = [], [], []
    diagnostic = "startup context " * 100 + "original startup deadline"

    class UnreapedHelper:
        pid = 12345

        def poll(self):
            if cleanup_failure == "poll":
                raise OSError("cleanup poll failed")

        def wait(self, timeout=None):
            waits.append(timeout)
            if cleanup_failure == "wait":
                raise OSError("cleanup wait failed")
            raise linux.subprocess.TimeoutExpired("cleanup-only", timeout)

    def kill(pid, sig):
        kills.append((pid, sig))
        if cleanup_failure == "signal":
            raise OSError("cleanup signal failed")

    def setup(*_args):
        if setup_refused:
            return json.dumps({"kind": "refused", "code": "original-setup-failure",
                               "why": diagnostic}).encode()
        raise TimeoutError(diagnostic)

    def launch(*_args, **kwargs):
        # A real child inherits these descriptors before the parent closes its
        # copies. Keep that transport shape so the intended setup error occurs.
        child_fds.extend(os.dup(fd) for fd in kwargs["pass_fds"])
        return UnreapedHelper()

    monkeypatch.setattr(linux, "_backend_requirements", lambda: BackendCapabilities(
        "linux-namespace/8", "kernel", ("user-namespace",)))
    library = tmp_path / "libseccomp.so"
    library.write_bytes(b"fixture")
    monkeypatch.setattr(linux, "_seccomp_path", lambda: str(library))
    monkeypatch.setattr(linux, "_verify_source", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(linux, "scratch_root", lambda: tmp_path)
    monkeypatch.setattr(linux.subprocess, "Popen", launch)
    monkeypatch.setattr(linux._StatusReader, "read_line", setup)
    monkeypatch.setattr(linux.os, "killpg", kill)
    monkeypatch.setattr(linux.time, "monotonic", lambda: 50.0)
    monkeypatch.setattr(defaults, "SERVICE_CLEANUP_SECONDS", 7)

    try:
        with pytest.raises(IsolationUnavailable) as caught:
            linux.start(_spec())
    finally:
        for fd in child_fds:
            os.close(fd)

    refusal = caught.value.refusal
    assert refusal.code == ("original-setup-failure" if setup_refused else "isolation-launch-failed")
    if setup_refused:
        assert refusal.why == diagnostic
    else:
        assert refusal.detail["error"] == "TimeoutError: " + diagnostic
    cleanup = refusal.detail["cleanup"]
    assert cleanup["state"] == "unconfirmed" and cleanup["pid"] == 12345
    assert Path(cleanup["workspace"]).is_dir()
    assert waits == ([7] if cleanup_failure in ("timeout", "wait") else [])
    assert kills == ([] if cleanup_failure == "poll" else [(12345, linux.signal.SIGKILL)])


@pytest.mark.parametrize("setup_refused", [False, True])
def test_launch_failure_does_not_close_descriptors_reused_by_another_launch(
    tmp_path, monkeypatch, setup_refused,
):
    from behave.isolation import BackendCapabilities

    inherited, reused = [], []

    class Helper:
        pid = 12345

        def poll(self):
            return 0

    def launch(*_args, **kwargs):
        inherited.extend(os.dup(fd) for fd in kwargs["pass_fds"])
        return Helper()

    def setup(*_args):
        # start has already closed the parent copies of the child's two pipe
        # ends and its specification writer. Simulate another concurrent launch
        # acquiring those exact descriptor numbers before this launch fails.
        reused.extend(os.open(os.devnull, os.O_RDONLY) for _ in range(3))
        if setup_refused:
            return b'{"kind":"refused","code":"setup-refused","why":"fixture"}'
        raise TimeoutError("fixture startup deadline")

    monkeypatch.setattr(linux, "_backend_requirements", lambda: BackendCapabilities(
        "linux-namespace/8", "kernel", ("user-namespace",)))
    library = tmp_path / "libseccomp.so"
    library.write_bytes(b"fixture")
    monkeypatch.setattr(linux, "_seccomp_path", lambda: str(library))
    monkeypatch.setattr(linux, "_verify_source", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(linux, "scratch_root", lambda: tmp_path)
    monkeypatch.setattr(linux.subprocess, "Popen", launch)
    monkeypatch.setattr(linux._StatusReader, "read_line", setup)

    try:
        with pytest.raises(IsolationUnavailable):
            linux.start(_spec())
        assert len(reused) == 3
        for descriptor in reused:
            os.fstat(descriptor)
    finally:
        for descriptor in inherited + reused:
            try:
                os.close(descriptor)
            except OSError:
                pass


@pytest.mark.parametrize('expired', [False, True])
@pytest.mark.parametrize('reaped', [False, True])
@pytest.mark.parametrize('failure', [TimeoutError, ValueError, IsolationUnavailable])
def test_startup_timeout_keeps_owner_cancellation_separate_from_host_failure(
        tmp_path, monkeypatch, expired, reaped, failure):
    from types import SimpleNamespace
    from behave.sim.search_policy import SearchBudget, SearchPolicy, SearchStopped, search_scope

    lease = SearchBudget(SearchPolicy(time_budget_s=30)).direction(1)
    inherited, killed, waited = [], [], []

    def wait(timeout):
        waited.append(timeout)
        if not reaped:
            raise subprocess.TimeoutExpired('fixture bootstrap', timeout)
        return 0

    def launch(*_args, **kwargs):
        inherited.extend(os.dup(fd) for fd in kwargs['pass_fds'])
        return SimpleNamespace(pid=12345, poll=lambda: None, wait=wait)

    def setup(*_args):
        if expired:
            lease.deadline = time.monotonic() - 1
        if failure is IsolationUnavailable:
            raise IsolationUnavailable(IsolationRefusal('fixture-refused', 'setup denied'))
        raise failure('fixture namespace status')

    monkeypatch.setattr(linux, '_prepare_start', lambda _spec: (None, None))
    monkeypatch.setattr(linux, '_wire_spec', lambda *_args: {})
    monkeypatch.setattr(linux, 'scratch_root', lambda: tmp_path)
    monkeypatch.setattr(linux.subprocess, 'Popen', launch)
    monkeypatch.setattr(linux._StatusReader, 'read_line', setup)
    monkeypatch.setattr(linux.os, 'killpg', lambda *args: killed.append(args))
    cancelled = expired and reaped and failure is TimeoutError
    try:
        with search_scope(lease), pytest.raises(SearchStopped if cancelled else IsolationUnavailable) as caught:
            linux.start(_spec())
    finally:
        for descriptor in inherited:
            os.close(descriptor)
    assert killed == [(12345, linux.signal.SIGKILL)] and len(waited) == 1
    if cancelled:
        assert caught.value.reason == 'time-budget-exhausted'
        assert caught.value.cleanup_confirmed
    else:
        refusal = caught.value.refusal
        assert refusal.code == ('fixture-refused' if failure is IsolationUnavailable
                                else 'isolation-launch-failed')
        assert ('cleanup' in refusal.detail) is not reaped
        if failure is not IsolationUnavailable:
            assert refusal.detail['error'] == failure.__name__ + ': fixture namespace status'


def test_launcher_and_status_reader_share_one_cleanup_deadline(monkeypatch):
    from types import SimpleNamespace

    from behave.core import runtime_defaults as defaults

    waits = []
    times = iter((1.0, 2.0, 4.0))
    monkeypatch.setattr(linux.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(defaults, "SERVICE_CLEANUP_SECONDS", 7)
    process = object.__new__(linux.SandboxProcess)
    process._popen = SimpleNamespace(poll=lambda: 0, wait=lambda timeout: waits.append(timeout))
    process._finish_status = lambda timeout: waits.append(timeout)
    process.kill()
    assert waits == [6.0, 4.0]


def test_wait_timeout_does_not_start_a_second_cleanup_allowance(monkeypatch):
    from types import SimpleNamespace

    process = object.__new__(linux.SandboxProcess)
    process._spec = SimpleNamespace(argv=("test-child",))
    process._finish_status = lambda timeout: True
    failure = subprocess.TimeoutExpired("test-child", 1)

    def expired(**kwargs):
        raise failure

    process._popen = SimpleNamespace(wait=expired)
    monkeypatch.setattr(process, "kill", lambda: pytest.fail("wait must leave cleanup to its owner"))
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        process.wait(timeout=1)
    assert caught.value is failure


def test_close_uses_its_callers_remaining_cleanup_time(monkeypatch, tmp_path):
    from types import SimpleNamespace

    waits = []
    process = object.__new__(linux.SandboxProcess)
    process._root = str(tmp_path / "owned-workspace")
    process.stdin, process.stdout, process.stderr = (io.BytesIO() for _ in range(3))
    process._popen = SimpleNamespace(poll=lambda: 0, wait=lambda timeout: waits.append(timeout))
    process._done = SimpleNamespace(is_set=lambda: True)
    process._finish_status = lambda timeout: waits.append(timeout)
    ticks = iter((14.0, 16.0))
    monkeypatch.setattr(linux.time, "monotonic", lambda: next(ticks))

    process.close(deadline=17.0)

    assert waits == [3.0, 1.0]
    assert process.stdout.closed


def test_protocol_input_budget_is_enforced_before_launch(monkeypatch):
    spec = replace(_spec(), limits=ResourceLimits(protocol_bytes=4))
    monkeypatch.setattr(
        linux,
        "start",
        lambda *_args, **_kwargs: pytest.fail(
            "an oversized protocol document must not launch"),
    )

    result = linux.run(spec, stdin=b"x" * (spec.limits.protocol_bytes + 1))

    assert result.status is IsolationStatus.REFUSED
    assert result.refusal.code == "isolation-protocol-input-too-large"


def test_interactive_protocol_budget_is_cumulative_and_byte_exact():
    class Owner:
        limited = False

        def _protocol_limited(self):
            self.limited = True

    owner = Owner()
    sink = io.BytesIO()
    writer = linux._TextWriter(linux._BoundedBinaryWriter(sink, 4, owner))

    assert writer.write("é") == 1
    assert writer.write("ab") == 2
    with pytest.raises(linux.IsolationOutputLimit, match="protocol input"):
        writer.write("c")

    assert sink.getvalue() == "éab".encode()
    assert owner.limited is True


def test_unlimited_protocol_streams_keep_byte_counts_without_a_traffic_quota():
    class Owner:
        def _protocol_limited(self):
            pytest.fail("no input quota was configured")

        def _output_limited(self, name):
            pytest.fail(f"no {name} quota was configured")

    owner = Owner()
    sink = io.BytesIO()
    writer = linux._BoundedBinaryWriter(sink, None, owner)
    payload = b"x" * 8192 + b"\n"
    for _ in range(3):
        assert writer.write(payload) == len(payload)
    assert writer._seen == 3 * len(payload)
    reader = linux._BoundedBinaryReader(io.BytesIO(sink.getvalue()), None, owner, "stdout")
    assert reader.readline() == payload
    assert reader.read() == payload * 2
    assert reader._seen == writer._seen
    assert ResourceLimits(stdout_bytes=None, protocol_bytes=None).as_dict()["stdout_bytes"] is None


def test_structured_stream_can_cross_the_former_128_mib_cumulative_cap():
    chunk = b"x" * (1 << 20)

    class Stream:
        def write(self, payload):
            return len(payload)

        def read(self, size):
            assert size == len(chunk)
            return chunk

    class Owner:
        def _protocol_limited(self):
            pytest.fail("input has no quota")

        def _output_limited(self, name):
            pytest.fail(f"{name} has no quota")

    reader = linux._BoundedBinaryReader(Stream(), None, Owner(), "stdout")
    writer = linux._BoundedBinaryWriter(Stream(), None, Owner())
    for _ in range(129):
        assert reader.read(len(chunk)) == chunk
        assert writer.write(chunk) == len(chunk)
    assert reader._seen == writer._seen == 129 << 20


@pytest.mark.parametrize("writer", [linux._write_all, linux_child._write_all])
def test_namespace_specification_write_handles_partial_pipe_writes(monkeypatch, writer):
    written = bytearray()

    def partial(_fd, payload):
        chunk = bytes(payload[:2])
        written.extend(chunk)
        return len(chunk)

    monkeypatch.setattr(linux.os, "write", partial)
    writer(7, b"abcdefg")

    assert bytes(written) == b"abcdefg"


@pytest.mark.parametrize("exec_started", [False, True])
def test_target_setup_diagnostic_survives_the_complete_handshake(monkeypatch, exec_started):
    diagnostic = "startup context " * 5000 + "original exec failure"
    records = []

    def fail(spec, setup_fd):
        if exec_started:
            linux_child._write_all(setup_fd, b"EXEC\n")
        raise OSError(diagnostic)

    monkeypatch.setattr(linux_child, "_target_process", fail)
    monkeypatch.setattr(linux_child, "_write_status", lambda fd, row: records.append(row))
    # A real pipe/fork exercises writes beyond pipe capacity. No namespace or
    # target command is started: the child executes only the failure above.
    linux_child._supervise({}, -1)

    assert records == [{
        "kind": "refused", "code": "isolation-target-setup-failed",
        "why": "OSError: " + diagnostic,
    }]


def test_status_diagnostic_has_no_implicit_size_quota(tmp_path):
    payload = json.dumps({
        "kind": "refused", "code": "isolation-target-setup-failed",
        "why": "startup context " * 5000 + "original failure",
    }).encode() + b"\n"
    status = tmp_path / "status"
    status.write_bytes(payload)
    with status.open("rb") as stream:
        assert linux._StatusReader(stream.fileno()).read_line(10.0) == payload


def test_status_reader_retains_coalesced_ready_and_terminal_without_bytewise_reads(monkeypatch):
    descriptor, writer = os.pipe()
    ready = b'{"kind":"ready","pid":1}\n'
    terminal = b'{"kind":"finished","status":"completed","exit_code":0}\n'
    original_read = os.read
    reads = []

    def read(fd, count):
        if fd == descriptor:
            reads.append(count)
        return original_read(fd, count)

    monkeypatch.setattr(linux.os, "read", read)
    # The quota applies to each message, not the coalesced pipe buffer.
    monkeypatch.setattr(linux, "_STATUS_BYTES", len(terminal))
    try:
        os.write(writer, ready + terminal)
        reader = linux._StatusReader(descriptor)
        assert reader.read_line(0) == ready
        assert reader.read_line(0) == terminal
        assert reads == [io.DEFAULT_BUFFER_SIZE]
    finally:
        os.close(descriptor)
        os.close(writer)


def test_status_reader_keeps_partial_message_until_remaining_bytes_arrive():
    descriptor, writer = os.pipe()
    try:
        reader = linux._StatusReader(descriptor)
        os.write(writer, b'{"kind":')
        with pytest.raises(TimeoutError):
            reader.read_line(0)
        os.write(writer, b'"ready","pid":1}\n')
        assert reader.read_line(0) == b'{"kind":"ready","pid":1}\n'
    finally:
        os.close(descriptor)
        os.close(writer)


def test_status_quota_checks_buffered_second_message(monkeypatch):
    descriptor, writer = os.pipe()
    monkeypatch.setattr(linux, "_STATUS_BYTES", 8)
    try:
        os.write(writer, b'first\n' + b'x' * 8 + b'\n')
        reader = linux._StatusReader(descriptor)
        assert reader.read_line(0) == b'first\n'
        with pytest.raises(ValueError, match="protocol budget"):
            reader.read_line(0)
    finally:
        os.close(descriptor)
        os.close(writer)


def test_status_diagnostic_honors_an_explicit_runtime_quota(tmp_path, monkeypatch):
    status = tmp_path / "status"
    status.write_bytes(b"x" * 100 + b"\n")
    monkeypatch.setattr(linux, "_STATUS_BYTES", 20)
    with status.open("rb") as stream, pytest.raises(ValueError, match="protocol budget"):
        linux._StatusReader(stream.fileno()).read_line(10.0)


def test_status_pipe_accepts_high_numbered_descriptors():
    import fcntl
    import resource

    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= 2048:
        pytest.skip("test requires an allowed descriptor above FD_SETSIZE")
    read_fd, write_fd = os.pipe()
    try:
        high_fd = fcntl.fcntl(read_fd, fcntl.F_DUPFD_CLOEXEC, 2048)
        try:
            os.write(write_fd, b'{"kind":"ready","pid":1}\n')
            assert linux._StatusReader(high_fd).read_line(5.) == b'{"kind":"ready","pid":1}\n'
        finally:
            os.close(high_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize("status", [
    {"kind": "finished", "status": "completed", "exit_code": 0},
    {"kind": "finished", "status": "exited", "exit_code": 7},
    {"kind": "finished", "status": "timed-out"},
])
def test_status_deadline_still_reads_a_buffered_terminal_receipt(status):
    descriptor, writer = os.pipe()
    try:
        payload = json.dumps(status).encode() + b"\n"
        os.write(writer, payload)
        # The reader may be scheduled after its wait allowance, with the
        # supervisor's complete receipt already queued. Do not wait again or
        # invent an outcome: read exactly the bytes the supervisor supplied.
        assert linux._terminal_status(linux._StatusReader(descriptor).read_line(0)) == status
    finally:
        os.close(descriptor)
        os.close(writer)


@pytest.mark.parametrize("payload", [b"", b'{"kind":"finished"'])
def test_status_deadline_does_not_wait_for_missing_terminal_bytes(payload):
    descriptor, writer = os.pipe()
    try:
        if payload:
            os.write(writer, payload)
        with pytest.raises(TimeoutError):
            linux._StatusReader(descriptor).read_line(0)
    finally:
        os.close(descriptor)
        os.close(writer)


@pytest.mark.parametrize("cleanup", [True, False])
@pytest.mark.parametrize("execution", [
    {"kind": "finished", "status": "completed", "exit_code": 0},
    {"kind": "finished", "status": "exited", "exit_code": 7},
    {"kind": "finished", "status": "signaled", "signal": 9},
    {"kind": "finished", "status": "timed-out"},
    {"kind": "finished", "status": "cpu-limit"},
    {"kind": "finished", "status": "memory-limit"},
])
def test_descendant_cleanup_retains_target_outcome(monkeypatch, cleanup, execution):
    limited = execution["status"] in {"timed-out", "cpu-limit", "memory-limit"}
    target_status = execution.get("exit_code", 0) << 8 | execution.get("signal", 0)
    waits = iter([(0, 0), (17, target_status)] if limited else [(17, target_status)])
    chunks = iter((b"EXEC\n", b""))
    records = []
    monkeypatch.setattr(linux_child.os, "fork", lambda: 17)
    monkeypatch.setattr(linux_child.os, "read", lambda fd, size: next(chunks))
    monkeypatch.setattr(linux_child.os, "waitpid", lambda *args: next(waits))
    monkeypatch.setattr(linux_child, "_pids", lambda: (17,))
    monkeypatch.setattr(linux_child, "_usage", lambda pids: (0, 0))
    monkeypatch.setattr(linux_child, "_limit_reason", lambda *args: execution["status"])
    monkeypatch.setattr(linux_child, "_kill_tree", lambda: None)
    monkeypatch.setattr(linux_child, "_reap", lambda **kw: None)
    monkeypatch.setattr(linux_child, "_terminate_descendants", lambda **kw: cleanup)
    monkeypatch.setattr(linux_child, "_write_status", lambda fd, row: records.append(row))

    linux_child._supervise({"limits": {}, "cleanup_seconds": 5, "poll_seconds": 0.05}, -1)
    assert records[0] == {"kind": "ready", "pid": 17}
    terminal = linux._terminal_status(json.dumps(records[1]).encode())
    if cleanup:
        assert terminal == execution
    else:
        assert terminal["kind"] == "refused"
        assert terminal["code"] == "isolation-descendant-reap-failed"
        assert terminal["execution"] == execution
        descriptor, writer = os.pipe()
        os.close(writer)
        process = linux.SandboxProcess.__new__(linux.SandboxProcess)
        process._spec = _spec()
        process._status_reader = linux._StatusReader(descriptor)
        process._done = threading.Event()
        process._status_lock = threading.Lock()
        monkeypatch.setattr(linux._StatusReader, "read_line", lambda *args: json.dumps(terminal).encode())
        process._finish_status()
        receipt = IsolationResult(IsolationStatus.REFUSED, "policy", "backend",
                                  refusal=process._status_refusal)
        assert not receipt.cleanup_confirmed
        assert receipt.refusal.detail["execution_status"] == execution["status"]


@pytest.mark.parametrize("execution", [None, {}, {"kind": "refused"},
                                       {"kind": "finished", "status": "completed", "exit_code": 7}])
def test_terminal_refusal_rejects_malformed_execution_evidence(execution):
    payload = json.dumps({
        "kind": "refused", "code": "isolation-descendant-reap-failed",
        "why": "cleanup incomplete", "execution": execution,
    }).encode()
    with pytest.raises(ValueError, match="namespace .* status"):
        linux._terminal_status(payload)


@pytest.mark.parametrize("cleanup", [None, {}, {"state": "confirmed", "why": "done"},
                                    {"state": "unconfirmed", "why": ""}])
def test_terminal_refusal_rejects_malformed_cleanup_evidence(cleanup):
    payload = json.dumps({
        "kind": "refused", "code": "cleanup-failed", "why": "cleanup incomplete",
        "cleanup": cleanup,
    }).encode()
    with pytest.raises(ValueError, match="namespace cleanup status"):
        linux._terminal_status(payload)


def test_terminal_refusal_exposes_original_execution_to_the_caller(monkeypatch):
    descriptor, writer = os.pipe()
    os.close(writer)
    process = linux.SandboxProcess.__new__(linux.SandboxProcess)
    process._spec = _spec()
    process._status_reader = linux._StatusReader(descriptor)
    process._done = threading.Event()
    process._status_lock = threading.Lock()
    payload = json.dumps({
        "kind": "refused", "code": "isolation-descendant-reap-failed",
        "why": "cleanup incomplete",
        "execution": {"kind": "finished", "status": "exited", "exit_code": 7},
    }).encode()
    monkeypatch.setattr(linux._StatusReader, "read_line", lambda *args: payload)
    process._finish_status()

    assert process._done.is_set()
    assert process._status_refusal.code == "isolation-descendant-reap-failed"
    assert process._status_refusal.detail == {"execution_status": "exited", "exit_code": 7}


def test_namespace_status_documents_are_exact_and_typed():
    ready = json.dumps({"kind": "ready", "pid": 7}).encode()
    terminal = json.dumps({
        "kind": "finished", "status": "exited", "exit_code": 2,
    }).encode()

    assert linux._ready_status(ready)["pid"] == 7
    assert linux._terminal_status(terminal)["exit_code"] == 2
    assert linux._terminal_status(json.dumps({
        "kind": "refused",
        "code": "isolation-descendant-reap-failed",
        "why": "the process tree remained live",
    }).encode())["code"] == "isolation-descendant-reap-failed"
    with pytest.raises(ValueError, match="ready status"):
        linux._ready_status(
            json.dumps({"kind": "ready", "pid": True}).encode())
    with pytest.raises(ValueError, match="exit status"):
        linux._terminal_status(json.dumps({
            "kind": "finished", "status": "completed", "exit_code": 3,
        }).encode())


def test_descendant_cleanup_must_empty_the_namespace(monkeypatch):
    calls = {"kill": 0, "reap": 0}
    snapshots = iter(((2,), (2,), ()))

    monkeypatch.setattr(linux_child, "_kill_tree", lambda: calls.__setitem__(
        "kill", calls["kill"] + 1))
    monkeypatch.setattr(linux_child, "_reap", lambda **_kwargs: calls.__setitem__(
        "reap", calls["reap"] + 1))
    monkeypatch.setattr(linux_child, "_pids", lambda: next(snapshots, ()))
    monkeypatch.setattr(linux_child.time, "sleep", lambda _seconds: None)

    assert linux_child._terminate_descendants(timeout_s=1.0, poll_seconds=0.01) is True
    assert calls["kill"] == 1
    assert calls["reap"] >= 2


def test_descendant_cleanup_returns_without_sleep_after_last_reap(monkeypatch):
    alive = [True]
    monkeypatch.setattr(linux_child, "_kill_tree", lambda: None)
    monkeypatch.setattr(linux_child, "_pids", lambda: (2,) if alive[0] else ())
    monkeypatch.setattr(linux_child, "_reap", lambda: alive.__setitem__(0, False))
    monkeypatch.setattr(linux_child.time, "sleep", lambda _seconds: pytest.fail(
        "an empty namespace must not wait for another polling interval"))

    assert linux_child._terminate_descendants(timeout_s=5, poll_seconds=0.05)


def test_descendant_cleanup_poll_never_extends_remaining_deadline(monkeypatch):
    clock = [10.0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(linux_child, "_kill_tree", lambda: None)
    monkeypatch.setattr(linux_child, "_pids", lambda: (2,))
    monkeypatch.setattr(linux_child, "_reap", lambda: None)
    monkeypatch.setattr(linux_child.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(linux_child.time, "sleep", sleep)

    assert not linux_child._terminate_descendants(timeout_s=0.25, poll_seconds=5)
    assert waits == [0.25]
    assert clock[0] == 10.25


def test_descendant_cleanup_stop_time_belongs_to_the_same_deadline(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(linux_child.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(linux_child, "_kill_tree", lambda: clock.__setitem__(0, 15.0))
    monkeypatch.setattr(linux_child, "_pids", lambda: (2,))
    monkeypatch.setattr(linux_child, "_reap", lambda: None)
    monkeypatch.setattr(linux_child.time, "sleep", lambda _seconds: pytest.fail(
        "stopping the process tree already consumed the cleanup allowance"))

    assert not linux_child._terminate_descendants(timeout_s=5, poll_seconds=0.05)


def test_namespace_supervision_receives_shared_runtime_settings(tmp_path, monkeypatch):
    from behave.core import runtime_defaults as defaults

    library = tmp_path / "libseccomp.so"
    library.write_bytes(b"fixture-library")
    monkeypatch.setattr(defaults, "SERVICE_CLEANUP_SECONDS", 7)
    monkeypatch.setattr(defaults, "ISOLATION_COMPLETION_POLL_SECONDS", 0.03)
    wire = linux._wire_spec(_spec(), str(tmp_path / "root"), str(library))
    assert wire["cleanup_seconds"] == 7
    assert wire["poll_seconds"] == 0.03


def test_readonly_mount_scaffolding_has_no_private_storage_quota(tmp_path, monkeypatch):
    mounts = []
    root = str(tmp_path / "root")
    monkeypatch.setattr(linux_child, "_mount", lambda *args: mounts.append(args))
    monkeypatch.setattr(linux_child.os, "chroot", lambda path: None)
    monkeypatch.setattr(linux_child.os, "chdir", lambda path: None)
    linux_child._setup({
        "root": root, "binaries": [], "read_only": [], "devices": [], "masked": [],
        "writable": [{"target": "/work", "bytes": 1234}], "cwd": "/work",
    })
    assert mounts[1] == ("tmpfs", root, "tmpfs",
                         linux_child.MS_NOSUID | linux_child.MS_NODEV, "mode=0755")
    # This does not remove the operator's writable-data budget or the final
    # read-only root. The obsolete 16 MiB cap concerned mountpoints only.
    assert any(row[-1] == "size=1234,mode=0700" for row in mounts)
    assert mounts[-1] == (None, root, None,
                          linux_child.MS_REMOUNT | linux_child.MS_RDONLY |
                          linux_child.MS_NOSUID | linux_child.MS_NODEV)


def test_cached_attestation_has_no_record_size_quota(tmp_path):
    from types import SimpleNamespace

    source = tmp_path / "source"
    source.write_bytes(b"fixture")
    manifest = content_attestation.metadata_manifest(source, allow_symlinks=False)
    document = content_attestation._document(str(source), False, _A, manifest)
    record = tmp_path / "record"
    content_attestation._write_record(record, document)
    info = record.stat()

    class LargeRecord:
        # Exercise the size check without allocating a 256 MiB JSON fixture.
        def lstat(self):
            return SimpleNamespace(st_mode=info.st_mode, st_uid=info.st_uid,
                                   st_nlink=1, st_size=(256 << 20) + 1)

        def read_text(self, **kwargs):
            return record.read_text(**kwargs)

    assert content_attestation._read_record(LargeRecord(), str(source), False) == (_A, manifest)


def test_process_cpu_accounting_includes_waited_children_and_spaced_names():
    # Fields 14--17 follow eleven fields after the parenthesised process name.
    row = "17 (a worker name) S " + " ".join(
        ["0"] * 10 + ["2", "3", "5", "7"] + ["0"] * 8)

    assert linux_child._stat_cpu_ticks(row) == 17


def test_device_type_cannot_expose_an_ordinary_host_file(tmp_path):
    ordinary = tmp_path / "not-a-device"
    ordinary.write_text("host bytes")

    with pytest.raises(IsolationUnavailable) as caught:
        linux._verify_device(str(ordinary))

    assert caught.value.refusal.code == "isolation-device-not-a-device"


def test_refusal_detail_is_frozen():
    refusal = IsolationRefusal("no", "why", {"capability": False})
    with pytest.raises(TypeError):
        refusal.detail["capability"] = True


def test_tree_identity_covers_descendant_bytes_and_executable_bits(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    leaf = tree / "tool"
    leaf.write_text("first")
    leaf.chmod(0o644)
    first = content_digest(str(tree))

    leaf.write_text("second")
    second = content_digest(str(tree))
    assert second != first

    leaf.chmod(0o755)
    assert content_digest(str(tree)) != second


@pytest.mark.parametrize("directory", [False, True])
def test_metadata_manifest_reads_each_entry_once(tmp_path, monkeypatch, directory):
    tree = tmp_path / "runtime"
    if directory:
        tree.mkdir()
        (tree / "nested").mkdir()
        (tree / "nested/module.py").write_text("value = 1\n")
        (tree / "link").symlink_to("nested/module.py")
        paths = (tree, tree / "link", tree / "nested", tree / "nested/module.py")
    else:
        tree.write_text("value = 1\n")
        paths = (tree,)
    expected = content_attestation.metadata_manifest(tree, allow_symlinks=True)
    original = os.lstat
    reads = []

    def counted(path):
        reads.append(Path(path))
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "lstat", counted)
        actual = content_attestation.metadata_manifest(tree, allow_symlinks=True)
    assert actual == expected
    assert reads == list(paths)


def test_reusable_runtime_rechecks_every_descendant_without_rereading_bytes(
    tmp_path, monkeypatch,
):
    cache = tmp_path / "attestations"
    monkeypatch.setattr(content_attestation, "_CACHE_ROOT", cache)
    monkeypatch.setattr(content_attestation, "_METADATA_SETTLE_NS", 0)
    tree = tmp_path / "runtime"
    tree.mkdir()
    leaf = tree / "module.py"
    leaf.write_text("first")
    root_times = (tree.stat().st_atime_ns, tree.stat().st_mtime_ns)

    scans = 0
    byte_digest = isolation_content._content_digest

    def counted(*args, **kwargs):
        nonlocal scans
        scans += 1
        return byte_digest(*args, **kwargs)

    monkeypatch.setattr(isolation_content, "_content_digest", counted)
    first = read_only(
        str(tree), "/runtime/tree", reuse_attestation=True)
    again = read_only(
        str(tree), "/runtime/tree", reuse_attestation=True)

    assert again.sha256 == first.sha256
    assert scans == 2

    # Restoring the root timestamp does not hide an atomic descendant replacement:
    # the complete manifest sees the leaf's new inode and metadata.
    replacement = tmp_path / "replacement.py"
    replacement.write_text("other")
    replacement.replace(leaf)
    os.utime(tree, ns=root_times)
    changed = read_only(
        str(tree), "/runtime/tree", reuse_attestation=True)
    assert changed.sha256 != first.sha256
    assert scans == 4


def test_corrupt_runtime_attestation_is_a_cache_miss(tmp_path, monkeypatch):
    cache = tmp_path / "attestations"
    monkeypatch.setattr(content_attestation, "_CACHE_ROOT", cache)
    monkeypatch.setattr(content_attestation, "_METADATA_SETTLE_NS", 0)
    source = tmp_path / "runtime.py"
    source.write_text("value = 1\n")

    scans = 0
    byte_digest = isolation_content._content_digest

    def counted(*args, **kwargs):
        nonlocal scans
        scans += 1
        return byte_digest(*args, **kwargs)

    monkeypatch.setattr(isolation_content, "_content_digest", counted)
    expected = read_only(
        str(source), "/runtime/module.py", reuse_attestation=True).sha256
    record = next(cache.glob("*.json"))
    document = json.loads(record.read_text())
    document["digest"] = "0" * 64
    record.write_text(json.dumps(document))
    # Model a fresh verifier process: a warm process correctly retains the
    # already-validated in-memory attestation and need not reread its disk cache.
    content_attestation._MEMORY.clear()

    got = read_only(
        str(source), "/runtime/module.py", reuse_attestation=True).sha256
    assert got == expected
    assert scans == 4


def test_runtime_changed_during_freeze_is_never_published(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        content_attestation, "_CACHE_ROOT", tmp_path / "attestations")
    source = tmp_path / "runtime.py"
    source.write_text("value = 1\n")
    byte_digest = isolation_content._content_digest
    scans = 0

    def change_after_first_scan(*args, **kwargs):
        nonlocal scans
        scans += 1
        digest = byte_digest(*args, **kwargs)
        if scans == 1:
            source.write_text("value = 2\n")
        return digest

    monkeypatch.setattr(
        isolation_content, "_content_digest", change_after_first_scan)
    mounted = read_only(
        str(source), "/runtime/module.py", reuse_attestation=True)

    assert scans in {3, 4}
    assert mounted.sha256 == content_digest(str(source))


def test_reusable_runtime_cache_has_cross_thread_singleflight(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        content_attestation, "_CACHE_ROOT", tmp_path / "attestations")
    monkeypatch.setattr(content_attestation, "_METADATA_SETTLE_NS", 0)
    source = tmp_path / "runtime"
    source.mkdir()
    for index in range(32):
        (source / f"module_{index}.py").write_text(f"VALUE = {index}\n")
    byte_digest = isolation_content._content_digest
    scan_lock = threading.Lock()
    scans = 0
    metadata_walks = 0
    metadata_manifest = content_attestation.metadata_manifest

    def counted(*args, **kwargs):
        nonlocal scans
        with scan_lock:
            scans += 1
        time.sleep(0.02)
        return byte_digest(*args, **kwargs)

    monkeypatch.setattr(isolation_content, "_content_digest", counted)

    def counted_metadata(*args, **kwargs):
        nonlocal metadata_walks
        with scan_lock:
            metadata_walks += 1
        time.sleep(0.02)
        return metadata_manifest(*args, **kwargs)

    monkeypatch.setattr(
        content_attestation, "metadata_manifest", counted_metadata)
    results = []

    def attest():
        results.append(read_only(
            str(source), "/runtime/tree", reuse_attestation=True).sha256)

    threads = [threading.Thread(target=attest) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(results)) == 1
    assert scans == 2
    assert metadata_walks == 2


def test_reusable_runtime_attestation_is_shared_across_processes(tmp_path):
    cache = tmp_path / "attestations"
    source = tmp_path / "runtime"
    source.mkdir()
    for index in range(16):
        (source / f"module_{index}.py").write_text(f"VALUE = {index}\n")

    context = multiprocessing.get_context("spawn")
    with context.Pool(8) as pool:
        results = pool.map(
            _attest_in_fresh_process,
            [(str(cache), str(source))] * 8,
        )

    assert len(set(results)) == 1
    assert len(tuple(cache.glob("*.json"))) == 1


def test_reusable_runtime_launch_refuses_changed_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        content_attestation, "_CACHE_ROOT", tmp_path / "attestations")
    source = tmp_path / "runtime.py"
    source.write_text("value = 1\n")
    mounted = read_only(
        str(source), "/runtime/module.py", reuse_attestation=True)
    source.write_text("value = 2\n")

    with pytest.raises(IsolationUnavailable) as caught:
        linux._verify_source(
            mounted.source,
            mounted.sha256,
            reuse_attestation=mounted.reuse_attestation,
        )
    assert caught.value.refusal.code == "isolation-input-changed"


def test_symlink_policy_is_explicit_and_part_of_identity(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "payload").write_text("bytes")
    (tree / "alias").symlink_to("payload")

    with pytest.raises(ValueError, match="symlink"):
        read_only(str(tree), "/runtime/tree")
    mounted = read_only(
        str(tree), "/runtime/tree", allow_symlinks=True)
    assert mounted.allow_symlinks is True
    assert mounted.identity()["allow_symlinks"] is True


def test_ldd_is_resolved_only_from_the_trusted_system_path(
    tmp_path, monkeypatch,
):
    tool = tmp_path / "ldd"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    looked_up = []
    launched = []

    def which(name, *, path):
        looked_up.append((name, path))
        return str(tool)

    def run(command, **options):
        launched.append((command, options))
        return isolation_content.subprocess.CompletedProcess(
            command, 0, stdout="", stderr="")

    monkeypatch.setenv("PATH", "/ambient/untrusted")
    monkeypatch.setattr(isolation_content.shutil, "which", which)
    monkeypatch.setattr(isolation_content.subprocess, "run", run)

    assert isolation_content._ldd_closure(("/runtime/python",)) == ()
    assert looked_up == [("ldd", isolation_content.os.defpath)]
    assert launched[0][0] == [str(tool.resolve()), "/runtime/python"]
    assert launched[0][1]["env"] == {"PATH": isolation_content.os.defpath}


def test_missing_system_ldd_refuses_before_launch(monkeypatch):
    monkeypatch.setattr(
        isolation_content.shutil, "which", lambda _name, *, path: None)
    monkeypatch.setattr(
        isolation_content.subprocess,
        "run",
        lambda *_args, **_options: pytest.fail(
            "a missing trusted ldd must not launch anything"),
    )

    with pytest.raises(RuntimeError, match="system tool 'ldd' is unavailable"):
        isolation_content._ldd_closure(("/runtime/python",))


def test_ldd_normalizes_relocatable_runtime_paths_without_resolving_symlinks(
    monkeypatch,
):
    completed = isolation_content.subprocess.CompletedProcess(
        ["ldd"],
        0,
        stdout=(
            "libpython3.11.so => /runtime/python/bin/../lib/libpython3.11.so\n"
            "/lib64/ld-linux-x86-64.so.2\n"
        ),
        stderr="",
    )
    monkeypatch.setattr(
        isolation_content, "_system_executable", lambda _name: "/usr/bin/ldd")
    monkeypatch.setattr(
        isolation_content.subprocess, "run", lambda *_args, **_options: completed)
    monkeypatch.setattr(isolation_content.os.path, "isfile", lambda _path: True)

    assert isolation_content._ldd_closure(("/runtime/python",)) == (
        "/lib64/ld-linux-x86-64.so.2",
        "/runtime/python/lib/libpython3.11.so",
    )


def test_python_runtime_mounts_an_immutable_bytecode_free_stdlib(
    tmp_path, monkeypatch,
):
    executable = tmp_path / "python"
    executable.write_text("runtime", encoding="utf-8")
    executable.chmod(0o755)
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    (stdlib / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    cache = stdlib / "__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"derived")
    calls = []

    def image(source, target, **options):
        calls.append((source, target))
        return read_only(str(stdlib / "module.py"), target)

    monkeypatch.setattr(
        isolation_content.sysconfig, "get_path", lambda name: str(stdlib))
    monkeypatch.setattr(
        isolation_content.sysconfig, "get_config_var", lambda _name: None)
    monkeypatch.setattr(isolation_content, "_ldd_closure", lambda _files: ())
    monkeypatch.setattr(isolation_content, "binary", lambda source, target,
                        **_options: Binary(source, target, _A))
    monkeypatch.setattr(
        "behave.isolation.package_image.python_package", image)
    _binaries, mounts, _masked = isolation_content.python_runtime(str(executable))

    guest = f"/runtime/python/lib/python{isolation_content.sys.version_info.major}." \
            f"{isolation_content.sys.version_info.minor}"
    assert calls == [(str(stdlib.resolve()), guest)]
    assert mounts[0].target == guest
