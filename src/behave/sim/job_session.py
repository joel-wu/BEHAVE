"""Bounded, ordered JSON sessions inside the named-job isolation boundary.

Only transport is shared. Each engine owns its fixed context and operation schema;
malformed messages, timeouts and worker failures terminate the session, never retry it.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
import traceback

from behave.core import runtime_defaults as defaults
from behave.isolation import IsolationUnavailable, start
from behave.isolation.preparation_control import PreparationCancelled

from .core.refusal import Refusal, Refused
from .cpu_admission import cpu_cleanup_unconfirmed, cpu_work_width, reserve_cpus
from .job_isolation import IsolatedJobFailure, _freeze_inputs, _job_spec, _strict_json
from .execution_control import check_execution, remaining_timeout

WIRE_BYTES = defaults.SIM_JOB_WIRE_BYTES


class JobSession:
    """Parent-side resource owner; no source execution or solver imports."""

    def __init__(self, job, context, *, module, version, timeout_s, wall_s,
                 exchanges, timings=None, cpus=1):
        self._child = None
        self._closed = False
        self._cleanup_error: BaseException | None = None
        self._serial = 0
        self._version = version
        self._stderr_tail = bytearray()
        self._stderr_bytes = 0
        self._stderr_lock = threading.Lock()
        self._stderr_thread = None
        self.timings = [] if timings is None else timings
        for value in (timeout_s, wall_s):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("session deadlines must be finite and positive")
        if exchanges is not None and (type(exchanges) is not int or exchanges < 1):
            raise ValueError("a session needs a finite positive exchange budget")
        self._exchanges = exchanges
        with reserve_cpus(cpu_work_width(cpus)) as admitted:
            self._cpus = admitted
            frozen, mounts = _freeze_inputs(job, context)
            spec = _job_spec(job, frozen, mounts, timeout_s=remaining_timeout(wall_s),
                             wire_bytes=defaults.SIM_SEARCH_SESSION_WIRE_BYTES,
                             module=module, cpus=admitted)
            try:
                try:
                    self._child = start(spec)
                except IsolationUnavailable as error:
                    if error.refusal.detail.get("cleanup", {}).get("state") == "unconfirmed":
                        cpu_cleanup_unconfirmed()
                    raise
                except BaseException as error:
                    if not isinstance(error, PreparationCancelled) or not error.cleanup_confirmed:
                        cpu_cleanup_unconfirmed()
                    raise
                self._stderr_thread = threading.Thread(target=self._drain, args=(self._child,), daemon=True)
                self._stderr_thread.start()
                self.opened = self._exchange("open", frozen, timeout_s)
            except IsolationUnavailable as exc:
                failure = IsolatedJobFailure("refused", str(exc), code=exc.refusal.code)
                self.close(failure=failure)
                raise failure from None
            except BaseException as error:
                self.close(failure=error)
                raise

    def _drain(self, child):
        try:
            # A long-running worker can flush just one short stage message.
            # Buffered read(n) waits for n bytes or EOF; read1 retains it now,
            # without waiting for the next stage or changing the bounded tail.
            while chunk := child.stderr.read1(4096):
                with self._stderr_lock:
                    self._stderr_bytes += len(chunk)
                    self._stderr_tail.extend(chunk)
                    del self._stderr_tail[:-defaults.PYTHON_STDERR_BYTES]
        except (OSError, ValueError, RuntimeError):
            pass  # The namespace supervisor bounds output and owns cleanup.

    def _stderr_diagnostic(self):
        with self._stderr_lock:
            return {'bytes': self._stderr_bytes,
                    'truncated': self._stderr_bytes > len(self._stderr_tail),
                    'tail': self._stderr_tail.decode('utf-8', errors='replace')}

    def _exchange(self, op, payload, timeout_s):
        # A prepared synchronous solver is idle between requests. Keep its
        # formula, not a CPU permit: seed replay must be able to take that CPU
        # before the next Select/Probe. Acquire before writing any request byte.
        try:
            with reserve_cpus(self._cpus):
                return self._exchange_admitted(op, payload, timeout_s)
        except BaseException as error:
            self.close(failure=error)
            raise

    def _exchange_admitted(self, op, payload, timeout_s):
        check_execution()
        if self._closed or self._child is None:
            raise IsolatedJobFailure("no-answer", "the generation session is closed")
        started = time.monotonic()
        row = {"op": op, "status": "failed"}
        self.timings.append(row)
        child, box = self._child, []
        try:
            if self._exchanges is not None and self._serial >= self._exchanges:
                raise IsolatedJobFailure("refused", "session exchange budget is exhausted")
            wire = json.dumps({"version": self._version, "id": self._serial,
                               "op": op, "payload": payload}, allow_nan=False,
                              separators=(",", ":")).encode() + b"\n"
            if WIRE_BYTES is not None and len(wire) > WIRE_BYTES:
                raise IsolatedJobFailure("refused", "session request exceeds wire budget")

            def communicate():
                try:
                    child.stdin.write(wire)
                    child.stdin.flush()
                    box.append(child.stdout.readline(-1 if WIRE_BYTES is None else WIRE_BYTES + 1))
                except Exception as exc:  # noqa: BLE001 - transport thread
                    box.append(exc)

            thread = threading.Thread(target=communicate, daemon=True)
            check_execution()
            thread.start()
            until = time.monotonic() + timeout_s
            while thread.is_alive():
                check_execution()
                remaining = until - time.monotonic()
                if remaining <= 0:
                    break
                # Clamp the wait, not an earlier clock reading plus a later
                # remaining lifetime. That combination can expire before the
                # shared deadline and misreport cancellation as worker failure.
                thread.join(min(remaining_timeout(remaining),
                                defaults.ISOLATION_COMPLETION_POLL_SECONDS))
            check_execution()
            if thread.is_alive():
                raise IsolatedJobFailure("timeout", f"session {op} exceeded {timeout_s}s")
            if not box or type(box[0]) is not bytes:
                raise IsolatedJobFailure("crashed", "generation session transport stopped")
            line = box[0]
            if WIRE_BYTES is not None and len(line) > WIRE_BYTES:
                raise IsolatedJobFailure("no-answer", "invalid session response frame")
            if not line.endswith(b"\n"):
                # EOF can be the supervisor reporting a real worker/resource
                # failure. Preserve that cause before cleanup requests a kill;
                # waiting shares this exchange's deadline, not a new allowance.
                detail = "invalid session response frame: worker closed its response stream"
                try:
                    code = child.wait(timeout=remaining_timeout(
                        max(0.0, timeout_s - (time.monotonic() - started))))
                except subprocess.TimeoutExpired:
                    detail += "; worker termination was not confirmed within the exchange deadline"
                else:
                    terminal = {"status": child.final_status, "returncode": code}
                    if self._stderr_thread is not None:
                        self._stderr_thread.join(timeout=max(
                            0.0, timeout_s - (time.monotonic() - started)))
                    diagnostic = self._stderr_diagnostic()
                    if diagnostic['bytes']:
                        terminal['stderr'] = diagnostic
                    refusal = child.status_refusal
                    if refusal is not None:
                        terminal["refusal"] = refusal.as_dict()
                    row["worker_exit"] = terminal
                    detail += "; " + json.dumps(terminal, sort_keys=True)
                check_execution()
                raise IsolatedJobFailure("no-answer", detail)
            got = _strict_json(line)
            common = {"version", "id", "ok", "wall_ms"}
            if got.get("version") != self._version or type(got.get("id")) is not int \
                    or got["id"] != self._serial or type(got.get("ok")) is not bool \
                    or type(got.get("wall_ms")) not in (int, float) \
                    or not math.isfinite(got["wall_ms"]) or got["wall_ms"] < 0:
                raise IsolatedJobFailure("no-answer", "invalid session response identity")
            if got["ok"] and set(got) == common | {"result"}:
                self._serial += 1
                row.update(status="ok", worker_ms=got["wall_ms"])
                return got["result"]
            if not got["ok"] and set(got) == common | {"refusal"}:
                refusal = got["refusal"]
                if type(refusal) is dict and set(refusal) in (
                        {"kind", "why"}, {"kind", "why", "detail"}) \
                        and type(refusal.get("detail", {})) is dict \
                        and all(type(refusal[k]) is str for k in ("kind", "why")):
                    raise Refused(Refusal(refusal["kind"], refusal["why"],
                                          **refusal.get("detail", {})))
            raise IsolatedJobFailure("no-answer", str(got.get("error", "invalid reply")))
        except BaseException as error:
            # Reap before leaving reserve_cpus: a failed cleanup must quarantine
            # the held permit, not make it available to another rollout.
            self.close(failure=error)
            raise
        finally:
            row["wall_ms"] = (time.monotonic() - started) * 1000
            try:
                diagnostic = self._stderr_diagnostic()
                if diagnostic['bytes']:
                    row['stderr'] = diagnostic
            except Exception:
                pass  # Diagnostics cannot replace the operation's result.

    def close(self, *, failure: BaseException | None = None):
        if failure is None:
            failure = sys.exception()
        # A fatal exchange can pass through several nested exception handlers.
        # Stop once; keep an unconfirmed handle and its error, not a live session
        # or a new cleanup allowance at each boundary.
        if not self._closed:
            self._closed = True
            try:
                if self._child is not None:
                    self._child.close()
            except BaseException as cleanup:  # noqa: BLE001 -- preserve the primary failure/cancellation
                self._cleanup_error = cleanup
                cpu_cleanup_unconfirmed()
            else:
                self._child = None
        cleanup = self._cleanup_error
        if cleanup is not None and cleanup is not failure:
            if failure is None:
                raise cleanup
            note = ("Generation session cleanup unconfirmed: "
                    + "".join(traceback.format_exception_only(cleanup)).rstrip())
            if note not in getattr(failure, "__notes__", ()):
                failure.add_note(note)


def serve(reader, writer, *, version, dispatch):
    """A fatal response is the last response; IDs cannot be skipped or replayed."""
    expected = 0
    while True:
        line = reader.readline(-1 if WIRE_BYTES is None else WIRE_BYTES + 1)
        if not line:
            return
        started = time.monotonic()
        reply = {"version": version, "id": expected}
        try:
            if (WIRE_BYTES is not None and len(line) > WIRE_BYTES) or not line.endswith(b"\n"):
                raise ValueError("session request exceeds its framing budget")
            request = _strict_json(line)
            if set(request) != {"version", "id", "op", "payload"} \
                    or request["version"] != version \
                    or type(request["id"]) is not int or request["id"] != expected:
                raise ValueError("session request has an invalid version or sequence")
            if (request["op"] == "open") != (expected == 0):
                raise ValueError("session opens once and cannot rebind its context")
            reply.update(ok=True, result=dispatch(request["op"], request["payload"]))
        except Refused as exc:
            reply.update(ok=False, refusal=exc.refusal.as_dict())
        except Exception as exc:  # noqa: BLE001 - isolated protocol boundary
            reply.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        reply["wall_ms"] = (time.monotonic() - started) * 1000
        document = json.dumps(reply, allow_nan=False, separators=(",", ":")).encode()
        if WIRE_BYTES is not None and len(document) + 1 > WIRE_BYTES:
            return
        writer.write(document + b"\n")
        writer.flush()
        if not reply["ok"]:
            return
        expected += 1


def stdio(serve_engine):
    if sys.argv[1:] != ["--stdio"]:
        raise SystemExit("generation sessions use --stdio")
    from .job_context import _named_job_scope

    # Like the one-shot worker dispatcher, this entry point is launched by
    # _job_spec inside the named-job boundary. Nested source interpreters must
    # inherit that boundary rather than attempt a second namespace.
    # Source/library output cannot masquerade as protocol responses.
    with os.fdopen(os.dup(1), "wb") as protocol:
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), 1)
        with _named_job_scope():
            serve_engine(sys.stdin.buffer, protocol)
