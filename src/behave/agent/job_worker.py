"""Trusted allocated job worker and nonblocking inspect/cancel commands.

No takeover, scheduler polling, or PID-based kill operation is exposed. The
durable request contains no environment values; this worker starts from a small
allowlist. Untrusted workloads must retain their own IsolationSpec boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.isolation.service import ServiceResult, run_service

from .jobs import JobControl, JobObservation, JobRequest, validate_worker


def worker_identity(request: JobRequest) -> dict:
    allocation = step = None
    if request.allocation is not None:
        try:
            allocation, step = (int(os.environ[name]) for name in ("SLURM_JOB_ID", "SLURM_STEP_ID"))
            if os.environ.get("SLURM_PROCID") != "0" or os.environ.get("SLURM_STEP_NUM_TASKS") != "1":
                raise ValueError("not a singleton step")
        except (KeyError, ValueError) as error:
            raise RecordError("job-worker-invalid", "expected one task inside the requested allocation") from error
    return validate_worker({"host": socket.gethostname(),
                            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                            "pid": os.getpid(), "allocation": allocation, "step": step,
                            **({"cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES")}
                               if request.gpus else {})}, request)


def execute(directory: Path, key: str) -> JobObservation:
    control = JobControl(directory)
    with control.run.owner():
        observed = control.inspect(key)
        if observed.state == "settled":
            return observed
        if observed.state == "started":
            raise RecordError("job-already-dispatched", "lost worker needs reconciliation, never redispatch")
        journal, events, request = control._load(key)
        identity = worker_identity(request)
        # CPU affinity applies to the trusted command and all its descendants,
        # not to the caller. Local and Slurm workers use the same lifetime code.
        from behave.core.resources import allocated_cpus

        cpus = allocated_cpus()
        if len(cpus) < request.cpus:
            raise RecordError("job-cpus-unavailable", "worker affinity cannot satisfy the request")
        argv = ("/usr/bin/taskset", "--cpu-list", ",".join(map(str, cpus[:request.cpus])), *request.argv)
        event = journal.append(events, "job_started", identity)
        events += (event,)
        try:
            result = run_service(argv, cwd=Path(request.cwd),
                                 environment={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1",
                                              "PYTHONUNBUFFERED": "1", "LANG": "C.UTF-8",
                                              "CUDA_VISIBLE_DEVICES": identity.get("cuda_visible_devices", "")},
                                 deadline=None if request.wall_seconds is None else time.monotonic() + request.wall_seconds,
                                 output_bytes=request.output_bytes, network=request.network,
                                 cancelled=lambda: control.cancelled(key), cleanup_seconds=request.cleanup_seconds)
        except Exception:
            # Preserve unknown cleanup in the receipt, but surface the original
            # exception to the operator instead of silently losing diagnostics.
            control._settle(journal, events, ServiceResult("supervisor-error", None, b"", b"", 0.))
            raise
        control._settle(journal, events, result)
        return control.inspect(key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "inspect", "cancel"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("key")
    args = parser.parse_args(argv)
    # Validate the digest spelling without opening any scheduler connection.
    BlobRef(args.key, 0)
    control = JobControl(args.directory)
    if args.action == "run":
        result = execute(args.directory, args.key)
        if result.result is not None:
            sys.stdout.buffer.write(result.result.stdout)
            sys.stderr.buffer.write(result.result.stderr)
        return (0 if result.result is not None and result.result.status == "exited"
                and result.result.returncode == 0 and result.cleanup.quiescent else 1)
    result = getattr(control, args.action)(args.key)
    print(json.dumps(result.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
