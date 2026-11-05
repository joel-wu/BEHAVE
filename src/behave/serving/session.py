"""A single namespace-owned model-server incarnation with runtime budgets.

Readiness is not model acceptance. The caller runs its own provider/tool smoke
and closes this session in finally. Restart means a fresh session after confirmed
cleanup, with the same ServingSpec; request history stays with the agent journal.
There is no PID-file adoption, implicit restart or change of model policy.
"""

from __future__ import annotations

import errno
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.core import runtime_defaults as defaults
from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json, parse_json
from behave.isolation.service import ServiceResult, run_service

from .lifecycle import ServingFailure, quiescent_call
from .spec import ServingSpec


def check_port(port: int) -> None:
    """Reject a live listener, but allow normal TCP TIME_WAIT after shutdown.

    This short reservation is lifecycle hygiene, not an atomic server handoff
    or endpoint attestation. The server still has to bind and pass readiness.
    """
    try:
        with socket.socket() as reserved:
            reserved.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            reserved.bind(("127.0.0.1", port))
            reserved.listen(1)
    except OSError as error:
        code = "port-in-use" if error.errno == errno.EADDRINUSE else "port-reservation-failed"
        raise ServingFailure(code) from error


def probe(spec: ServingSpec, *, scratch: Path, deadline: float | None, cancelled=None) -> bytes | None:
    # During weight loading there is no listener. Avoid repeatedly booting a
    # Python/PID namespace just to discover ECONNREFUSED, especially when many
    # engines cold-start together. An open port is NOT readiness: the existing
    # bounded HTTP/model-identity check still runs below.
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        return None
    try:
        with socket.create_connection(("127.0.0.1", spec.port), timeout=remaining):
            pass
    except (ConnectionRefusedError, TimeoutError):
        return None
    # Socket timeout alone does not bound a trickling HTTP peer. A short-lived
    # trusted service owns the *whole* GET including headers and body drain.
    script = ("import sys; sys.path.insert(0,sys.argv.pop(1)); "
              "from behave.serving.probe import main; main()")
    result = quiescent_call("readiness", lambda: run_service(
        (sys.executable, "-I", "-B", "-c", script,
         str(Path(__file__).resolve().parents[2]), str(spec.port)), cwd=scratch,
        environment={"PATH": "/usr/bin:/bin"}, network="host", deadline=deadline,
        output_bytes=None, cancelled=cancelled, cleanup_seconds=spec.cleanup_seconds))
    return result.stdout if result.status == "exited" and result.returncode == 0 else None


def matches(raw: bytes, model: str) -> bool:
    row = parse_json(raw)
    entries = row.get("data")
    return (row.get("object") == "list" and type(entries) is list and len(entries) == 1
            and type(entries[0]) is dict and entries[0].get("id") == model)


