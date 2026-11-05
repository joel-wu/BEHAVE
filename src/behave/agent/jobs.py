"""Durable control for one trusted allocated job; no orphan takeover.

The worker journal authorizes at most one dispatch. A separate immutable cancel
request can be published while the worker holds ownership. Only its lifetime
supervisor may publish cleanup evidence. This is not a distributed scheduler.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, array, fields, text, thaw_json
from behave.isolation.service import ServiceResult

from .journal import AttemptJournal, Event
from .run_store import JournalDirectory


@dataclass(frozen=True, slots=True)
class JobRequest:
    argv: tuple[str, ...]
    cwd: str
    wall_seconds: int | None
    cpus: int = 1
    allocation: int | None = None
    cleanup_seconds: int | None = defaults.JOB_CLEANUP_SECONDS
    output_bytes: int | None = defaults.JOB_OUTPUT_BYTES
    network: str = "none"
    gpus: int = 0
    host: str | None = None

    def __post_init__(self) -> None:
        if type(self.argv) is not tuple or not self.argv:
            raise ValueError("job argv must be a nonempty tuple")
        if any(type(arg) is not str or "\0" in arg for arg in self.argv):
            raise ValueError("job arguments must be strings without NUL")
        if not Path(self.argv[0]).is_absolute():
            raise ValueError("job needs an absolute executable")
        if not Path(text(self.cwd, "job cwd")).is_absolute():
            raise ValueError("job cwd must be absolute")
        for name in ("wall_seconds", "cpus", "cleanup_seconds"):
            value = getattr(self, name)
            if name != "cpus" and value is None:
                continue
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.output_bytes is not None and (type(self.output_bytes) is not int or self.output_bytes < 1):
            raise ValueError("output_bytes must be positive or None")
        if self.allocation is not None and (type(self.allocation) is not int or self.allocation < 1):
            raise ValueError("allocation must be a positive integer or None")
        if self.network not in {"none", "host"}:
            raise ValueError("job network must be none or host")
        if type(self.gpus) is not int or self.gpus < 0:
            raise ValueError("job GPU count must be a nonnegative integer")
        if self.gpus and self.allocation is None:
            raise ValueError("GPU jobs require an explicit Slurm allocation")
        if self.host is not None and (self.allocation is None or type(self.host) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", self.host) is None):
            raise ValueError("node placement requires one explicit host and allocation")

    def as_dict(self) -> dict:
        # Keep existing CPU request identities byte-for-byte unchanged.
        return {**{name: getattr(self, name) for name in self.__dataclass_fields__ if name not in {"gpus", "host"}},
                "argv": list(self.argv), **({"gpus": self.gpus} if self.gpus else {}),
                **({"host": self.host} if self.host is not None else {})}

    @property
    def schema(self) -> str:
        return "behave.allocated-job/3" if self.host is not None else "behave.allocated-job/2" if self.gpus else "behave.cpu-job/1"

    @classmethod
    def from_dict(cls, value: object) -> JobRequest:
        keys = set(cls.__dataclass_fields__) - {"gpus", "host"}
        row = fields(value, keys | ({"gpus", "host"} & value.keys() if type(value) is dict else set()))
        return cls(**{**row, "argv": tuple(array(row["argv"]))})

    def slurm_prefix(self, *, overlap: bool = False) -> tuple[str, ...]:
        """One step in an existing allocation, never a new allocation request."""
        if self.allocation is None or type(overlap) is not bool:
            raise ValueError("Slurm launch needs an allocation and explicit overlap choice")
        # Leave time for the worker to drain/reap and publish its receipt before
        # Slurm's independent step limit. Scheduling wait is bounded separately.
        time_limit = ()
        if self.wall_seconds is not None and self.cleanup_seconds is not None:
            seconds = self.wall_seconds + self.cleanup_seconds + defaults.JOB_SLURM_EXIT_SECONDS
            time_limit = (f"--time={(seconds + 59) // 60}",)
        return ("srun", f"--jobid={self.allocation}", "--exact",
                f"--immediate={defaults.JOB_SLURM_WAIT_SECONDS}",
                "--nodes=1", "--ntasks=1", f"--cpus-per-task={self.cpus}",
                f"--gpus-per-task={self.gpus}" if self.gpus else "--gres=none",
                "--kill-on-bad-exit=1", *time_limit, "--export=NONE",
                *((f"--nodelist={self.host}",) if self.host is not None else ()),
                *(("--overlap",) if overlap else ()))


@dataclass(frozen=True, slots=True)
class JobObservation:
    key: str
    request: JobRequest
    state: str
    head: BlobRef
    cancel_requested: bool
    worker: dict | None
    result: ServiceResult | None

    @property
    def cleanup(self) -> CleanupState:
        # Even a prepared job could have an already queued but unregistered
        # worker. A cancel acknowledgement alone never authorizes replacement.
        return CleanupState.UNKNOWN if self.result is None else self.result.cleanup_state

    def as_dict(self) -> dict:
        return {"key": self.key, "request": self.request.as_dict(), "state": self.state,
                "head": self.head.as_dict(), "cancel_requested": self.cancel_requested,
                "worker": self.worker, "cleanup": self.cleanup.value,
                "result": None if self.result is None else {
                    "status": self.result.status, "execution_status": self.result.execution_status,
                    "returncode": self.result.returncode,
                    "stdout_bytes": len(self.result.stdout), "stderr_bytes": len(self.result.stderr),
                    "elapsed_seconds": self.result.elapsed_seconds,
                    "cleanup_elapsed_seconds": self.result.cleanup_elapsed_seconds,
                    "cleanup_error": self.result.cleanup_error}}


def _result(event: Event, run: JournalDirectory, request: JobRequest) -> ServiceResult:
    raw = thaw_json(event.payload)
    current = raw.get("schema") in ("behave.job-result/2", "behave.job-result/3")
    detailed = raw.get("schema") == "behave.job-result/3"
    row = fields(raw, {"status", "returncode", "stdout", "stderr", "elapsed_seconds", "cleanup_elapsed_seconds"}
                 | ({"schema", "execution_status"} if current else set())
                 | ({"cleanup_error"} if detailed else set()))
    if row["status"] not in {"exited", "timeout", "cancelled", "output-limit", "launch-failed",
                             "cleanup-incomplete", "supervisor-error"}:
        raise RecordError("job-result-invalid", "unknown supervisor status")
    if ((row["returncode"] is not None and type(row["returncode"]) is not int)
            or (row["status"] == "exited" and row["returncode"] is None)):
        raise RecordError("job-result-invalid", "returncode must be an integer or null")
    for name in ("elapsed_seconds", "cleanup_elapsed_seconds"):
        if type(row[name]) not in {float, int} or not math.isfinite(row[name]) or row[name] < 0:
            raise RecordError("job-result-invalid", "invalid supervisor timing")
    refs = tuple(BlobRef.from_dict(row[name]) for name in ("stdout", "stderr"))
    if (tuple(dict.fromkeys(refs)) != event.artifacts
            or (request.output_bytes is not None and sum(ref.size_bytes for ref in refs) > request.output_bytes)):
        raise RecordError("job-result-invalid", "invalid output closure or bound")
    try:
        return ServiceResult(row["status"], row["returncode"], *(run.objects.read(ref) for ref in refs),
                             row["elapsed_seconds"], row["cleanup_elapsed_seconds"],
                             execution_status=row["execution_status"] if current else None,
                             cleanup_error=row["cleanup_error"] if detailed else None)
    except ValueError as error:
        raise RecordError("job-result-invalid", str(error)) from None


def validate_worker(value: object, request: JobRequest) -> dict:
    row = fields(value, {"host", "boot_id", "pid", "allocation", "step"}
                 | ({"cuda_visible_devices"} if request.gpus else set()))
    text(row["host"], "worker host")
    if request.host is not None and row["host"] != request.host:
        raise RecordError("job-worker-invalid", "worker is not on the requested host")
    text(row["boot_id"], "worker boot identity")
    if type(row["pid"]) is not int or row["pid"] < 1 or row["allocation"] != request.allocation:
        raise RecordError("job-worker-invalid", "worker does not match its request")
    if request.allocation is None:
        valid_step = row["step"] is None
    else:
        valid_step = type(row["allocation"]) is int and type(row["step"]) is int and row["step"] >= 0
    if not valid_step:
        raise RecordError("job-worker-invalid", "worker must identify a single numeric Slurm step")
    if request.gpus:
        devices = row["cuda_visible_devices"]
        # Slurm supplies a visible-device list, not permission to use all GPUs.
        # Keep UUID/MIG spellings intact; never substitute host ordinals.
        assigned = devices.split(",") if type(devices) is str else []
        if (len(assigned) != request.gpus
                or len(set(assigned)) != request.gpus
                or any(re.fullmatch(r"(?:[0-9]+|(?:GPU|MIG)-[A-Za-z0-9/-]+)", item) is None
                       for item in assigned)):
            raise RecordError("job-gpus-unavailable", "Slurm device visibility must match the requested GPU count")
    return row


class JobControl:
    def __init__(self, directory: str | Path):
        self.run = JournalDirectory(directory)

    def create(self, request: JobRequest) -> str:
        if type(request) is not JobRequest:
            raise TypeError("job creation needs JobRequest")
        with self.run.owner():
            journal = self.run.journal("job")
            if journal.load() or self.run.journal("cancel").load():
                raise RecordError("job-already-created", "use a fresh directory for a new job")
            event = journal.append((), "job_created", {"schema": request.schema,
                                   "nonce": uuid.uuid4().hex, "request": request.as_dict()})
            return BlobRef.of(event.to_bytes()).sha256

    def _load(self, key: str) -> tuple[AttemptJournal, tuple[Event, ...], JobRequest]:
        journal = self.run.journal("job")
        events = journal.load()
        if not events or BlobRef.of(events[0].to_bytes()).sha256 != key:
            raise RecordError("job-identity-mismatch", "expected the exact job creation key")
        if tuple(e.kind for e in events) != ("job_created", "job_started", "job_settled")[:len(events)]:
            raise RecordError("job-history-invalid", "unexpected job lifecycle")
        if any(event.artifacts for event in events[:2]):
            raise RecordError("job-history-invalid", "only a settlement may declare output artifacts")
        row = fields(thaw_json(events[0].payload), {"schema", "nonce", "request"})
        request = JobRequest.from_dict(row["request"])
        if (row["schema"] != request.schema or type(row["nonce"]) is not str
                or re.fullmatch(r"[0-9a-f]{32}", row["nonce"]) is None):
            raise RecordError("job-history-invalid", "invalid job creation record")
        return journal, events, request

    def cancelled(self, key: str) -> bool:
        events = self.run.journal("cancel").load()
        if not events:
            return False
        if len(events) != 1 or events[0].kind != "job_cancel_requested" or thaw_json(events[0].payload) != {"key": key}:
            raise RecordError("job-cancel-invalid", "cancel request is for another job")
        return True

    def cancel(self, key: str) -> JobObservation:
        """Idempotent request; no signals, PID lookup, or cleanup assertion."""
        self.inspect(key)
        try:
            self.run.journal("cancel").append((), "job_cancel_requested", {"key": key})
        except RecordError as error:
            if error.code != "journal-conflict" or not self.cancelled(key):
                raise
        return self.inspect(key)

    def inspect(self, key: str) -> JobObservation:
        _, events, request = self._load(key)
        worker = None if len(events) == 1 else validate_worker(thaw_json(events[1].payload), request)
        result = None if len(events) < 3 else _result(events[2], self.run, request)
        return JobObservation(key, request, ("prepared", "started", "settled")[len(events) - 1],
                              BlobRef.of(events[-1].to_bytes()), self.cancelled(key), worker, result)

    def _settle(self, journal: AttemptJournal, events: tuple[Event, ...], result: ServiceResult) -> None:
        """Worker-only commit, after the existing service supervisor returns."""
        refs = tuple(self.run.objects.put(data) for data in (result.stdout, result.stderr))
        payload = {"schema": "behave.job-result/3", "status": result.status,
                   "execution_status": result.execution_status, "returncode": result.returncode,
                   "stdout": refs[0].as_dict(), "stderr": refs[1].as_dict(),
                   "elapsed_seconds": result.elapsed_seconds,
                   "cleanup_elapsed_seconds": result.cleanup_elapsed_seconds,
                   "cleanup_error": result.cleanup_error}
        # Validate before committing, including output bound and known status.
        request = JobRequest.from_dict(thaw_json(events[0].payload)["request"])
        _result(Event(2, BlobRef.of(events[-1].to_bytes()), "job_settled", payload,
                      tuple(dict.fromkeys(refs))), self.run, request)
        journal.append(events, "job_settled", payload, refs)
