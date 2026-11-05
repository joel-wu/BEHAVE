"""Shared artifact-development capabilities; no submission, golden, reward or judge.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from behave.agent.admission import ResourcePool
from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.journal import AttemptJournal, Event
from behave.agent.lifetime import AttemptControl, ExecutionWindow, current_window, execution_window
from behave.agent.local_rpc import LocalRPCServer, exchange
from behave.agent.prompting import render
from behave.agent.replay import Tape
from behave.agent.tools import Tool, ToolResult, ToolUnavailable, current_invocation, object_schema
from behave.agent.workspace import Workspace, WorkspaceSnapshot
from behave.core.artifacts import BlobRef, FileData, SourceArtifact, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, fields, parse_json, thaw_json, version
from behave.core.storage import DirectoryStore

from .cocotb_worker import BACKEND, CocotbBackend, CocotbJob, CocotbRun, run_cocotb
from .submission import ArtifactSelection
from .tb_report import TBObservation
from .testbench import TBPolicy

if TYPE_CHECKING:
    from behave.agent.attempt import AttemptSpec
    from behave.agent.process_worker import IngressFactory, WorkerChannels, WorkerIngress
    from behave.isolation.model import ResourceLimits
CocotbExecutor = Callable[[CocotbJob, DirectoryStore], CocotbRun]


def development_ingress(spec: AttemptSpec, policy: TBPolicy | None,
                        channels: WorkerChannels) -> IngressFactory | None:
    """Deferred relay factory for construction development tools."""
    if channels.development_pool is None:
        return None
    if policy is None:
        raise ValueError("development relay requires an admitted cocotb tool")

    def enter(deadline: float | None, cancelled: Callable[[], bool]) -> WorkerIngress:
        from .development import DevelopmentRelay

        return DevelopmentRelay(spec=spec, policy=policy, solver=channels.journal,
                                journal=channels.development, pool=channels.development_pool,
                                deadline=deadline, cancelled=cancelled)

    return enter


def worker_tools(*, python_limits: ResourceLimits | None, cocotb_policy: TBPolicy | None,
                 development_endpoint: str) -> tuple[Tool, ...]:
    """Realize the construction worker's configured development tools."""
    tools = []
    if python_limits is not None:
        from behave.agent.commands import python_commands

        tools.append(python_commands(python_limits).tool())
    if cocotb_policy is not None:
        from .development import CocotbRelayClient

        executor = None if not development_endpoint else CocotbRelayClient(development_endpoint)
        tools.append(cocotb_tool(cocotb_policy, executor=executor))
    return tuple(tools)


def job_from_snapshot(origin: BlobRef, arguments: dict, policy: TBPolicy, objects: DirectoryStore) -> CocotbJob:
    """One source-selection rule for local execution and coordinator validation."""
    row = fields(arguments, {"rtl", "testbench"})
    selections = tuple(ArtifactSelection.from_dict(row[name]) for name in ("rtl", "testbench"))
    snapshot = WorkspaceSnapshot.from_bytes(read_verified(objects, origin))
    paths = sorted({path for selection in selections for path in selection.files})
    by_path = {path: found for path in paths if (found := snapshot.file(path)) is not None}
    if any(path not in by_path for path in paths):
        raise RecordError("workspace-file-missing", "a selected cocotb dependency is absent")
    files = tuple(FileData(by_path[path], read_verified(objects, by_path[path].content)) for path in paths)
    sources = tuple(SourceArtifact(tuple(by_path[path] for path in selection.files),
                                   selection.entry_file, selection.entry_symbol) for selection in selections)
    return CocotbJob("development", origin, files, *sources, policy)


def execute_cocotb(job: CocotbJob, objects: DirectoryStore) -> CocotbRun:
    from behave.isolation.scratch import ScratchDirectory, retention_record

    cleanup = CleanupState.COMPLETE
    with ScratchDirectory(prefix="behave-cocotb-dev-", cleanup=lambda: cleanup) as scratch:
        cleanup = CleanupState.UNKNOWN
        result = run_cocotb(job, objects=objects, workdir=scratch)
        cleanup = result.observation.cleanup
        if not cleanup.quiescent:
            retained = objects.put(retention_record(scratch, cleanup))
            result = replace(result, observation=replace(result.observation,
                evidence=(*result.observation.evidence, retained)))
        return result