class ServingSession:
    def __init__(self, spec: ServingSpec, *, scratch: Path, environment: dict[str, str], records: Path):
        self.spec, self.scratch = spec, scratch
        self.environment = dict(environment)
        self.records = records
        self.incarnation = uuid.uuid4().hex
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._result: ServiceResult | None = None
        self._closed = False
        self._started = False
        self._ready = False
        self._failure: str | None = None
        self._probe_cleanup = CleanupState.COMPLETE

    def _record(self, name: str, value: dict):
        with (self.records / name).open("xb") as stream:
            stream.write(canonical_json(value) + b"\n")

    def _serve(self, deadline: float | None):
        try:
            self._result = run_service(self.spec.command(), cwd=self.scratch, environment=self.environment,
                                       deadline=deadline, output_bytes=self.spec.log_bytes,
                                       network="host", cancelled=self._cancel.is_set,
                                       cleanup_seconds=self.spec.cleanup_seconds)
        except BaseException:  # noqa: BLE001 -- thread boundary cannot claim successful cleanup on supervisor failure
            # Do not invent a clean exit when supervision itself fails.
            self._failure = self._failure or "supervisor-error"
        finally:
            self._done.set()

    def start(self, *, cancelled=None) -> LocalChatEndpoint:
        if self._started or self._closed:
            raise ServingFailure("session-already-used")
        self._started = True
        self.records.mkdir(parents=True, exist_ok=False)
        self._record("launch.json", {"schema": "behave.serving-launch/1", "incarnation": self.incarnation,
                                    "spec": self.spec.as_dict(), "identity": self.spec.identity,
                                    "argv": self.spec.command()})
        began = time.monotonic()

        def interrupted():
            if cancelled is not None and cancelled():
                self._cancel.set()
            return self._cancel.is_set()

        try:
            if interrupted():
                raise ServingFailure("startup-cancelled")
            # Fail on a pre-existing listener, never attach to somebody else's
            # endpoint. This is local lifecycle hygiene, not server attestation.
            check_port(self.spec.port)
            deadline = None if self.spec.lifetime_seconds is None else began + self.spec.lifetime_seconds
            self._thread = threading.Thread(target=self._serve, args=(deadline,), daemon=True)
            self._thread.start()
            startup = None if self.spec.startup_seconds is None else began + self.spec.startup_seconds
            while startup is None or time.monotonic() < startup:
                if interrupted():
                    raise ServingFailure("startup-cancelled")
                self.check_alive()
                # One startup deadline covers the server and its readiness
                # check. There is no separate Python/bootstrap timeout.
                raw = probe(self.spec, scratch=self.scratch, deadline=startup, cancelled=interrupted)
                if interrupted():
                    raise ServingFailure("startup-cancelled")
                if raw is not None:
                    if not matches(raw, self.spec.served_model):
                        raise ServingFailure("readiness-model-mismatch")
                    self.check_alive()
                    self._record("ready.json", {"schema": "behave.serving-ready/1", "incarnation": self.incarnation,
                                               "elapsed_seconds": time.monotonic() - began,
                                               "models": parse_json(raw)})
                    self._ready = True
                    return self.endpoint()
                interval = defaults.SERVING_POLL_SECONDS
                self._done.wait(interval if startup is None else min(interval, max(0, startup - time.monotonic())))
            raise ServingFailure("startup-timeout")
        except BaseException as error:
            self._failure = error.code if isinstance(error, ServingFailure) else type(error).__name__
            if isinstance(error, ServingFailure):
                self._probe_cleanup = CleanupState.combine(self._probe_cleanup, error.cleanup_state)
            try:
                self.close()
            except BaseException as cleanup_error:  # noqa: BLE001 -- preserve the active startup failure
                state = (cleanup_error.cleanup_state if isinstance(cleanup_error, ServingFailure)
                         else CleanupState.UNKNOWN)
                if isinstance(error, ServingFailure):
                    error.cleanup_state = CleanupState.combine(error.cleanup_state, state)
                # Keep private exception payloads out of the serving API; the
                # original failure remains primary and cleanup stays typed.
                cause = cleanup_error.code if isinstance(cleanup_error, ServingFailure) else type(cleanup_error).__name__
                error.add_note(f"Serving cleanup unconfirmed: {cause}")
            raise

    def endpoint(self) -> LocalChatEndpoint:
        if not self._ready or self._closed:
            raise ServingFailure("endpoint-not-ready")
        self.check_alive()
        return LocalChatEndpoint(f"http://127.0.0.1:{self.spec.port}/v1/chat/completions",
                                 self.spec.served_model, self.spec.identity)

    def check_alive(self):
        if self._thread is None or self._closed:
            raise ServingFailure("server-not-running")
        if self._done.is_set():
            if self._result is None:
                raise ServingFailure("server-supervisor-error", cleanup_state=CleanupState.UNKNOWN)
            raise ServingFailure("server-" + self._result.status, cleanup_state=self._result.cleanup_state)

    def close(self) -> dict:
        if self._closed:
            row = parse_json((self.records / "stopped.json").read_bytes())
            if not row["cleanup_complete"]:
                raise ServingFailure("cleanup-incomplete", cleanup_state=CleanupState(row["cleanup_state"]))
            return row
        if not self._started:
            raise ServingFailure("session-not-started")
        self._cancel.set()
        if self._thread is not None and self._thread.ident is not None:
            self._thread.join(timeout=None if self.spec.cleanup_seconds is None else
                              self.spec.cleanup_seconds + defaults.SERVING_JOIN_GRACE_SECONDS)
        result = self._result
        if self._thread is None or self._thread.ident is None:
            server_cleanup = CleanupState.COMPLETE
        else:
            server_cleanup = result.cleanup_state if self._done.is_set() and result is not None else CleanupState.UNKNOWN
        cleanup = CleanupState.combine(server_cleanup, self._probe_cleanup)
        row = {"schema": "behave.serving-stopped/3", "incarnation": self.incarnation,
               "ready": self._ready, "failure": self._failure, "cleanup_complete": cleanup.quiescent,
               "cleanup_state": cleanup.value,
               "service_status": None if result is None else result.status,
               "execution_status": None if result is None else result.execution_status,
               "cleanup_elapsed_seconds": None if result is None else result.cleanup_elapsed_seconds,
               "dropped_output_bytes": None if result is None else result.dropped_output_bytes,
               "returncode": None if result is None else result.returncode}
        if result is not None:
            for name in ("stdout", "stderr"):
                with (self.records / (name + ".log")).open("xb") as stream:
                    stream.write(getattr(result, name))
        self._record("stopped.json", row)
        self._closed = True
        if not cleanup.quiescent:
            raise ServingFailure("cleanup-incomplete", cleanup_state=cleanup)
        return row
