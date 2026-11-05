"""Weaker launcher for trusted unit fixtures, never a production fallback.

Restricted hosts still use a fresh worker and its production stdio protocol.
This does not provide namespaces, filesystem confinement or resource accounting;
tests of those guarantees must use the real isolation backend.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import behave
from behave.core import runtime_defaults as defaults
from behave.sim.job_isolation import IsolatedJobFailure, _strict_json
from behave.sim.worker import WorkerFailed


def run_direct_job(name, payload, timeout_s, label=None, verbose=False):
    try:
        document = json.dumps({"job": name, "payload": payload}, allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise WorkerFailed("refused", f"this job's payload is not JSON: {error}") from None

    # Match the worktree that supplied the fixture, not another installed Behave.
    env = dict(os.environ)
    here = str(Path(behave.__file__).resolve().parents[1])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{here}{os.pathsep}{existing}" if existing else here
    started = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, "-m", "behave.sim.worker", "--stdio"], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    try:
        try:
            output, error = proc.communicate(document, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            raise WorkerFailed(
                "timeout", f"{label or name} did not finish in {timeout_s:.0f}s") from None
        if proc.returncode != 0:
            raise WorkerFailed("crashed", f"the child exited {proc.returncode}: "
                               + error.decode("utf-8", errors="replace"))
        try:
            got = _strict_json(output)
        except IsolatedJobFailure as error:
            raise WorkerFailed(error.how, error.detail, code=error.code) from None
        if got.get("ok") is not True:
            raise WorkerFailed("crashed", str(got.get("error", "")))
        if set(got) != {"ok", "result"}:
            raise WorkerFailed("no-answer", "a successful response must contain exactly ok and result")
        if verbose:
            print(f"[worker] {label or name} finished in "
                  f"{time.monotonic() - started:.1f}s", flush=True)
        return got["result"]
    finally:
        try:
            # start_new_session makes pid the group ID, even after the leader
            # exits. Reap descendants on success, timeout and interruption.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=defaults.SIM_JOB_CLEANUP_SECONDS)
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