def cocotb_tool(policy: TBPolicy, *, executor: CocotbExecutor | None = None) -> Tool:
    """An explicit capability with pinned limits; importing it launches nothing.

    Each call snapshots its selected sources and executes once. The attempt
    journal owns replay/recovery. An optional coordinator executor adds shared
    admission and its own execution receipt, never a retry loop. A later edit
    creates a different execution input. Local workspace inputs are temporary;
    only checkpointed callers retain those source snapshots.
    """
    if type(policy) is not TBPolicy or (
        policy.backend, policy.collector, policy.metric
    ) != (BACKEND, CocotbBackend.collector, CocotbBackend.metric):
        raise ValueError("development cocotb requires the supported backend and collector policy")
    if policy.max_retries != 0:
        raise ValueError("development calls execute once; set max_retries=0")
    if executor is not None and not callable(executor):
        raise TypeError("development executor must be callable")

    def execute(workspace: Workspace, arguments: dict) -> ToolResult:
        from behave.isolation.scratch import ScratchDirectory

        # Legacy development callers still use checkpointed workspaces. A live
        # workspace stages only selected sources locally for the existing runner,
        # never its build directory or intermediate source revisions in NFS.
        cleanup = CleanupState.COMPLETE
        staging = (ScratchDirectory(prefix="behave-dev-inputs-", parent=workspace.directory.parent,
                                    cleanup=lambda: cleanup)
                   if workspace.local else nullcontext(None))
        with staging as directory:
            source = workspace
            if directory is not None:
                source = Workspace(DirectoryStore(Path(directory) / "objects"))
                row = fields(arguments, {"rtl", "testbench"})
                selected = tuple(ArtifactSelection.from_dict(row[name]) for name in ("rtl", "testbench"))
                for path in sorted({path for selection in selected for path in selection.files}):
                    source.write(path, workspace.read(path))
            origin, closure = source.checkpoint()
            job = job_from_snapshot(origin, arguments, policy, source.store)
            inputs = source.store.put(job.to_bytes())
            cleanup = CleanupState.UNKNOWN
            result = (execute_cocotb if executor is None else executor)(job, source.store)
            cleanup = result.observation.cleanup
            if workspace.local:
                workspace.store.publish(source.store, result.observation.closure)
        observation = result.observation
        console = b"" if result.console is None else read_verified(workspace.store, result.console)
        content = {
            **({} if workspace.local else {"inputs": inputs.as_dict(), "workspace": origin.as_dict()}),
            "status": observation.status, "code": observation.code, "comparison": observation.comparison,
            "console": console.decode("utf-8", errors="replace"),
            "console_bytes": None if result.console is None else result.console.as_dict(),
            "coverage_assurance": "submitted-process-diagnostic",
            "coverage": None if observation.coverage is None else observation.coverage.as_dict(),
        }
        record = workspace.store.put(canonical_json(content))
        artifacts = tuple(dict.fromkeys((
            *(() if workspace.local else (*closure, inputs)), record, *observation.closure)))
        if observation.status == "unavailable":
            raise ToolUnavailable(observation.code, artifacts=artifacts)
        return ToolResult(content, artifacts)

    selection = object_schema({"files": {"type": "array", "items": {"type": "string"}},
                               "entry_file": {"type": "string"}, "entry_symbol": {"type": "string"}})
    return Tool(
        "run_cocotb", render("shared/tools/run_cocotb"),
        object_schema({"rtl": selection, "testbench": selection}), execute,
        f"solver-cocotb/2:{BlobRef.of(policy.to_bytes()).sha256}",
    )


SCHEMA = "behave.development-relay/1"


def unavailable(code: str, *, cleanup: CleanupState = CleanupState.COMPLETE) -> CocotbRun:
    return CocotbRun(TBObservation("unavailable", code, cleanup=cleanup))


