"""Lifetime supervision for a *trusted* service, not a candidate sandbox.

The service retains host filesystem access. Untrusted code must still use
``IsolationSpec``. A PID namespace owns all descendants, including new sessions
and nested namespaces; terminating its init kills them as well. No host-process
fallback is allowed. An optional monotonic deadline covers launch, execution
and pipe drain. Whole-attempt workers use no cumulative deadline. Cancellation
and descendant cleanup apply in either case, with a separate cleanup grace.
"""

from __future__ import annotations

import math
import os
import selectors
import signal
import socket
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile

from behave.core.lifecycle import CleanupState
from behave.core.paths import scratch_root
from behave.core.runtime_defaults import ISOLATION_COMPLETION_POLL_SECONDS, LOG_RETAIN_BYTES, SERVICE_CLEANUP_SECONDS

from .startup import StartupCancelled, acquire_startup, startup_slots

_EXECUTION_STATUSES = frozenset({"exited", "timeout", "cancelled", "output-limit", "launch-failed"})


def service_execution_status(status: str, recorded: str | None = None) -> str | None:
    """Validate the original outcome; legacy cleanup-only receipts stay unknown."""
    if recorded is None and status in _EXECUTION_STATUSES:
        return status
    if recorded is not None and (
        type(recorded) is not str or recorded not in _EXECUTION_STATUSES
        or status not in {recorded, "cleanup-incomplete"}
    ):
        raise ValueError("service execution status disagrees with its overall outcome")
    return recorded


@dataclass(frozen=True, slots=True)
class ServiceResult:
    # Overall disposition remains fail-closed for existing callers. The original
    # execution outcome is independent; a cleanup failure cannot erase it.
    status: str
    returncode: int | None
    stdout: bytes
    stderr: bytes
    elapsed_seconds: float
    cleanup_elapsed_seconds: float = 0.
    dropped_output_bytes: int = 0
    execution_status: str | None = None
    cleanup_error: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "execution_status", service_execution_status(self.status, self.execution_status))
        if self.cleanup_error is not None and (
            type(self.cleanup_error) is not str or self.status != "cleanup-incomplete"
        ):
            raise ValueError("service cleanup error requires an incomplete-cleanup receipt")

    @property
    def cleanup_state(self) -> CleanupState:
        # This supervisor reports these outcomes only after its cleanup checks.
        if self.status == "cleanup-incomplete":
            return CleanupState.INCOMPLETE
        if self.status in {"exited", "timeout", "cancelled", "output-limit", "launch-failed"}:
            return CleanupState.COMPLETE
        return CleanupState.UNKNOWN


def _receive_init_handle(owner: socket.socket) -> int | None:
    message, descriptors, flags, _ = socket.recv_fds(owner, 16, 1, socket.MSG_CMSG_CLOEXEC)
    if message != b"init" or len(descriptors) != 1 or flags & socket.MSG_CTRUNC:
        for descriptor in descriptors:
            os.close(descriptor)
        return None
    descriptor = descriptors[0]
    os.set_inheritable(descriptor, False)
    return descriptor


