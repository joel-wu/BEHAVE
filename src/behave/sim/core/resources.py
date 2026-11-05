"""Resource allocation policies and lifetime hooks for verifier subprocesses.

The host's CPU count is not an allocation.  In particular, Python's
``os.cpu_count()`` reports all CPUs on a Slurm node even when the process is
bound to a one-CPU cgroup.  Worker sizing therefore starts with the process's
actual affinity and uses scheduler metadata only on platforms without that
interface.
"""

from __future__ import annotations

import os
import re

from behave.core import runtime_defaults as defaults

__all__ = ["available_cpus", "bind_worker_to_parent", "job_cpu_limit", "solver_memory_mb", "worker_limit"]

_PR_SET_PDEATHSIG = 1

#: Resident memory for the complete exact-analysis process tree. The named
#: isolation supervisor enforces aggregate RSS, including source-audit children
#: and solver workers; each child also inherits this address-space ceiling.
ARTIFACT_MEMORY_MB = defaults.SIM_BEHAVIOR_ANALYSIS_MEMORY_MB
#: One query's internal Z3 ceiling, reserving headroom for the frontend,
#: source-audit state and result construction. This is not an RSS limit.
SOLVER_MEMORY_MB = defaults.SIM_BEHAVIOR_SOLVER_MEMORY_MB


def solver_memory_mb(artifact_memory_mb=ARTIFACT_MEMORY_MB):
    """Derive one query's Z3 ceiling from the complete artifact ceiling."""

    if artifact_memory_mb is None:
        return None
    if type(artifact_memory_mb) is not int or artifact_memory_mb < 1:
        raise ValueError("an artifact memory ceiling is a positive integer")
    return max(
        1,
        artifact_memory_mb * SOLVER_MEMORY_MB // ARTIFACT_MEMORY_MB,
    )


def bind_worker_to_parent():
    """Best-effort Linux parent-death hook for disposable pool workers.

    This supplements the owning job's process cleanup; it is not isolation or
    a cleanup receipt. Preserve the portable fallback when libc is unavailable.
    """
    import ctypes
    import signal

    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
        # If the parent died before registration, its death signal was missed.
        if os.getppid() == 1:
            os._exit(0)
    except Exception:  # noqa: BLE001, S110 - retain the best-effort portable hook
        pass


def _positive_prefix(value: str | None) -> int | None:
    """Return the leading positive integer in one scheduler quantity."""
    if not value:
        return None
    match = re.match(r"\s*(\d+)", value)
    if match is None:
        return None
    count = int(match.group(1))
    return count if count > 0 else None


def available_cpus() -> int:
    """Number of CPUs this process is entitled to schedule work on.

    ``sched_getaffinity`` is authoritative when available because it observes
    the cpuset applied to this process.  The environment fallbacks cover batch
    systems and platforms without a process-affinity API.  The hardware count
    is deliberately last.
    """
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        try:
            count = len(affinity(0))
        except (OSError, TypeError, ValueError):
            pass
        else:
            if count > 0:
                return count

    for variable in (
        "SLURM_CPUS_PER_TASK",
        "SLURM_CPUS_ON_NODE",
        "SLURM_JOB_CPUS_PER_NODE",
        "PBS_NP",
        "NSLOTS",
    ):
        count = _positive_prefix(os.environ.get(variable))
        if count is not None:
            return count

    return max(1, os.cpu_count() or 1)


def job_cpu_limit(requested: int) -> int:
    """Physical concurrency already reserved by a named job's trusted parent.

    The namespace launcher supplies this ceiling explicitly, including to
    spawned descendants. It is not another pool or a request for more CPUs.
    Logical work items/seeds must not be removed to meet this ceiling.
    """
    return _worker_limit("BEHAVE_JOB_CPU_LIMIT", requested)


def worker_limit(variable: str, requested: int) -> int:
    """Apply one optional, explicit process-pool cap.

    CPU affinity answers what the process *may* use.  A release run with outer
    xdist workers also needs a separate answer for what each worker *should*
    use internally.  Keeping that policy in a named environment variable avoids
    falsifying affinity and makes the cap part of the recorded release config.
    """
    return job_cpu_limit(_worker_limit(variable, requested))


def _worker_limit(variable: str, requested: int) -> int:
    if type(requested) is not int or requested < 1:
        raise ValueError(f"requested workers must be a positive integer, got {requested!r}")
    raw = os.environ.get(variable)
    if raw is None:
        return requested
    try:
        limit = int(raw)
    except ValueError:
        raise ValueError(f"{variable} must be a positive integer, got {raw!r}") from None
    if str(limit) != raw.strip() or limit < 1:
        raise ValueError(f"{variable} must be a positive integer, got {raw!r}")
    return min(requested, limit)