class DevelopmentRelay(LocalRPCServer):
    def __init__(self, *, spec: AttemptSpec, policy: TBPolicy, solver: AttemptJournal,
                 journal: AttemptJournal, pool: ResourcePool, deadline: float | None, cancelled=lambda: False):
        if type(spec) is not AttemptSpec or type(pool) is not ResourcePool:
            raise TypeError("development relay requires an attempt and coordinator pool")
        cocotb_tool(policy)  # Same admitted backend, collector and no-retry rule.
        if solver.directory == journal.directory or solver.objects.directory != journal.objects.directory:
            raise ValueError("development and solver journals must be separate in the same store")
        super().__init__(name="development-relay", deadline=deadline, cancelled=cancelled)
        self.spec, self.policy, self.solver, self.pool = spec, policy, solver, pool
        self.objects, self.tape = journal.objects, Tape(journal)
        config = self.objects.put(canonical_json({"schema": SCHEMA, "attempt": spec.as_dict(),
                                                 "policy": parse_json(policy.to_bytes()),
                                                 "admission": pool.policy.as_dict()}))
        self.tape.record("development_initialized", {"configuration": config.as_dict()}, (config,))
        self.calls: dict[str, tuple[BlobRef, BlobRef | None]] = {}
        self.local_calls = set()
        active = set()
        for event in self.tape.events[self.tape.cursor:]:
            row = thaw_json(event.payload)
            key = row["invocation"]
            if event.kind == "development_queued" and key not in self.calls:
                self.calls[key] = (BlobRef.from_dict(row["request"]), None)
            elif event.kind == "development_requested" and key in self.calls and key not in active:
                if self.calls[key][1] is not None:
                    raise RecordError("development-event-invalid", "dispatch follows completion")
                active.add(key)
            elif event.kind in {"development_settled", "development_declined"} and key in self.calls:
                if self.calls[key][1] is not None or (key in active) != (event.kind == "development_settled"):
                    raise RecordError("development-event-invalid", "unbound or repeated development outcome")
                self.calls[key] = (self.calls[key][0], BlobRef.from_dict(row["result"]))
                result = CocotbRun.from_dict(parse_json(read_verified(self.objects, self.calls[key][1])))
                self.child_cleanup = CleanupState.combine(self.child_cleanup, result.observation.cleanup)
            else:
                raise RecordError("development-event-invalid", "unexpected development event")
        if any(key in active and result is None for key, (_, result) in self.calls.items()):
            self.child_cleanup = CleanupState.combine(self.child_cleanup, CleanupState.UNKNOWN)
        self.pool.quarantine(self.child_cleanup)
        self.tape.cursor = len(self.tape.events)

    def _result(self, request: dict, result: CocotbRun, *, dispatched: bool) -> BlobRef:
        ref = self.objects.put(canonical_json(result.as_dict()))
        key = request["invocation"]["sha256"]
        self.tape.record("development_settled" if dispatched else "development_declined",
                         {"invocation": key, "result": ref.as_dict()}, (ref, *result.observation.closure))
        self.calls[key] = (self.calls[key][0], ref)
        return ref

    def respond(self, data: bytes, *, cancelled) -> bytes:
        if "objects" in parse_json(data):
            return self._respond_local(data, cancelled=cancelled)
        row = fields(parse_json(data), {"schema", "invocation", "job"})
        version(row["schema"], SCHEMA)
        invocation, job_ref = BlobRef.from_dict(row["invocation"]), BlobRef.from_dict(row["job"])
        request = self.objects.put(canonical_json(row))
        key = invocation.sha256
        if key in self.calls:
            previous, result = self.calls[key]
            if previous != request:
                raise RecordError("development-request-conflict", "invocation names different inputs")
            if result is None:
                result = self.objects.put(canonical_json(unavailable(
                    "development-outcome-unknown", cleanup=self.child_cleanup).as_dict()))
            return canonical_json({"schema": SCHEMA, "request": request.as_dict(), "result": result.as_dict()})

        self.child_cleanup.require_quiescent(self.name)
        event = Event.from_bytes(read_verified(self.objects, invocation))
        events = self.solver.load()
        if not events or events[-1] != event or event.kind != "tool_requested":
            raise RecordError("development-invocation-unbound", "request is not the pending solver tool call")
        call = thaw_json(event.payload)["call"]
        if call["name"] != "run_cocotb" or call["argument_error"] is not None:
            raise RecordError("development-invocation-unbound", "request does not authorize cocotb")
        origin = BlobRef.from_dict(dict(event.payload["before"]))
        job = job_from_snapshot(origin, call["arguments"], self.policy, self.objects)
        if BlobRef.of(job.to_bytes()) != job_ref:
            raise RecordError("development-input-mismatch", "source selection or policy differs from the tool request")
        self.objects.put(job.to_bytes())
        self.tape.record("development_queued", {"invocation": key, "request": request.as_dict()},
                         (request, invocation, job_ref, *event.artifacts))
        self.calls[key] = (request, None)
        deadline = time.monotonic() + self.policy.worker_timeout_seconds
        if self.deadline is not None:
            deadline = min(self.deadline, deadline)
        window = ExecutionWindow(AttemptControl(cancelled=cancelled), time.time() + max(0, deadline - time.monotonic()),
                                 deadline)
        dispatched = False
        try:
            with self.pool.acquire(deadline=deadline, cancelled=cancelled), execution_window(window):
                if cancelled() or window.remaining() <= 0:
                    raise AttemptInterrupted("development-admission-cancelled")
                self.tape.record("development_requested", {"invocation": key})
                dispatched = True
                try:
                    self.child_cleanup = CleanupState.UNKNOWN
                    result = execute_cocotb(job, self.objects)
                    if type(result) is not CocotbRun:
                        raise TypeError("invalid development result")
                    self.child_cleanup = result.observation.cleanup
                except (AttemptInterrupted, RecordError):
                    raise  # A failed durable read/write is not a settled execution.
                except Exception:  # noqa: BLE001 -- executor failure is not a candidate failure
                    traceback.print_exc()  # Retained by the existing coordinator stderr log.
                    result = unavailable("development-service-failed", cleanup=self.child_cleanup)
                finally:
                    self.pool.quarantine(self.child_cleanup)
        except AttemptInterrupted as error:
            if dispatched:
                raise  # No completion receipt: do not settle or claim the child has exited.
            result = unavailable(error.code)
        result_ref = self._result(row, result, dispatched=dispatched)
        return canonical_json({"schema": SCHEMA, "request": request.as_dict(), "result": result_ref.as_dict()})

    def _respond_local(self, data: bytes, *, cancelled) -> bytes:
        """Same-host transient inputs. The solver journal owns the call history.

        This directory is supplied by the trusted worker, never tool arguments.
        A local rollout cannot be resumed after loss, so do not publish source
        snapshots or a second durable development ledger for these calls.
        """
        row = fields(parse_json(data), {"schema", "invocation", "job", "objects"})
        version(row["schema"], SCHEMA)
        invocation = BlobRef.from_dict(row["invocation"])
        events = self.solver.load()
        if (not events or events[-1].kind != "tool_requested"
                or BlobRef.of(events[-1].to_bytes()) != invocation
                or events[-1].payload["before"] is not None):
            raise RecordError("development-invocation-unbound", "request is not the pending local tool call")
        call = thaw_json(events[-1].payload)["call"]
        if call["name"] != "run_cocotb" or call["argument_error"] is not None:
            raise RecordError("development-invocation-unbound", "request does not authorize cocotb")
        if invocation in self.local_calls:
            raise AttemptInterrupted("development-outcome-unknown")
        self.local_calls.add(invocation)
        objects = DirectoryStore(row["objects"])
        job_ref = BlobRef.from_dict(row["job"])
        job = CocotbJob.from_bytes(read_verified(objects, job_ref), objects)
        expected = job_from_snapshot(job.origin, call["arguments"], self.policy, objects)
        if expected.to_bytes() != job.to_bytes():
            raise RecordError("development-input-mismatch", "source selection or policy differs from tool request")
        self.child_cleanup.require_quiescent(self.name)
        deadline = time.monotonic() + self.policy.worker_timeout_seconds
        if self.deadline is not None:
            deadline = min(self.deadline, deadline)
        window = ExecutionWindow(AttemptControl(cancelled=cancelled),
                                 time.time() + max(0, deadline - time.monotonic()), deadline)
        with self.pool.acquire(deadline=deadline, cancelled=cancelled), execution_window(window):
            self.child_cleanup = CleanupState.UNKNOWN
            try:
                result = execute_cocotb(job, objects)
                self.child_cleanup = result.observation.cleanup
            finally:
                self.pool.quarantine(self.child_cleanup)
        ref = objects.put(canonical_json(result.as_dict()))
        return canonical_json({"schema": SCHEMA, "request": BlobRef.of(data).as_dict(), "result": ref.as_dict()})