def run_service(
    argv: tuple[str, ...], *, cwd: Path, environment: Mapping[str, str],
    deadline: float | None, output_bytes: int | None = LOG_RETAIN_BYTES,
    input_data: bytes = b"", network: str = "none",
    cancelled: Callable[[], bool] | None = None,
    cleanup_seconds: int | None = SERVICE_CLEANUP_SECONDS,
    process_limit: int | None = None,
) -> ServiceResult:
    """Run an explicitly configured trusted command; never inherit credentials.

    ``output_bytes`` bounds retained stdout/stderr combined. Excess log bytes
    are drained and counted without terminating the service. Structured stdout responses use
    ``None`` to retain their complete records. Time and cancellation still apply.
    This function does not install signal handlers or change caller alarms.
    Host networking is an explicit capability for trusted broker services only;
    candidate sandboxes still use IsolationSpec. Cancellation is polled while
    draining output, including after the command leader exits.
    """
    if not argv or not Path(argv[0]).is_absolute():
        raise ValueError("service requires an absolute executable")
    if output_bytes is not None and (type(output_bytes) is not int or output_bytes < 1):
        raise ValueError("service output limit must be positive or None")
    if deadline is not None and not math.isfinite(deadline):
        raise ValueError("service deadline must be finite or None")
    # The caller already owns these bytes. This layer does not impose another
    # size quota on structured model/trainer transport.
    if type(input_data) is not bytes:
        raise TypeError("service input must be bytes")
    if network not in {"none", "host"}:
        raise ValueError("service network must be none or host")
    if cancelled is not None and not callable(cancelled):
        raise TypeError("service cancellation requires a predicate")
    if cleanup_seconds is not None and (type(cleanup_seconds) is not int or cleanup_seconds < 1):
        raise ValueError("service cleanup grace must be a positive integer or None")
    if process_limit is not None and (type(process_limit) is not int or process_limit == 0 or process_limit < -1):
        raise ValueError("service process limit must be positive, -1 (unlimited), or None (inherit)")
    began = time.monotonic()
    if cancelled is not None and cancelled():
        return ServiceResult("cancelled", None, b"", b"", 0.)
    if deadline is not None and deadline <= began:
        return ServiceResult("timeout", None, b"", b"", 0.)
    owner = child = process = startup = None
    try:
        startup = acquire_startup(deadline=deadline, cancelled=cancelled)
        # A regular input avoids blocking pipe writes before supervision.
        with TemporaryFile(dir=scratch_root()) as stdin:
            stdin.write(input_data)
            stdin.seek(0)
            if cancelled is not None and cancelled():
                return ServiceResult("cancelled", None, b"", b"", time.monotonic() - began)
            if deadline is not None and time.monotonic() >= deadline:
                return ServiceResult("timeout", None, b"", b"", time.monotonic() - began)
            owner, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            # The pidfd continues to own PID 1 after the bootstrap exec. Keep
            # the application below the shared reaper: long-lived judge workers
            # do not themselves wait for orphans from nested namespaces.
            command = (
                "/usr/bin/unshare", "--user", "--map-root-user", "--mount",
                "--pid", "--fork", "--mount-proc", "--kill-child=KILL",
                *(("--net",) if network == "none" else ()), "--",
                sys.executable, "-I", "-B", str(Path(__file__).with_name("service_init.py")),
                str(child.fileno()), sys.executable, "-I", "-B",
                str(Path(__file__).with_name("command_init.py")), *argv,
            )
            # Linux snapshots the creating process's NPROC limit into the new
            # user namespace. Raising it inside that namespace is too late.
            # Apply an explicit trusted-service limit in this child only;
            # leave the caller, hard limit, and default service policy intact.
            if process_limit is not None:
                command = ("/usr/bin/prlimit", f"--nproc={process_limit}:", "--", *command)
            environment = dict(environment)
            slots = startup_slots()
            environment["BEHAVE_ISOLATION_STARTUP_SLOTS"] = "none" if slots is None else str(slots)
            if startup.descriptor is not None:
                environment["BEHAVE_ISOLATION_STARTUP_FD"] = str(startup.descriptor)
                environment["BEHAVE_ISOLATION_STARTUP_DIR"] = str(startup.directory)
            process = subprocess.Popen(
                command, cwd=cwd, env=environment, stdin=stdin,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
                pass_fds=(child.fileno(), *startup.pass_fds),
            )
    except (StartupCancelled, TimeoutError) as error:
        if owner is not None:
            owner.close()
        return ServiceResult("cancelled" if isinstance(error, StartupCancelled) else "timeout",
                             None, b"", b"", time.monotonic() - began)
    except OSError as error:
        if owner is not None:
            owner.close()
        diagnostic = f"{type(error).__name__}: {error}".encode("utf-8", errors="backslashreplace")
        return ServiceResult("launch-failed", None, b"", diagnostic, time.monotonic() - began)
    finally:
        if child is not None:
            child.close()
        if process is None and startup is not None:
            startup.close()
    buffers = [bytearray(), bytearray()]
    status = "exited"
    total = 0
    dropped = 0
    init_fd = None
    failure = None
    try:
        with selectors.DefaultSelector() as poller:
            owner.setblocking(False)
            poller.register(owner, selectors.EVENT_READ, "init")
            for index, stream in enumerate((process.stdout, process.stderr)):
                os.set_blocking(stream.fileno(), False)
                poller.register(stream, selectors.EVENT_READ, index)
            while poller.get_map() or process.poll() is None:
                if cancelled is not None and cancelled():
                    status = "cancelled"
                    break
                remaining = math.inf if deadline is None else deadline - time.monotonic()
                if remaining <= 0:
                    status = "timeout"
                    break
                for key, _ in poller.select(min(remaining, ISOLATION_COMPLETION_POLL_SECONDS)):
                    if key.data == "init":
                        init_fd = _receive_init_handle(owner)
                        poller.unregister(owner)
                        if init_fd is None:
                            status = "launch-failed"
                            break
                        # Readiness may arrive at the deadline (or alongside
                        # cancellation). Recheck before authorizing exec.
                        if cancelled is not None and cancelled():
                            status = "cancelled"
                            break
                        if deadline is not None and time.monotonic() >= deadline:
                            status = "timeout"
                            break
                        owner.send(b"1")
                        owner.close()
                        startup.ready()
                        continue
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        poller.unregister(key.fileobj)
                        continue
                    accepted = chunk if output_bytes is None else chunk[:output_bytes - total]
                    buffers[key.data].extend(accepted)
                    total += len(accepted)
                    dropped += len(chunk) - len(accepted)
                if status != "exited":
                    break
    except BaseException as error:
        failure = error
        raise
    finally:
        cleanup_began = time.monotonic()
        cleanup_error = None
        try:
            cleanup = _finish_service(process, owner, init_fd,
                                      None if cleanup_seconds is None else cleanup_began + cleanup_seconds)
        except BaseException as error:
            detail = "".join(traceback.format_exception_only(error)).rstrip()
            if failure is not None:
                failure.add_note("Service cleanup unconfirmed: " + detail)
            elif isinstance(error, (OSError, subprocess.SubprocessError)):
                # Timeout/cancellation/exit are returned outcomes, not active
                # Python exceptions. They need the same cause preservation.
                cleanup, cleanup_error = CleanupState.INCOMPLETE, detail
            else:
                raise
        finally:
            startup.close()
    return ServiceResult(status if cleanup.quiescent else "cleanup-incomplete",
                         process.returncode, bytes(buffers[0]), bytes(buffers[1]),
                         time.monotonic() - began, time.monotonic() - cleanup_began, dropped,
                         execution_status=status, cleanup_error=cleanup_error)


