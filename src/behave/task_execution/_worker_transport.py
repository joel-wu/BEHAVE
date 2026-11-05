"""Transport for the fixed trusted judge, testbench and PPA services.

Submitted code runs only through the services' existing candidate isolation.
Neither a task nor a model can choose a worker module or executable.
"""

from __future__ import annotations

import os
import stat
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.paths import scratch_root, tool_resource_environment
from behave.core.records import canonical_json, fields, parse_json, version
from behave.core.storage import DirectoryStore

_SERVICES = {
    "ppa": ("behave.ppa.worker", "behave.ppa-worker/1"),
    "judge": ("behave.task_execution.judge", "behave.judge-worker/5"),
    "testbench": ("behave.task_execution.cocotb_worker", "behave.testbench-worker/4"),
}


@dataclass(frozen=True, slots=True)
class WorkerResponse:
    code: str | None
    retryable: bool
    result: object
    evidence: tuple[BlobRef, ...]
    cleanup: CleanupState = CleanupState.COMPLETE


def read_record(path: Path) -> dict:
    """Read a private JSON result; a mistaken FIFO must not block the coordinator."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("service response is not a regular file")
        data = stream.read()
    return parse_json(data)


def cpu_entrypoint(kind: str, pool_image: str) -> None:
    """Install the public allocation scope inside a fixed trusted worker."""
    from importlib import import_module

    from behave.sim import api

    module, _ = _SERVICES[kind]
    endpoint = api.CpuPoolEndpoint.from_dict(parse_json(pool_image.encode()))
    with api.cpu_scope(endpoint):
        import_module(module).main()


def exchange(
    kind: str, packet: bytes, *, workdir: Path, objects: DirectoryStore,
    deadline: float, timeout_seconds: int, log_bytes: int,
    cancelled: Callable[[], bool] | None = None,
    cpu_pool: dict | None = None,
) -> WorkerResponse:
    from behave.agent.lifetime import current_window
    from behave.isolation.runtime_environment import runtime_environment
    from behave.isolation.service import run_service

    if kind not in _SERVICES or type(packet) is not bytes:
        raise ValueError("invalid fixed-service request")
    module, schema = _SERVICES[kind]
    packet_ref = BlobRef.of(packet)
    bootstrap = ("import sys; sys.path.insert(0, sys.argv.pop(1)); from "
                 + module + " import main; main()")
    argv = (sys.executable, "-I", "-B", "-c", bootstrap, str(Path(__file__).resolve().parents[2]),
            str(workdir), packet_ref.sha256, str(packet_ref.size_bytes))
    if cpu_pool is not None:
        bootstrap = ("import sys; sys.path.insert(0,sys.argv.pop(1)); "
                     "from behave.task_execution._worker_transport import cpu_entrypoint; "
                     "cpu_entrypoint(sys.argv.pop(1),sys.argv.pop(1))")
        argv = (sys.executable, "-I", "-B", "-c", bootstrap, str(Path(__file__).resolve().parents[2]),
                kind, canonical_json(cpu_pool).decode(), str(workdir), packet_ref.sha256, str(packet_ref.size_bytes))
    # Isolation's immutable, digest-verified runtime images are node-local and
    # reusable. Pointing TMPDIR at each dispatch silently rebuilt the complete
    # Python/site-package images for every TB and judge call. Mutable submitted
    # inputs, builds, CAS and response files still use the explicit workdir.
    local_root = str(scratch_root())
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                   "TMPDIR": local_root, "BEHAVE_SCRATCH_DIR": local_root,
                   "XDG_CACHE_HOME": str(workdir / "cache"),
                   "BEHAVE_WORK_DIR": str(workdir / "sim-work")}
    environment.update(tool_resource_environment())
    environment["BEHAVE_RUNTIME_DIR"] = str(runtime_environment().directory)
    # These are existing public Sim execution settings, not candidate-provided
    # environment. Keep them across the trusted service boundary; otherwise a
    # caller's memory-conscious lane count silently becomes the auto default.
    for name in ("BEHAVE_REPLAY_WORKERS", "BEHAVE_ANALYSIS_WORKERS"):
        if name in os.environ:
            environment[name] = os.environ[name]
    window = current_window()
    if window is not None:
        deadline = min(deadline, time.monotonic() + window.remaining())
        parent_cancelled = cancelled
        cancelled = lambda: window.control.cancelled() or (parent_cancelled is not None and parent_cancelled())
    try:
        got = run_service(argv, cwd=workdir, environment=environment, deadline=deadline,
                          output_bytes=log_bytes, cancelled=cancelled)
    except Exception as error:  # noqa: BLE001 -- absent lifetime evidence must stop retries
        # An exception is not a cleanup receipt. Do not retry or advance from it.
        traceback.print_exc()
        log = objects.put(canonical_json({"schema": f"behave.{kind}-worker-receipt/4",
                                         "request": packet_ref.as_dict(), "cleanup": "unknown",
                                         "error_type": type(error).__name__}))
        return WorkerResponse(f"{kind}-worker-supervision-failed", False, None,
                              (log, objects.put(packet)), CleanupState.UNKNOWN)
    stdout, stderr = objects.put(got.stdout), objects.put(got.stderr)
    limits = {"component_timeout_seconds": timeout_seconds, "worker_log_bytes": log_bytes} if kind == "judge" else {
        "timeout_seconds": timeout_seconds, "log_bytes": log_bytes}
    log = objects.put(canonical_json({
        "schema": f"behave.{kind}-worker-receipt/4", "request": packet_ref.as_dict(),
        "status": got.status, "returncode": got.returncode, "elapsed_seconds": got.elapsed_seconds,
        "execution_status": got.execution_status,
        "cleanup": got.cleanup_state.value,
        "cleanup_error": got.cleanup_error,
        "dropped_output_bytes": got.dropped_output_bytes,
        **limits, "stdout": stdout.as_dict(), "stderr": stderr.as_dict(),
    }))
    logs = (log, objects.put(packet), stdout, stderr)
    if got.status != "exited" or got.returncode != 0:
        suffix = got.status if got.status != "exited" else "failed"
        return WorkerResponse(f"{kind}-worker-{suffix}", got.cleanup_state.quiescent and got.status != "cancelled",
                              None, logs, got.cleanup_state)
    try:
        row = fields(read_record(workdir / "response.json"), {"schema", "request", "result"})
        version(row["schema"], schema)
        if BlobRef.from_dict(row["request"]) != packet_ref:
            raise ValueError("service response belongs to another dispatch")
    except (OSError, ValueError, TypeError):
        traceback.print_exc()
        return WorkerResponse(f"{kind}-worker-invalid-response", True, None, logs, got.cleanup_state)
    return WorkerResponse(None, False, row["result"], logs, got.cleanup_state)
