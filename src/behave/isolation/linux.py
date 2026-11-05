"""Linux user-namespace isolation backend.

The backend has no host-process compatibility mode.  If any required namespace,
mount, seccomp or supervision capability is absent, ``start`` returns the typed
capability refusal through ``IsolationUnavailable``.
"""

from __future__ import annotations

import ctypes.util
import io
import json
import math
import os
import select
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import traceback
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root

from .content import _content_digest, binary, content_digest, native_runtime, read_only
from .content_attestation import reusable_content_digest
from .model import (
    BackendCapabilities,
    IsolationRefusal,
    IsolationResult,
    IsolationSpec,
    IsolationStatus,
    IsolationUnavailable,
    NetworkMode,
    ResourceLimits,
    WritableRoot,
)
from .preparation_control import PreparationCancelled, check_preparation
from .startup import acquire_startup

_BACKEND = "linux-namespace/8"
_STATUS_BYTES = defaults.ISOLATION_STATUS_BYTES

_TERMINAL_STATUSES = {
    "completed",
    "exited",
    "signaled",
    "timed-out",
    "cpu-limit",
    "memory-limit",
    "process-limit",
    "output-limit",
    "protocol-limit",
    "file-limit",
}


class IsolationOutputLimit(RuntimeError):
    """A stream exceeded the bound fixed before launch."""


class IsolationCleanupError(RuntimeError):
    """The caller must not reuse resources whose owner has not been reaped."""


