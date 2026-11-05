"""Independent submitted-TB orchestration; no golden or artifact-score access.

The backend owns TB execution and diagnostic coverage collection. This module
only freezes its inputs, validates result binding, and journals bounded retries.
The cocotb backend executes ordinary submitted tests without exposing the judge.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Protocol

from behave.agent.admission import ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import AttemptJournal
from behave.agent.replay import Tape
from behave.agent.workspace import WorkspaceSnapshot
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, FileData, LoadedSource, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, fields, parse_json, text, thaw_json, version
from behave.core.storage import DirectoryStore
from behave.data.published import PublicTaskInputs

from .capture import load_submission
from .result import TaskResult
from .run_store import RunStore
from .solver import solver_input_files
from .tb_report import TBObservation, TBReport


@dataclass(frozen=True, slots=True)
class TBPolicy:
    """Pinned command budgets; the service additionally bounds setup/cleanup.

    ``timeout_seconds`` limits simulation, independently of compilation. The
    outer ceiling includes both commands and the configured overhead for isolation
    supervisors, runtime preparation and evidence export. Exceeding that outer
    ceiling is unavailable infrastructure, not proof that the TB is wrong.
    """

    backend: str
    collector: str
    metric: str
    max_retries: int = defaults.TB_MAX_RETRIES
    timeout_seconds: int = defaults.TB_RUN_SECONDS
    log_bytes: int = defaults.LOG_RETAIN_BYTES
    seed: int = defaults.TB_SEED
    compile_timeout_seconds: int = defaults.TB_COMPILE_SECONDS

    def __post_init__(self) -> None:
        for name in ("backend", "collector", "metric"):
            text(getattr(self, name), name)
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ValueError("testbench retry budget must be a nonnegative integer")
        for name in ("timeout_seconds", "compile_timeout_seconds", "log_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.seed) is not int or not 1 <= self.seed < (1 << 31):
            raise ValueError("testbench seed must be a positive signed 32-bit integer")

    def to_bytes(self) -> bytes:
        return canonical_json({"schema": "behave.testbench-policy/3", **asdict(self)})

    @property
    def worker_timeout_seconds(self) -> int:
        return self.compile_timeout_seconds + self.timeout_seconds + defaults.TB_WORKER_OVERHEAD_SECONDS

    @classmethod
    def from_bytes(cls, data: bytes) -> TBPolicy:
        row = fields(parse_json(data), {"schema", *(field.name for field in dataclass_fields(cls))})
        version(row["schema"], "behave.testbench-policy/3")
        return cls(**{key: value for key, value in row.items() if key != "schema"})


@dataclass(frozen=True, slots=True)
class TBRequest:
    task: PublicTaskInputs
    submission: BlobRef
    behavior: LoadedSource
    rtl: LoadedSource
    testbench: LoadedSource
    policy: TBPolicy
    dispatch: int
    workdir: Path
    cancelled: Callable[[], bool] = lambda: False


class TBBackend(Protocol):
    """Trusted implementation; submitted Python and DUT never own its report.

    Implementations must enforce the policy's worker limits and isolate all
    submitted code. Coverage comes from the pinned parser of simulator output,
    not a printed percentage. It remains a submitted-process diagnostic; no
    tamper-proof attestation or external correctness is claimed.
    """

    identity: str

    def observe(self, request: TBRequest, objects: DirectoryStore) -> TBObservation: ...


def record_final_testbench(*, task: PublicTaskInputs, result: TaskResult, run: RunStore,
                          policy: TBPolicy, backend: TBBackend, admission: ResourcePool,
                          cancelled: Callable[[], bool]) -> tuple[BlobRef | None, dict | None, tuple[BlobRef, ...]]:
    """The same final TB measurement for immediate and deferred evaluation."""
    try:
        if result.judgment is not None:
            result.judgment.cleanup_state.require_quiescent("judge")
        with run.owner():
            tb = observe_testbench(task=task, submission=result.submission, policy=policy,
                                   journal=run.journal("testbench"), backend=backend,
                                   admission=admission, cancelled=cancelled)
            _, refs = run.seal("testbench")
            reference = run.objects.put(canonical_json(tb.as_dict()))
            return reference, None, (*refs, reference)
    except Exception as error:
        if not isinstance(error, (RecordError, AttemptInterrupted, OSError)):
            logging.getLogger(__name__).exception("Final testbench processing failed; retaining the task result")
        code = "storage-unavailable" if isinstance(error, OSError) else f"testbench-{type(error).__name__}"
        return None, {"type": type(error).__name__, "code": getattr(error, "code", code)}, ()


def observe_testbench(
    *, task: PublicTaskInputs, submission: BlobRef, policy: TBPolicy,
    journal: AttemptJournal, backend: TBBackend,
    cancelled: Callable[[], bool] = lambda: False, admission: ResourcePool | None = None,
) -> TBReport:
    """Use a journal separate from solver and score judging; never modify either."""
    from behave.isolation.scratch import ScratchDirectory

    if type(task) is not PublicTaskInputs or type(policy) is not TBPolicy:
        raise TypeError("testbench observation requires the public task view and TBPolicy")
    if not callable(cancelled) or (admission is not None and type(admission) is not ResourcePool):
        raise TypeError("invalid testbench execution control")
    if backend.identity != policy.backend:
        raise RecordError("tb-backend-mismatch", "testbench policy selected another backend")
    frozen = load_submission(journal.objects, submission)
    if (frozen.task_id, frozen.release, frozen.revision) != (task.task_id, task.release, task.revision):
        raise RecordError("tb-task-mismatch", "submission belongs to another task")
    snapshot = WorkspaceSnapshot.from_bytes(read_verified(journal.objects, frozen.workspace))
    public_files = solver_input_files(task)
    public_refs = tuple(journal.objects.put(file.data) for file in public_files)
    policy_ref = journal.objects.put(policy.to_bytes())
    inputs = journal.objects.put(canonical_json({
        "schema": "behave.testbench-inputs/2", "task_id": task.task_id,
        "admission": None if admission is None else admission.policy.identity,
        "release": task.release.as_dict(), "revision": task.revision.as_dict(),
        "submission": submission.as_dict(), "policy": policy_ref.as_dict(),
        "spec_path": task.spec_path,
        "public_files": [file.reference.as_dict() for file in public_files],
        "artifacts": {name: getattr(frozen, name).as_dict() for name in ("behavior", "rtl", "testbench")},
    }))
    tape = Tape(journal)
    tape.record("tb_started", {"inputs": inputs.as_dict()},
                (inputs, submission, frozen.workspace, policy_ref, *public_refs,
                 *(file.content for file in snapshot.files)))
    absent = [name for name in ("testbench", "behavior", "rtl") if getattr(frozen, name).artifact is None]
    if absent:
        result = TBObservation("unavailable", "tb-components-missing")
        tape.record("tb_unavailable", {"missing": absent, "result": result.as_dict()})
    else:
        sources = [LoadedSource(component.artifact, tuple(
            FileData(file, read_verified(journal.objects, file.content)) for file in component.files))
            for component in (frozen.behavior, frozen.rtl, frozen.testbench)]
        rtl_ref = journal.objects.put(canonical_json(sources[1].artifact.as_dict()))
        for dispatch in range(policy.max_retries + 1):
            live = tape.cursor == len(tape.events)
            if not live and tape.events[tape.cursor].kind == "tb_unavailable":
                result = TBObservation.from_dict(thaw_json(tape.take("tb_unavailable").payload)["result"])
                break
            permit = admission.acquire(cancelled=cancelled) if admission is not None and live else nullcontext()
            try:
                if live and cancelled():
                    raise AttemptInterrupted("testbench-cancelled")
                work_cleanup = CleanupState.COMPLETE
                with permit, ScratchDirectory(prefix="behave-tb-", cleanup=lambda: work_cleanup) as local:  # noqa: B023 -- exits before the next dispatch
                    request = TBRequest(task, submission, *sources, policy, dispatch, local, cancelled)
                    work_cleanup = CleanupState.UNKNOWN if live else CleanupState.COMPLETE
                    result = _dispatch(tape, inputs, rtl_ref, backend, request, journal, admission)
                    work_cleanup = result.cleanup if live else CleanupState.COMPLETE
            except AttemptInterrupted as error:
                if not live:
                    raise
                result = TBObservation("unavailable", error.code)
                tape.record("tb_unavailable", {"result": result.as_dict()})
            if not result.retryable:
                break
    terminal = tape.record("tb_finished", {"inputs": inputs.as_dict(), "result": result.as_dict()})
    tape.finish()
    return TBReport(inputs, result, BlobRef.of(terminal.to_bytes()))


def _dispatch(tape: Tape, inputs: BlobRef, rtl_ref: BlobRef, backend: TBBackend,
              request: TBRequest, journal: AttemptJournal, admission: ResourcePool | None) -> TBObservation:
    if tape.begin("tb_requested", {"inputs": inputs.as_dict(), "dispatch": request.dispatch}):
        try:
            result = backend.observe(request, journal.objects)
            if type(result) is not TBObservation:
                raise TypeError("testbench backend must return TBObservation")
            measurement = result.coverage
            if measurement is not None and (measurement.rtl, measurement.collector, measurement.metric) != (
                    rtl_ref, request.policy.collector, request.policy.metric):
                raise ValueError("coverage does not match the submitted RTL and policy")
            for ref in result.closure:
                read_verified(journal.objects, ref)
        except AttemptInterrupted as error:
            result = TBObservation("unavailable", error.code, cleanup=CleanupState.UNKNOWN)
        except Exception:  # noqa: BLE001 -- TB failure never enters artifact scoring
            result = TBObservation("unavailable", "tb-backend-invalid-result", cleanup=CleanupState.UNKNOWN)
        if admission is not None:
            admission.quarantine(result.cleanup)  # Fence locally even if persistence fails.
        if not result.cleanup.quiescent:
            from behave.isolation.scratch import retention_record

            retained = journal.objects.put(retention_record(request.workdir, result.cleanup))
            result = replace(result, evidence=(*result.evidence, retained))
        tape.record("tb_settled", {"result": result.as_dict()}, result.closure)
    else:
        result = TBObservation.from_dict(thaw_json(tape.take("tb_settled").payload)["result"])
        if admission is not None:
            admission.quarantine(result.cleanup)
    return result
