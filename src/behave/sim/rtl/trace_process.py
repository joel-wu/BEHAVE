"""Fixed-module workers for the CPU-only decode/monitor stage.

Unlike multiprocessing.spawn, these workers never import the caller's __main__.
The private pipe connects two trusted Sim processes; its pickle frames are not
an artifact input format. Candidate JSON remains bytes inside a check request.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path
from queue import Empty, LifoQueue

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root

from ..cpu_admission import check_cpu_work, cpu_cleanup_unconfirmed
from ..execution_control import check_execution
from .trace_transport import TraceChannel

READY = "sim-trace-worker/3"


def _check_control():
    check_execution()
    check_cpu_work()


def _cleanup(actions):
    """Attempt all owned actions without replacing the first failure."""
    failures = []
    for action in actions:
        try:
            action()
        except BaseException as failure:  # noqa: BLE001 - finish cleanup even during cancellation
            failures.append(failure)
    if failures:
        first, *others = failures
        for other in others:
            first.add_note("Additional trace worker cleanup failure: "
                           + "".join(traceback.format_exception_only(other)).rstrip())
        raise first


class _Worker:
    def __init__(self, plan, monitor):
        self.process = None
        parent, child = socket.socketpair()
        self.connection = TraceChannel(parent, _check_control)
        self.errors = tempfile.TemporaryFile(dir=scratch_root())  # noqa: SIM115 - owned until worker.close()
        env = dict(os.environ, BEHAVE_JOB_CPU_LIMIT="1")
        root = str(Path(__file__).resolve().parents[3])
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (root, env.get("PYTHONPATH"))))
        try:
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-m", "behave.sim.rtl._trace_worker", str(child.fileno())],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self.errors,
                    pass_fds=(child.fileno(),), env=env)
            except OSError:  # Popen reports an unsuccessful exec after reaping it.
                raise
            except BaseException:
                cpu_cleanup_unconfirmed()  # no returned process handle
                raise
            child.close()
            self.connection.send((replace(plan, completion_targets=None, checkpoint_plans=None), monitor))
            if self._receive() != READY:
                raise RuntimeError("trace worker did not acknowledge its plan")
        except BaseException as failure:
            try:
                _cleanup((child.close, self.close))
            except BaseException as cleanup:  # noqa: BLE001 - preserve the active failure or cancellation
                failure.add_note("Trace worker cleanup unconfirmed: "
                                 + "".join(traceback.format_exception_only(cleanup)).rstrip())
            raise

    def check(self, stimulus, payload, execution, replay_seconds, completion=None):
        check_execution()
        check_cpu_work()
        self.connection.send((stimulus, payload, execution, replay_seconds, completion))
        ok, result = self._receive()
        if not ok:
            raise RuntimeError(f"trusted trace worker failed: {result}")
        return result

    def _receive(self):
        return self.connection.recv()

    def stop(self):
        # The owner has joined all requests; replies are already in its hands.
        # There is no worker-side artifact to flush or graceful shutdown to await.
        # Popen.kill already handles an exited/reaped child; a separate poll
        # adds another failure point without making signal delivery safer.
        actions = [self.connection.close]
        if self.process is not None:
            actions.append(self.process.kill)
        _cleanup(actions)

    def reap(self, deadline):
        actions = []
        if self.process is not None:
            actions.append(lambda: self.process.wait(timeout=max(0.0, deadline - time.monotonic())))
        actions.append(self.errors.close)
        try:
            _cleanup(actions)
        except BaseException:
            cpu_cleanup_unconfirmed()
            raise

    def close(self):
        _close_workers((self,))


def _close_workers(workers):
    # The owner has joined all callers. Stop the entire wave before any waits,
    # including on cancellation; no lane receives a new cleanup allowance.
    deadline = time.monotonic() + defaults.SIM_JOB_CLEANUP_SECONDS
    actions = [worker.stop for worker in workers]
    actions.extend(lambda worker=worker: worker.reap(deadline) for worker in workers)
    try:
        _cleanup(actions)
    except BaseException:
        cpu_cleanup_unconfirmed()
        raise


class TraceWorkerPool:
    """Lazy fixed-width lanes, not another queue of submitted run jobs."""

    def __init__(self, plan, monitor, workers):
        self.plan, self.monitor = plan, monitor
        self._available = LifoQueue(workers)
        self._workers = []
        self._lock = threading.Lock()
        for _ in range(workers):
            self._available.put(None)

    def check(self, stimulus, payload, execution, replay_seconds, completion=None):
        worker = self._available.get()
        try:
            if isinstance(worker, BaseException):
                # Other already-running replays can reach this lane after its
                # first interruption. Retain the actual terminal cause: budget
                # cancellation must not become an invented worker failure.
                raise worker.with_traceback(None)
            if worker is None:
                try:
                    worker = _Worker(self.plan, self.monitor)
                except BaseException as failure:
                    worker = failure
                    raise
                with self._lock:
                    self._workers.append(worker)
            try:
                return worker.check(stimulus, payload, execution, replay_seconds, completion)
            except BaseException as failure:
                # Interrupted frames cannot be resumed as another request. The
                # worker stays owned for cleanup, but its lane cannot be reused.
                worker = failure
                raise
        finally:
            self._available.put(worker)
            worker = None  # The queued exception must not also own itself via this frame.

    def close(self):
        _close_workers(self._workers)
        # Callers are joined before close. Failed lanes retain their exception
        # while open so queued requests see the original cause; after shutdown
        # those tracebacks would form a pool -> exception -> pool cycle keeping
        # the interrupted payload and the surrounding replay batch alive.
        while True:
            try:
                self._available.get_nowait()
            except Empty:
                break
