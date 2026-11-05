"""Detached process ownership, heartbeats and status receipts.

Submission requires an explicit working directory. The copied supervisor uses
only the standard library, so later checkout edits cannot replace its code.
Legacy optional validation summaries remain in status receipts for compatibility.
"""
from __future__ import annotations

import errno
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread


def _now():
    return datetime.now(UTC).isoformat()


def _retry_io(operation, *args):
    """Reopen small control records on transient I/O errors, never retry work."""
    for attempt in range(8):
        try:
            return operation(*args)
        except OSError as error:
            if error.errno not in (errno.ESTALE, errno.EIO, errno.ETIMEDOUT, errno.EAGAIN, 521) or attempt == 7:
                raise
            time.sleep(min(.05 * 2**attempt, 1.0))


def _write(path, value):
    payload = json.dumps(value, indent=2) + '\n'
    temporary = path.with_suffix('.tmp')

    def publish():
        temporary.write_text(payload)
        temporary.replace(path)

    _retry_io(publish)


@contextmanager
def node_heartbeat(path, *, interval):
    """Publish node-process liveness independently of a login-side launcher.

    This is observation, not a task deadline or proof of training progress.
    The ordinary runtime/results remain the authority for completion.
    """
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("heartbeat interval must be positive")
    stopped = Event()
    state = {"host": socket.gethostname(), "pid": os.getpid(),
             "started_at": _now(), "interval_seconds": interval, "state": "live"}

    def publish():
        try:
            _write(path, {**state, "heartbeat_at": _now()})
        except OSError as error:
            # Observation must not change the native training outcome.
            print(f"node heartbeat write failed: {error}", file=sys.stderr, flush=True)

    def watch():
        while not stopped.wait(interval):
            publish()

    publish()
    thread = Thread(target=watch, name="node-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()
        state.update(state="stopped", finished_at=_now())
        publish()


def run_child(argv, log: Path, stopped: Event) -> int:
    """Wait for an owned foreground child; stop grants its existing cleanup budget.

    This is not a completion witness or a detached/requeue supervisor. Callers
    still verify the child's cleanup and result receipts before releasing inputs.
    """
    from behave.core.runtime_defaults import JOB_CLEANUP_SECONDS

    with log.open("ab") as stream:
        child = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        while child.poll() is None:
            if stopped.wait(1):
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    return child.wait()
                # The shared runtime gets time to terminate its tool processes
                # and commit receipts. This is cleanup, not a rollout wall limit.
                try:
                    return child.wait(timeout=JOB_CLEANUP_SECONDS)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    return child.wait()
        return child.returncode


def wait_control(directory: Path, *, allow_failure: bool = False):
    """Wait on the existing detached supervisor, not on an agent attempt clock."""
    directory = Path(directory)

    wake = Event()
    while True:
        state = status_of(directory)
        if state["state"] in ("completed", "failed", "timed-out", "launch-error"):
            if state["state"] != "completed" and not allow_failure:
                raise RuntimeError(f"phase did not complete: {directory.name}: {state['state']}")
            return state
        wake.wait(30)



def submit(directory, argv, *, cwd, wall_seconds, metadata, cleanup_seconds=...):
    """Create a fresh, immutable launch record and return without waiting.

    Detaching the process session does not escape a Slurm step or other enclosing
    cgroup. A compute-side submitter must keep that scope alive until its owned
    controls settle; returning from a short launcher step can kill supervisors.
    """
    from behave.core import runtime_defaults as defaults

    if cleanup_seconds is ...:
        cleanup_seconds = defaults.JOB_CLEANUP_SECONDS
    cwd = Path(cwd).resolve()
    if wall_seconds is not None and (type(wall_seconds) is not int or wall_seconds < 1):
        raise ValueError('wall_seconds must be a positive integer or None')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    plan = {'argv': argv, 'wall_seconds': wall_seconds,
            'cleanup_seconds': cleanup_seconds,
            'heartbeat_seconds': defaults.JOB_HEARTBEAT_SECONDS, 'cwd': str(cwd),
            'submitted_at': _now(), 'metadata': metadata}
    _write(directory / 'launch.json', plan)
    # A later edit to the launcher must not change an already submitted run.
    runner = directory / 'runner.py'
    process = None
    try:
        shutil.copyfile(__file__, runner)
        with (directory / 'run.log').open('xb') as log:
            process = subprocess.Popen(
                [sys.executable, str(runner), '_supervise', str(directory)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True)
    except Exception as error:
        # The launch intent is already visible. Without a terminal receipt a
        # known pre-supervisor failure looks submitted forever to Train/Eval.
        # After Popen returns, however, only that supervisor owns status; a
        # log/PID receipt failure cannot prove its child did not start.
        if process is None:
            try:
                _write(directory / 'status.json', {
                    'state': 'launch-error', 'stage': 'supervisor_start',
                    'supervisor_started': False, 'finished_at': _now(),
                    'error': f'{type(error).__name__}: {error}',
                })
            except OSError as report_error:
                print(f'could not record supervisor launch failure: {report_error}',
                      file=sys.stderr, flush=True)
        raise
    _write(directory / 'supervisor.json', {'pid': process.pid})
    return {'directory': str(directory), 'supervisor_pid': process.pid,
            'state': 'submitted', 'log': str(directory / 'run.log')}


def _stop_command(process, cleanup_seconds):
    """Settle only our command's process group, not arbitrary Slurm workers.

    This local transport result does not certify remote workload quiescence.
    Train/Eval still require their node and artifact cleanup witnesses.
    """
    deadline = None if cleanup_seconds is None else time.monotonic() + cleanup_seconds
    errors = []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append(f'{type(error).__name__}: {error}')
        try:
            process.wait(timeout=None if deadline is None else max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            continue
        except OSError as error:
            errors.append(f'{type(error).__name__}: {error}')
            continue
        break
    try:
        code = process.poll()
    except OSError as error:
        errors.append(f'{type(error).__name__}: {error}')
        code = None
    return {'returncode': code, 'cleanup_scope': 'local-command',
            'cleanup_state': 'complete' if code is not None else 'unconfirmed',
            **({'cleanup_errors': errors} if errors else {})}


def supervise(directory):
    directory = Path(directory)
    plan = json.loads((directory / 'launch.json').read_text())
    began = time.monotonic()
    process = None
    status = {'state': 'starting', 'started_at': _now(), 'argv': plan['argv'], 'command_started': False}
    stage = 'supervisor_setup'
    try:
        _write(directory / 'status.json', status)
        stage = 'command_start'
        process = subprocess.Popen(
            plan['argv'], cwd=plan['cwd'], stdin=subprocess.DEVNULL,
            start_new_session=True)
        stage = 'supervision'
        status.update(state='supervising', command_pid=process.pid, command_started=True)
        while True:
            remaining = (math.inf if plan['wall_seconds'] is None else
                         plan['wall_seconds'] - (time.monotonic() - began))
            if remaining <= 0:
                raise subprocess.TimeoutExpired(plan['argv'], plan['wall_seconds'])
            status.update(heartbeat_at=_now(), elapsed_s=round(time.monotonic()-began, 2))
            _write(directory / 'status.json', status)
            try:
                code = process.wait(timeout=min(plan['heartbeat_seconds'], remaining))
                status.update(state='completed' if code == 0 else 'failed', returncode=code)
                break
            except subprocess.TimeoutExpired:
                continue
    except subprocess.TimeoutExpired:
        # Only this supervisor's new process group is signalled. Terminating srun
        # cancels its own step; Slurm's independent --time is the remote backstop.
        status.update(state='timed-out', **_stop_command(process, plan['cleanup_seconds']))
    except Exception as error:  # noqa: BLE001 -- an operator supervisor must publish its failure
        # Keep the legacy terminal state, but distinguish a failed spawn from
        # losing supervision of an already running command. Neither is retry
        # permission, and no terminal receipt precedes the cleanup attempt.
        status.update(state='launch-error', stage=stage, error=f'{type(error).__name__}: {error}')
        if process is not None:
            status.update(_stop_command(process, plan['cleanup_seconds']))
    finally:
        status.update(finished_at=_now(), elapsed_s=round(time.monotonic()-began, 2))
        try:
            _write(directory / 'status.json', status)
        except OSError as error:
            # The copied supervisor's log remains useful if control storage is
            # unavailable. Do not dump argv/environment, or report success.
            print(f"supervisor status write failed: {error}; state={status['state']}, "
                  f"command_started={status['command_started']}, "
                  f"cleanup={status.get('cleanup_state', 'unconfirmed')}", file=sys.stderr, flush=True)
            raise


def status_of(directory):
    """Observe an existing owner, reopening transiently stale NFS handles.

    Retry only reads; never relaunch work or turn an unavailable status into a
    success. Persistent IO failures still propagate to the operator.
    """
    return _retry_io(_status_of, directory)


def _status_of(directory):
    """Local observation. A stale heartbeat is never a PASS."""
    directory = Path(directory).resolve()
    launch = json.loads((directory / 'launch.json').read_text())
    path = directory / 'status.json'
    status = json.loads(path.read_text()) if path.is_file() else {'state': 'submitted'}
    beat = status.get('heartbeat_at')
    age = ((datetime.now(UTC) - datetime.fromisoformat(beat)).total_seconds()
           if beat is not None else None)
    status['heartbeat_age_seconds'] = age
    status['heartbeat_overdue'] = (
        status['state'] in ('starting', 'supervising') and age is not None
        and age > launch['heartbeat_seconds'])
    rows, unreadable = [], []
    def read_result(path):
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            # The existing validator publishes result.json with a regular write.
            # A poll during that write must not invent either a pass or failure.
            unreadable.append(str(path))
            return None
    for path in sorted((directory / 'validation').glob('*/result.json')):
        row = read_result(path)
        if row is not None:
            rows.append(row)
    final = directory / 'validation/result.json'
    final_record = read_result(final) if final.is_file() else None
    return dict(directory=str(directory), metadata=launch['metadata'], **status,
                completed_tasks=len(rows),
                passing_tasks=sum(r.get('passed') is True for r in rows),
                failed_tasks=[r.get('task_id') for r in rows if r.get('passed') is not True],
                validation_passed=(final_record.get('passed')
                                   if final_record is not None else None),
                unreadable_results=unreadable,
                log=str(directory / 'run.log'))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "_supervise":
        raise SystemExit("internal supervisor expects _supervise DIRECTORY")
    supervise(sys.argv[2])