class _StatusReader:
    """One pipe owner retains both startup and terminal message framing.

    Reading one byte per poll needlessly yields the GIL twice per character
    while other replay lanes construct large records. Read available chunks,
    preserving a coalesced terminal message across the startup handoff.
    """

    def __init__(self, fd: int):
        self.fd = fd
        self.pending = bytearray()
        self.poller = select.poll()
        self.poller.register(fd, select.POLLIN)

    def read_line(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            newline = self.pending.find(b"\n")
            size = len(self.pending) if newline < 0 else newline + 1
            if _STATUS_BYTES is not None and size > _STATUS_BYTES:
                raise ValueError("the namespace status exceeds its protocol budget")
            if newline >= 0:
                payload = bytes(self.pending[:size])
                del self.pending[:size]
                return payload
            # At the deadline drain only ready bytes, without a new wait. A
            # buffered receipt still names the outcome even if we ran late.
            remaining = max(0.0, deadline - time.monotonic())
            if not self.poller.poll(math.ceil(1000 * remaining)):
                raise TimeoutError("the namespace status did not arrive before the deadline")
            chunk = os.read(self.fd, io.DEFAULT_BUFFER_SIZE)
            if not chunk:
                payload = bytes(self.pending)
                self.pending.clear()
                return payload
            self.pending.extend(chunk)


def _status(payload: bytes) -> dict[str, object]:
    try:
        got = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("malformed namespace status") from None
    if type(got) is not dict or type(got.get("kind")) is not str:
        raise ValueError("malformed namespace status")
    return got


def _ready_status(payload: bytes) -> dict[str, object]:
    got = _status(payload)
    if got.get("kind") == "ready":
        if set(got) != {"kind", "pid"} \
                or type(got["pid"]) is not int or got["pid"] <= 0:
            raise ValueError("malformed namespace ready status")
        return got
    if got.get("kind") == "refused":
        if set(got) != {"kind", "code", "why"} \
                or type(got["code"]) is not str or not got["code"] \
                or type(got["why"]) is not str:
            raise ValueError("malformed namespace refusal status")
        return got
    raise ValueError("unexpected namespace setup status")


def _terminal_status(payload: bytes) -> dict[str, object]:
    got = _status(payload)
    if got.get("kind") == "refused":
        if set(got) - {"execution", "cleanup"} != {"kind", "code", "why"} \
                or type(got["code"]) is not str or not got["code"] \
                or type(got["why"]) is not str or not got["why"]:
            raise ValueError("malformed namespace terminal refusal")
        if "execution" in got:
            _finished_status(got["execution"])
        if "cleanup" in got:
            cleanup = got["cleanup"]
            if type(cleanup) is not dict or set(cleanup) != {"state", "why"} \
                    or cleanup["state"] != "unconfirmed" \
                    or type(cleanup["why"]) is not str or not cleanup["why"]:
                raise ValueError("malformed namespace cleanup status")
        return got
    return _finished_status(got)


def _finished_status(got: object) -> dict[str, object]:
    if type(got) is not dict:
        raise ValueError("malformed namespace terminal status")
    status = got.get("status")
    if got.get("kind") != "finished" or status not in _TERMINAL_STATUSES:
        raise ValueError("malformed namespace terminal status")
    if status in {"completed", "exited"}:
        if set(got) != {"kind", "status", "exit_code"} \
                or type(got["exit_code"]) is not int \
                or (status == "completed") != (got["exit_code"] == 0):
            raise ValueError("malformed namespace exit status")
    elif status == "signaled":
        if set(got) != {"kind", "status", "signal"} \
                or type(got["signal"]) is not int or got["signal"] <= 0:
            raise ValueError("malformed namespace signal status")
    elif set(got) != {"kind", "status"}:
        raise ValueError("malformed namespace limit status")
    return got


def _write_all(fd: int, payload: bytes) -> None:
    """Write one bounded helper document without assuming a one-shot pipe write."""

    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("the namespace specification pipe stopped accepting bytes")
        view = view[written:]


class _BoundedBinaryReader:
    def __init__(self, stream: BinaryIO, limit: int | None, owner: SandboxProcess, name: str):
        self._stream = stream
        self._limit = limit
        self._owner = owner
        self._name = name
        self._seen = 0

    def _account(self, payload: bytes) -> bytes:
        self._seen += len(payload)
        if self._limit is not None and self._seen > self._limit:
            self._owner._output_limited(self._name)
            raise IsolationOutputLimit(f"{self._name} exceeded {self._limit} bytes")
        return payload

    def read(self, size: int = -1) -> bytes:
        if self._limit is not None and (size < 0 or size > self._limit - self._seen + 1):
            size = self._limit - self._seen + 1
        return self._account(self._stream.read(size))

    def readline(self, size: int = -1) -> bytes:
        if self._limit is not None and (size < 0 or size > self._limit - self._seen + 1):
            size = self._limit - self._seen + 1
        return self._account(self._stream.readline(size))

    def read1(self, size: int = -1) -> bytes:
        if self._limit is not None and (size < 0 or size > self._limit - self._seen + 1):
            size = self._limit - self._seen + 1
        return self._account(self._stream.read1(size))

    def fileno(self) -> int:
        return self._stream.fileno()

    def close(self) -> None:
        self._stream.close()

    @property
    def closed(self) -> bool:
        return self._stream.closed


class _TextReader:
    def __init__(self, binary: _BoundedBinaryReader):
        self.buffer = binary

    def readline(self, size: int = -1) -> str:
        return self.buffer.readline(size).decode("utf-8", errors="strict")

    def read(self, size: int = -1) -> str:
        return self.buffer.read(size).decode("utf-8", errors="strict")

    def close(self) -> None:
        self.buffer.close()

    @property
    def closed(self) -> bool:
        return self.buffer.closed


class _BoundedBinaryWriter:
    """A pipe writer whose cumulative payload is fixed by the policy."""

    def __init__(self, stream: BinaryIO, limit: int | None, owner: SandboxProcess):
        self._stream = stream
        self._limit = limit
        self._owner = owner
        self._seen = 0

    def write(self, payload: bytes) -> int:
        if type(payload) is not bytes:
            raise TypeError("an isolated binary protocol accepts exact bytes")
        if self._limit is not None and self._seen + len(payload) > self._limit:
            self._owner._protocol_limited()
            raise IsolationOutputLimit(
                f"protocol input exceeded {self._limit} bytes")
        written = self._stream.write(payload)
        if written is None:
            written = len(payload)
        self._seen += written
        return written

    def flush(self) -> None:
        self._stream.flush()

    def close(self) -> None:
        self._stream.close()

    @property
    def closed(self) -> bool:
        return self._stream.closed


class _TextWriter:
    def __init__(self, binary: _BoundedBinaryWriter):
        self.buffer = binary

    def write(self, payload: str) -> int:
        if type(payload) is not str:
            raise TypeError("an isolated text protocol accepts exact strings")
        self.buffer.write(payload.encode("utf-8", errors="strict"))
        return len(payload)

    def flush(self) -> None:
        self.buffer.flush()

    def close(self) -> None:
        self.buffer.close()

    @property
    def closed(self) -> bool:
        return self.buffer.closed


def _supervisor_wait_seconds(spec: IsolationSpec) -> float:
    # The child can detect its execution deadline on the next poll, then spend
    # the configured cleanup allowance reaping descendants. A separate grace
    # setting could cut that short and hide the original terminal status.
    return (spec.limits.wall_seconds + defaults.SERVICE_CLEANUP_SECONDS
            + defaults.ISOLATION_COMPLETION_POLL_SECONDS)


class SandboxProcess:
    """A Popen-shaped handle whose lifetime owner is the namespace supervisor."""

    def __init__(
        self,
        popen: subprocess.Popen[bytes],
        status_reader: _StatusReader,
        root: str,
        spec: IsolationSpec,
        capabilities: BackendCapabilities,
        *,
        text: bool,
    ):
        self._popen = popen
        self._status_reader = status_reader
        self._root = root
        self._spec = spec
        self._capabilities = capabilities
        self._final: dict[str, object] | None = None
        self._status_refusal: IsolationRefusal | None = None
        self._limit_status: str | None = None
        self._done = threading.Event()
        self._status_lock = threading.Lock()
        stdout = _BoundedBinaryReader(popen.stdout, spec.limits.stdout_bytes, self, "stdout")
        stderr = _BoundedBinaryReader(popen.stderr, spec.limits.stderr_bytes, self, "stderr")
        self.stdout = _TextReader(stdout) if text else stdout
        self.stderr = _TextReader(stderr) if text else stderr
        stdin = _BoundedBinaryWriter(
            popen.stdin, spec.limits.protocol_bytes, self)
        self.stdin = _TextWriter(stdin) if text else stdin

    @property
    def pid(self) -> int:
        return self._popen.pid

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._capabilities

    @property
    def policy_digest(self) -> str:
        return self._spec.digest

    def _finish_status(self, timeout: float | None = None) -> bool:
        """Collect status on its caller, not a separately scheduled thread.

        A ready receipt or EOF after termination must not become a cleanup
        failure because a background reader has not run. Concurrent callers
        share one framed reader and the caller's original time allowance.
        """
        if self._done.is_set():
            return True
        timeout = _supervisor_wait_seconds(self._spec) if timeout is None else timeout
        began = time.monotonic()
        if not self._status_lock.acquire(blocking=False):
            if not self._status_lock.acquire(timeout=timeout):
                return False
            timeout = max(0.0, timeout - (time.monotonic() - began))
        try:
            return self._done.is_set() or self._receive_status(timeout)
        finally:
            self._status_lock.release()

    def _receive_status(self, timeout: float) -> bool:
        terminal = False
        try:
            try:
                payload = self._status_reader.read_line(timeout)
            except TimeoutError:
                return False  # A poll/wait timeout is not an invalid receipt.
            terminal = True
            if not payload:
                raise EOFError("the namespace supervisor closed its status pipe")
            self._final = _terminal_status(payload)
            if self._final.get("kind") == "refused":
                execution = self._final.get("execution")
                detail = ({} if execution is None else {
                    "execution_status": execution["status"],
                    **{name: execution[name] for name in ("exit_code", "signal") if name in execution},
                })
                if "cleanup" in self._final:
                    detail["cleanup"] = self._final["cleanup"]
                self._status_refusal = IsolationRefusal(
                    str(self._final["code"]), str(self._final["why"]), detail,
                )
        except (EOFError, ValueError, OSError) as error:
            terminal = True
            # Output/protocol-limit handling deliberately kills the supervisor; the
            # already-recorded limit remains authoritative.  Any other missing or
            # malformed status is a judge failure, never a candidate signal result.
            if self._limit_status is None:
                self._status_refusal = IsolationRefusal(
                    "isolation-supervisor-failed",
                    "the namespace supervisor returned no valid terminal status",
                    {"error": f"{type(error).__name__}: {error}"},
                )
            # Publish the cause; wait/kill/close retain the original deadline
            # and remain responsible for reaping the launcher.
            self._request_stop()
            self._final = {
                "kind": "finished",
                "status": "signaled",
                "signal": signal.SIGKILL,
            }
        finally:
            if terminal:
                try:
                    os.close(self._status_reader.fd)
                except OSError:
                    pass
                self._done.set()
        return True

    def _output_limited(self, name: str) -> None:
        self._limit_status = "output-limit"
        self._request_stop()

    def _protocol_limited(self) -> None:
        self._limit_status = "protocol-limit"
        self._request_stop()

    def _request_stop(self) -> None:
        """Signal termination without giving a callback its own reap allowance."""
        if self._popen.poll() is None:
            try:
                os.killpg(self._popen.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _returncode(self) -> int | None:
        if self._limit_status is not None:
            return -signal.SIGKILL
        final = self._final
        if final is None:
            return None
        status = final.get("status")
        if status == "completed":
            return 0
        if status == "exited":
            return int(final.get("exit_code", 1))
        if status == "signaled":
            return -int(final.get("signal", signal.SIGKILL))
        return -signal.SIGKILL

    def poll(self) -> int | None:
        if self._finish_status(0.0):
            return self._returncode()
        return self._popen.poll()

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        if not self._finish_status(timeout):
            raise subprocess.TimeoutExpired(self._spec.argv, timeout)
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        # Like Popen.wait(), this only waits. The lifetime owner decides when
        # to stop and reap; waiting must not quietly grant another cleanup budget.
        self._popen.wait(timeout=remaining)
        return self._returncode() or 0

    def kill(self, *, deadline: float | None = None) -> None:
        if deadline is None:
            deadline = time.monotonic() + defaults.SERVICE_CLEANUP_SECONDS
        self._request_stop()
        try:
            self._popen.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        self._finish_status(max(0.0, deadline - time.monotonic()))

    def close(self, *, deadline: float | None = None) -> None:
        self.kill(deadline=deadline)
        # kill() requests termination and waits within the owner's allowance.
        # close() is the owner's cleanup receipt: it must not report success
        # while the namespace launcher or status reader may still be alive.
        returncode = self._popen.poll()
        status_done = self._done.is_set()
        if returncode is None or not status_done:
            raise IsolationCleanupError(
                f"isolated process cleanup is unconfirmed; launcher_returncode={returncode}, "
                f"status_reader_finished={status_done}; retained workspace: {self._root}")
        for stream in (self.stdin, self.stdout, self.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        shutil.rmtree(self._root, ignore_errors=True)

    @property
    def final_status(self) -> str | None:
        if self._limit_status is not None:
            return self._limit_status
        self._finish_status(0.0)
        if self._status_refusal is not None:
            return IsolationStatus.REFUSED.value
        return None if self._final is None else str(self._final.get("status"))

    @property
    def status_refusal(self) -> IsolationRefusal | None:
        self._finish_status(0.0)
        return self._status_refusal


def _seccomp_path() -> str | None:
    # Normal installations need no ldconfig/compiler subprocess. In particular,
    # fork exhaustion must not make an installed library appear to be missing.
    multiarch = sysconfig.get_config_var("MULTIARCH")
    directories = [Path("/lib64"), Path("/usr/lib64"), Path("/lib"), Path("/usr/lib")]
    if multiarch:
        directories[:0] = [Path("/lib") / multiarch, Path("/usr/lib") / multiarch]

    def resolve(name: str) -> str | None:
        candidates = [Path(name)] if os.path.isabs(name) else [root / name for root in directories]
        return next((os.path.realpath(path) for path in candidates if path.is_file()), None)

    installed = resolve("libseccomp.so.2")
    if installed is not None:
        return installed
    # Preserve platform discovery for installations with another library name.
    name = ctypes.util.find_library("seccomp")
    return None if name is None else resolve(name)


def _backend_requirements() -> BackendCapabilities | IsolationRefusal:
    """Installed prerequisites; the real launch proves namespace/setup support."""
    if sys.platform != "linux":
        return IsolationRefusal("isolation-linux-required", "the Linux namespace backend is unavailable")
    unshare = "/usr/bin/unshare"
    seccomp = _seccomp_path()
    if not os.path.isfile(unshare) or seccomp is None:
        return IsolationRefusal(
            "isolation-capability-missing",
            "the backend requires unshare and libseccomp",
            {"unshare": os.path.isfile(unshare), "seccomp": seccomp is not None},
        )
    return BackendCapabilities(
        _BACKEND,
        os.uname().release,
        (
            "chroot", "empty-environment", "ipc-namespace", "mount-read-only",
            "network-namespace", "no-new-privileges", "pid-namespace",
            "pid1-tree-supervisor", "resource-monitor", "recursive-read-only",
            "seccomp-network-denial", "target-setup-handshake",
            "tree-cpu-accounting", "user-namespace", "uts-namespace",
        ),
    )


@lru_cache(maxsize=1)
def capabilities() -> BackendCapabilities | IsolationRefusal:
    """Cached minimal execution diagnostic, not a prerequisite for every launch.

    ``start`` must establish its own real isolation boundary and ready handshake
    regardless of whether an earlier probe succeeded. Merely entering namespaces
    does not establish mount, procfs, seccomp or supervised execution support.
    Reuse the real launch and cleanup path here, without probing before each job.
    """
    available = _backend_requirements()
    if type(available) is IsolationRefusal:
        return available
    try:
        executable = binary("/bin/true", "/bin/true")
        checked = run(IsolationSpec(
            argv=(executable.target,),
            binaries=(executable,),
            read_only=native_runtime((executable.source,)),
            writable=(WritableRoot("/work", 4096),),
            limits=ResourceLimits(
                wall_seconds=defaults.ISOLATION_CAPABILITY_PROBE_SECONDS,
                cpu_seconds=defaults.ISOLATION_CAPABILITY_PROBE_SECONDS,
                rss_bytes=64 << 20,
                processes=1,
                file_bytes=4096,
                stdout_bytes=64 << 10,
                stderr_bytes=64 << 10,
                protocol_bytes=64 << 10,
            ),
        ))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        return IsolationRefusal(
            "isolation-capability-probe-failed",
            "the minimal isolated execution probe failed",
            {"error": f"{type(error).__name__}: {error}"},
        )
    if checked.refusal is not None:
        return checked.refusal
    if checked.status is not IsolationStatus.COMPLETED or checked.exit_code != 0:
        return IsolationRefusal(
            "isolation-capability-probe-failed",
            "the minimal isolated execution did not complete successfully",
            {"status": checked.status.value,
             "exit_code": checked.exit_code,
             "signal": checked.signal,
             "diagnostic": checked.stderr.decode("utf-8", errors="replace")},
        )
    return available


def _verify_source(
    source: str,
    digest: str,
    *,
    executable: bool = False,
    allow_symlinks: bool = False,
    reuse_attestation: bool = False,
) -> None:
    if not os.path.exists(source):
        raise IsolationUnavailable(
            IsolationRefusal(
                "isolation-input-missing",
                "a declared isolation input is missing",
                {"source": source},
            )
        )
    if executable and not os.access(source, os.X_OK):
        raise IsolationUnavailable(
            IsolationRefusal(
                "isolation-binary-not-executable",
                "a declared binary is not executable",
                {"source": source},
            )
        )
    try:
        actual = (
            reusable_content_digest(
                source,
                allow_symlinks=allow_symlinks,
                byte_digest=_content_digest,
            )
            if reuse_attestation
            else content_digest(source, allow_symlinks=allow_symlinks)
        )
    except (OSError, ValueError) as error:
        raise IsolationUnavailable(
            IsolationRefusal(
                "isolation-input-unverifiable",
                "a declared input cannot be frozen",
                {"source": source, "error": type(error).__name__},
            )
        ) from None
    if actual != digest:
        raise IsolationUnavailable(
            IsolationRefusal(
                "isolation-input-changed",
                "a declared input no longer has its frozen digest",
                {"source": source, "expected": digest, "actual": actual},
            )
        )


def _verify_device(source: str) -> None:
    """Require a declared device locator to name an actual device node."""

    try:
        mode = os.lstat(source).st_mode
    except OSError as error:
        raise IsolationUnavailable(
            IsolationRefusal(
                "isolation-device-missing",
                "a declared device is missing or unreadable",
                {"source": source, "error": type(error).__name__},
            )
        ) from None
    if stat.S_ISLNK(mode) or not (stat.S_ISCHR(mode) or stat.S_ISBLK(mode)):
        raise IsolationUnavailable(
            IsolationRefusal(
                "isolation-device-not-a-device",
                "a declared device must be a character or block device node",
                {"source": source},
            )
        )


def _wire_spec(spec: IsolationSpec, root: str, seccomp: str) -> dict[str, object]:
    def mounted(item: object) -> dict[str, object]:
        return {"source": item.source, "target": item.target}

    backend_mount = read_only(
        seccomp, seccomp, reuse_attestation=True)
    return {
        "version": "linux-namespace-wire/1",
        "root": root,
        "argv": list(spec.argv),
        "binaries": [mounted(item) for item in spec.binaries],
        "read_only": [mounted(item) for item in (*spec.read_only, backend_mount)],
        "writable": [{"target": item.target, "bytes": item.bytes, "source": item.source}
                     for item in spec.writable],
        "masked": [{"target": item.target} for item in spec.masked],
        "devices": [{"source": item.source, "target": item.target, "writable": item.writable} for item in spec.devices],
        "environment": [list(item) for item in spec.environment],
        "network": spec.network.value,
        "host_network_namespace": os.stat("/proc/self/ns/net").st_ino,
        "cwd": spec.cwd,
        "limits": spec.limits.as_dict(),
        "cleanup_seconds": defaults.SERVICE_CLEANUP_SECONDS,
        "poll_seconds": defaults.ISOLATION_COMPLETION_POLL_SECONDS,
        "seccomp_library": seccomp,
    }


def _prepare_start(spec):
    """Validate launch inputs before any process or owned descriptor exists."""
    if type(spec) is not IsolationSpec:
        raise TypeError("start needs an exact IsolationSpec")
    available = _backend_requirements()
    if type(available) is IsolationRefusal:
        raise IsolationUnavailable(available)
    if spec.backend != available.backend:
        raise IsolationUnavailable(
            IsolationRefusal(
                "isolation-backend-unavailable",
                "the requested backend identity is unavailable",
                {"requested": spec.backend, "available": available.backend},
            )
        )
    if spec.network not in (NetworkMode.DENIED, NetworkMode.PRIVATE_LOOPBACK):
        raise IsolationUnavailable(
            IsolationRefusal(
                "isolation-network-mode-unsupported",
                "this backend implements denied or private-loopback networking only",
            )
        )
    for item in spec.binaries:
        if item.prepared_runtime:
            continue
        _verify_source(
            item.source,
            item.sha256,
            executable=True,
            reuse_attestation=item.reuse_attestation,
        )
    for item in spec.read_only:
        if item.prepared_runtime:
            continue
        _verify_source(
            item.source,
            item.sha256,
            allow_symlinks=item.allow_symlinks,
            reuse_attestation=item.reuse_attestation,
        )
    for item in spec.devices:
        _verify_device(item.source)

    seccomp = _seccomp_path()
    if seccomp is None:
        raise IsolationUnavailable(
            IsolationRefusal(
                "isolation-capability-missing",
                "libseccomp disappeared before namespace setup",
            )
        )
    return available, seccomp


def start(spec: IsolationSpec, *, text: bool = False) -> SandboxProcess:
    """Start one isolated process tree or raise a typed capability refusal."""
    startup_deadline = time.monotonic() + spec.limits.wall_seconds
    try:
        available, seccomp = _prepare_start(spec)
    except PreparationCancelled as error:
        error.cleanup_confirmed = True
        raise
    root = tempfile.mkdtemp(prefix="behave-isolation-", dir=scratch_root())
    owned_fds = set()
    child = None
    startup = None

    def close_owned(fd):
        owned_fds.remove(fd)
        os.close(fd)

    helper = str(Path(__file__).with_name("_linux_child.py"))
    command = [
        "/usr/bin/unshare",
        "--user",
        "--map-root-user",
        "--mount",
        "--pid",
        "--fork",
        "--kill-child=KILL",
        "--net",
        "--ipc",
        "--uts",
        sys.executable,
        "-I",
        "-S",
        helper,
    ]
    try:
        startup = acquire_startup(deadline=startup_deadline)
        spec_r, spec_w = os.pipe()
        owned_fds.update((spec_r, spec_w))
        status_r, status_w = os.pipe()
        owned_fds.update((status_r, status_w))
        environment = {
            "BEHAVE_ISOLATION_SPEC_FD": str(spec_r),
            "BEHAVE_ISOLATION_STATUS_FD": str(status_w),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if startup.descriptor is not None:
            environment["BEHAVE_ISOLATION_STARTUP_FD"] = str(startup.descriptor)
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env=environment,
            pass_fds=(spec_r, status_w, *startup.pass_fds),
            start_new_session=True,
        )
        close_owned(spec_r)
        close_owned(status_w)
        encoded = json.dumps(_wire_spec(spec, root, seccomp), separators=(",", ":")).encode("utf-8")
        _write_all(spec_w, encoded)
        close_owned(spec_w)
        status_reader = _StatusReader(status_r)
        ready = _ready_status(status_reader.read_line(max(0.0, startup_deadline - time.monotonic())))
        if ready.get("kind") != "ready":
            refusal = IsolationRefusal(
                str(ready.get("code", "isolation-setup-failed")),
                str(ready.get("why", "the namespace helper refused setup")),
            )
            raise IsolationUnavailable(refusal)
        startup.ready()
        process = SandboxProcess(child, status_reader, root, spec, available, text=text)
        owned_fds.remove(status_r)  # The process now owns the status reader.
        return process
    except BaseException as error:
        # Closed numbers may already belong to another concurrent launch.
        # Only release descriptors this attempt still owns.
        for fd in owned_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        cleanup = None
        if child is not None:
            cleanup_deadline = time.monotonic() + defaults.SERVICE_CLEANUP_SECONDS
            try:
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
            except (OSError, subprocess.TimeoutExpired) as teardown:
                # A stop/reap error is secondary to the launch failure. Keep the
                # original cause and workspace when termination is unconfirmed.
                cleanup = {"state": "unconfirmed", "pid": child.pid,
                           "workspace": root,
                           "error": f"{type(teardown).__name__}: {teardown}"}
        if cleanup is None:
            shutil.rmtree(root, ignore_errors=True)
            if isinstance(error, TimeoutError):
                # Startup can use the enclosing operation's remaining lifetime.
                # Its timeout then means owner cancellation, not a broken host.
                # Re-check only after confirmed reap; genuine setup faults and
                # unconfirmed cleanup must retain their original diagnostics.
                try:
                    check_preparation()
                except PreparationCancelled as stopped:
                    stopped.cleanup_confirmed = True
                    raise
        if isinstance(error, PreparationCancelled):
            error.cleanup_confirmed = cleanup is None
        if type(error) is IsolationUnavailable:
            if cleanup is not None:
                refusal = error.refusal
                raise IsolationUnavailable(IsolationRefusal(
                    refusal.code, refusal.why,
                    {**refusal.detail, "cleanup": cleanup})) from None
            raise
        if isinstance(
            error,
            (OSError, TimeoutError, ValueError, subprocess.SubprocessError),
        ):
            # Installed prerequisites do not guarantee a successful launch: host
            # policy may deny it, or the helper may fail before its ready status.
            # None of those races authorises a direct subprocess or an untyped escape.
            raise IsolationUnavailable(IsolationRefusal(
                "isolation-launch-failed",
                "the declared namespace boundary could not be established",
                {"error": f"{type(error).__name__}: {error}",
                 **({"cleanup": cleanup} if cleanup is not None else {})},
            )) from None
        if cleanup is not None:
            error.add_note(f"Launch cleanup unconfirmed: {cleanup['error']}; workspace: {root}")
        raise
    finally:
        if startup is not None:
            startup.close()


def run(spec: IsolationSpec, *, stdin: bytes = b"") -> IsolationResult:
    """Run to completion with bounded concurrent output capture."""
    if type(stdin) is not bytes:
        raise TypeError("isolated stdin must be exact bytes")
    if spec.limits.protocol_bytes is not None and len(stdin) > spec.limits.protocol_bytes:
        return IsolationResult(
            IsolationStatus.REFUSED,
            spec.digest,
            "unavailable",
            refusal=IsolationRefusal(
                "isolation-protocol-input-too-large",
                "the input exceeds the policy's protocol budget",
                {"bytes": len(stdin), "limit": spec.limits.protocol_bytes},
            ),
        )
    try:
        process = start(spec, text=False)
    except IsolationUnavailable as error:
        return IsolationResult(
            IsolationStatus.REFUSED,
            spec.digest,
            "unavailable",
            refusal=error.refusal,
        )
    output: dict[str, bytes] = {"stdout": b"", "stderr": b""}
    errors: list[BaseException] = []

    def consume(name: str) -> None:
        try:
            output[name] = getattr(process, name).read()
        except (IsolationOutputLimit, OSError, ValueError) as error:
            errors.append(error)

    readers = [threading.Thread(target=consume, args=(name,), daemon=True) for name in ("stdout", "stderr")]
    for reader in readers:
        reader.start()
    cleanup_deadline = None
    try:
        try:
            process.stdin.write(stdin)
            process.stdin.close()
        except (BrokenPipeError, IsolationOutputLimit, OSError, ValueError):
            # A target may terminate before consuming the request.  Its supervised
            # exit status below classifies that outcome; a pipe race must not escape
            # as an untyped verifier exception.
            pass
        supervisor_refusal = None
        try:
            process.wait(timeout=_supervisor_wait_seconds(spec))
        except subprocess.TimeoutExpired:
            cleanup_deadline = time.monotonic() + defaults.SERVICE_CLEANUP_SECONDS
            supervisor_refusal = process.status_refusal or IsolationRefusal(
                "isolation-supervisor-timeout",
                "the namespace supervisor did not finish within its bounded margin",
            )
            try:
                process.kill(deadline=cleanup_deadline)
            except (IsolationCleanupError, OSError, subprocess.TimeoutExpired) as cleanup:
                supervisor_refusal = replace(
                    supervisor_refusal,
                    detail={**supervisor_refusal.detail,
                            "stop_error": f"{type(cleanup).__name__}: {cleanup}"})
            else:
                # The status reader may finish while the owner stops/reaps the
                # launcher. Keep that terminal receipt instead of the earlier
                # wait timeout. A missing/malformed receipt is still a refusal;
                # successful cleanup alone does not establish execution status.
                if process.status_refusal is not None:
                    supervisor_refusal = process.status_refusal
                elif process.final_status is not None:
                    supervisor_refusal = None
        if cleanup_deadline is None:
            cleanup_deadline = time.monotonic() + defaults.SERVICE_CLEANUP_SECONDS
        for reader in readers:
            reader.join(max(0.0, cleanup_deadline - time.monotonic()))
        incomplete_streams = [name for name, reader in zip(output, readers) if reader.is_alive()]
        capture_errors = [type(error).__name__ for error in errors
                          if type(error) is not IsolationOutputLimit]
        capture = {"errors": capture_errors, "incomplete_streams": incomplete_streams}
        supervisor_refusal = supervisor_refusal or process.status_refusal
        if supervisor_refusal is not None:
            if capture_errors or incomplete_streams:
                supervisor_refusal = replace(
                    supervisor_refusal,
                    detail={**supervisor_refusal.detail, "capture": capture})
            result = IsolationResult(
                IsolationStatus.REFUSED,
                spec.digest,
                process.capabilities.identity,
                stdout=output["stdout"],
                stderr=output["stderr"],
                refusal=supervisor_refusal,
            )
        elif capture_errors or incomplete_streams:
            code = process.poll()
            result = IsolationResult(
                IsolationStatus.REFUSED,
                spec.digest,
                process.capabilities.identity,
                stdout=output["stdout"],
                stderr=output["stderr"],
                refusal=IsolationRefusal(
                    "isolation-output-capture-failed",
                    "the isolated process output could not be captured exactly",
                    {"capture": capture, "execution_status": process.final_status,
                     "exit_code": code if code is not None and code >= 0 else None,
                     "signal": -code if code is not None and code < 0 else None},
                ),
            )
        else:
            code = process.poll()
            status_name = ("output-limit" if errors else process.final_status or "signaled")
            result = IsolationResult(
                IsolationStatus(status_name),
                spec.digest,
                process.capabilities.identity,
                stdout=output["stdout"],
                stderr=output["stderr"],
                exit_code=code if code is not None and code >= 0 else None,
                signal=-code if code is not None and code < 0 else None,
            )
    except BaseException as error:
        try:
            process.close(deadline=cleanup_deadline)
        except BaseException as cleanup:  # noqa: BLE001 -- cleanup cannot replace the primary failure
            if isinstance(error, PreparationCancelled):
                error.cleanup_confirmed = False
            error.add_note("Isolation cleanup unconfirmed: "
                           + "".join(traceback.format_exception_only(cleanup)).rstrip())
        else:
            if isinstance(error, PreparationCancelled):
                error.cleanup_confirmed = True
        raise
    try:
        process.close(deadline=cleanup_deadline)
    except (IsolationCleanupError, OSError, subprocess.TimeoutExpired) as cleanup:
        detail = {"cleanup": {"state": "unconfirmed", "why":
                              "".join(traceback.format_exception_only(cleanup)).rstrip()}}
        if result.refusal is not None:
            refusal = replace(result.refusal, detail={**result.refusal.detail, **detail})
        else:
            refusal = IsolationRefusal(
                "isolation-cleanup-unconfirmed", "the namespace owner was not reaped",
                {**detail, "execution_status": result.status.value,
                 "exit_code": result.exit_code, "signal": result.signal})
        # Complete output remains evidence, but cannot certify completed cleanup.
        result = replace(result, status=IsolationStatus.REFUSED, refusal=refusal)
    return result


__all__ = ["IsolationCleanupError", "IsolationOutputLimit", "SandboxProcess", "capabilities", "run", "start"]