def _finish_service(process, owner, init_fd, cleanup_deadline) -> CleanupState:
    """Fence the actual namespace init, not just the local unshare transport."""
    cleanup = CleanupState.COMPLETE
    errors = []

    def remaining():
        return None if cleanup_deadline is None else max(0, cleanup_deadline - time.monotonic())

    def attempt(action):
        # A failed cleanup action must not skip reaping or closing the other
        # owned handles. Re-raise the first cause only after all attempts.
        try:
            return action()
        except BaseException as error:  # noqa: BLE001 - finish all owned cleanup, then re-raise
            errors.append(error)
            return None

    def receive_handle():
        if owner.fileno() >= 0:
            # A deadline can expire before the pidfd handoff. Never acknowledge
            # a late handoff: the service body must not start after timeout.
            with selectors.DefaultSelector() as handoff:
                handoff.register(owner, selectors.EVENT_READ)
                if handoff.select(remaining()):
                    return _receive_init_handle(owner)
        return None

    def stop():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def namespace_exited():
        with selectors.DefaultSelector() as exited:
            exited.register(init_fd, selectors.EVENT_READ)
            return bool(exited.select(remaining()))

    if init_fd is None:
        init_fd = attempt(receive_handle)
    forced = attempt(process.poll) is None
    if forced:
        attempt(stop)
    attempt(lambda: process.wait(timeout=remaining()))
    if init_fd is not None:
        if not attempt(namespace_exited):
            cleanup = CleanupState.INCOMPLETE
        attempt(lambda: os.close(init_fd))
    elif forced:
        # No acknowledged handoff means the service body could not run, but
        # a forced bootstrap exit still cannot claim confirmed cleanup.
        cleanup = CleanupState.INCOMPLETE
    for resource in (owner, process.stdout, process.stderr):
        attempt(resource.close)
    if errors:
        first, *others = errors
        for error in others:
            first.add_note("Additional service cleanup failure: "
                           + "".join(traceback.format_exception_only(error)).rstrip())
        raise first
    return cleanup