class CocotbRelayClient:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def __call__(self, job: CocotbJob, objects: DirectoryStore) -> CocotbRun:
        event, window = current_invocation(), current_window()
        if event is None or window is None:
            raise ToolUnavailable("development-invocation-missing")
        packet = canonical_json({"schema": SCHEMA, "invocation": objects.put(event.to_bytes()).as_dict(),
                                 "job": objects.put(job.to_bytes()).as_dict(),
                                 **({"objects": str(objects.directory)} if event.payload["before"] is None else {})})
        deadline = time.monotonic() + min(job.policy.worker_timeout_seconds, window.remaining())
        try:
            reply = fields(parse_json(exchange(self.endpoint, packet, deadline=deadline, cancelled=window.control.cancelled)),
                           {"schema", "request", "result"})
            version(reply["schema"], SCHEMA)
            if BlobRef.from_dict(reply["request"]) != BlobRef.of(packet):
                raise ValueError("development response belongs to another request")
            result = CocotbRun.from_dict(parse_json(read_verified(objects, BlobRef.from_dict(reply["result"]))))
            # run_cocotb already validates the evidence and console budget.
            # This same-store relay transfers references, not another copy of the files.
            return result
        except (OSError, ValueError) as error:
            raise ToolUnavailable("development-relay-" + type(error).__name__) from None
