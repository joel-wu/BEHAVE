"""Shared lifetime owner for fixed, trusted role workers.

The application selects the worker entrypoint and ingress factories; neither is
model-controlled. Candidate programs still execute through isolated tools. The
worker commits artifacts, while this owner attests cleanup before consuming them.
Callers own all journals exclusively. Unknown dispatches require reconciliation,
not relaunch; this is not a distributed lease.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Self

from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, fields, metadata, parse_json, thaw_json, version

from .admission import ResourcePool
from .attempt import AttemptInterrupted, AttemptResult, AttemptSpec
from .journal import AttemptJournal
from .process_model import ModelCommand
from .replay import Tape


class WorkerIngress(Protocol):
    pool: ResourcePool
    endpoint: str

    @property
    def cleanup_state(self) -> CleanupState: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, exc_type, exc_value, traceback) -> object: ...


IngressFactory = Callable[[float | None, Callable[[], bool]], WorkerIngress]

WORKER_LAUNCH_FIELDS = frozenset({
    "job", "journal", "objects", "model_admission", "provider",
    "development_admission", "development",
})


@dataclass(frozen=True)
class WorkerChannels:
    """The fixed worker ABI's journal/admission bindings, not role capabilities.

    Applications still authorize tools, select input closures and choose network
    policy. Construction validates channels before either inputs or work commit.
    """

    journal: AttemptJournal
    supervision: AttemptJournal
    model_pool: ResourcePool | None = None
    provider: AttemptJournal | None = None
    development_pool: ResourcePool | None = None
    development: AttemptJournal | None = None

    def __post_init__(self) -> None:
        channels = [self.journal, self.supervision]
        for pool, ledger in ((self.model_pool, self.provider), (self.development_pool, self.development)):
            if (pool is None) != (ledger is None):
                raise ValueError("shared admission requires its separate execution ledger")
            if pool is not None:
                if type(pool) is not ResourcePool:
                    raise TypeError("admission requires ResourcePool")
                channels.append(ledger)
        if (len({j.directory for j in channels}) != len(channels)
                or any(j.objects.directory != self.journal.objects.directory for j in channels)):
            raise ValueError("worker journals must be distinct and use one object store")

    def payload(self, reference: BlobRef) -> dict:
        return {"job": reference.as_dict(), "journal": str(self.journal.directory),
                "objects": str(self.journal.objects.directory),
                "model_admission": None if self.model_pool is None else self.model_pool.policy.identity,
                "provider": None if self.provider is None else str(self.provider.directory),
                "development_admission": None if self.development_pool is None else self.development_pool.policy.identity,
                "development": None if self.development is None else str(self.development.directory)}

    def model_ingress(self, spec: AttemptSpec, command: ModelCommand) -> IngressFactory | None:
        if self.model_pool is None:
            return None

        def enter(deadline: float | None, cancelled: Callable[[], bool]) -> WorkerIngress:
            from .model_relay import ModelRelay

            return ModelRelay(spec=spec, command=command, journal=self.provider, pool=self.model_pool,
                              deadline=deadline, cancelled=cancelled)

        return enter


def worker_receipt(events, *, namespace: str) -> dict:
    """Read the lifetime owner's complete handshake, never infer death from a role terminal."""
    from behave.isolation.service import service_execution_status

    if len(events) != 2 or [event.kind for event in events] != [namespace + "_requested", namespace + "_settled"]:
        raise AttemptInterrupted(namespace + "-outcome-unknown")
    raw = thaw_json(events[-1].payload)
    legacy = raw.get("schema") == f"behave.{namespace}-settlement/1"
    receipt = fields(raw, {
        "schema", "cleanup", "status", "returncode", "elapsed_seconds", "interruption", "supervision_errors"}
        | (set() if legacy else {"execution_status"}))
    version(receipt["schema"], f"behave.{namespace}-settlement/{1 if legacy else 2}")
    if not legacy:
        service_execution_status(receipt["status"], receipt["execution_status"])
    return receipt


def supervise_worker(*, namespace: str, module: str, attempt: AttemptSpec, model: ModelCommand,
                     journal: AttemptJournal, reference: BlobRef, log_bytes: int,
                     ingresses: tuple[IngressFactory | None, ...],
                     cancelled: Callable[[], bool] | None = None,
                     worker_network: str | None = None,
                     runtime_arguments: tuple[str, ...] = ()) -> tuple[dict, tuple[BlobRef, ...]]:
    """Settle all owners independently, including partially entered relays.

    Ingress slot zero is the model relay. If present, the worker has no host network;
    otherwise the pinned model command's network policy applies. Other slots are
    application-defined local relays. A trusted application may explicitly admit
    host networking for a fixed acquisition worker; this is never a model tool
    argument. Slot order is part of the fixed worker ABI.
    """
    from behave.isolation.runtime_environment import runtime_environment
    from behave.isolation.scratch import ScratchDirectory, retention_record
    from behave.isolation.service import run_service

    if not namespace.isidentifier() or not module.startswith("behave.") or not all(
            part.isidentifier() for part in module.split(".")) or not ingresses:
        raise ValueError("invalid trusted worker entrypoint or ingress slots")
    if worker_network not in {None, "host", "none"}:
        raise ValueError("invalid trusted worker network policy")
    began = time.monotonic()
    is_cancelled = cancelled or (lambda: False)
    relays, errors = [], []
    got = None
    worker_cleanup = CleanupState.COMPLETE  # No worker launched yet.
    scratch = None

    def owner_cleanup():
        return CleanupState.combine(worker_cleanup, *(relay.cleanup_state for relay in relays))

    def close(relay):
        try:
            relay.__exit__(None, None, None)
        except Exception as error:  # noqa: BLE001 -- close every independently owned relay
            errors.append(error)
        finally:
            relay.pool.quarantine(relay.cleanup_state)

    def enter(stack, factory):
        if factory is None:
            return ""
        relay = factory(None, is_cancelled)
        relays.append(relay)  # A failed __enter__ also owes a cleanup witness.
        try:
            result = relay.__enter__()
        except BaseException:
            relay.pool.quarantine(relay.cleanup_state)
            raise
        stack.callback(close, relay)
        return result.endpoint

    try:
        # Close every relay before deciding whether their shared workspace can go.
        with ScratchDirectory(prefix=f"behave-{namespace}-", cleanup=owner_cleanup) as scratch, ExitStack() as stack:
            endpoints = tuple(enter(stack, factory) for factory in ingresses)
            bootstrap = ("import sys; sys.path.insert(0,sys.argv.pop(1)); "
                         f"from {module} import worker_main; worker_main()")
            argv = (sys.executable, "-I", "-B", "-c", bootstrap, str(Path(__file__).resolve().parents[2]),
                    str(journal.directory), str(journal.objects.directory), reference.sha256,
                    str(reference.size_bytes), *endpoints, *runtime_arguments)
            from behave.core.paths import tool_resource_environment

            environment = {"PATH": os.defpath, "LANG": "C.UTF-8",
                           "TMPDIR": str(scratch.parent), "BEHAVE_SCRATCH_DIR": str(scratch.parent),
                           "BEHAVE_RUNTIME_DIR": str(runtime_environment().directory),
                           "BEHAVE_WORK_DIR": str(scratch / "sim-work")}
            environment.update(tool_resource_environment())
            network = worker_network or (model.network if not endpoints[0] else "none")
            # Preparation has not dispatched a worker. Only an actual launch
            # needs a returned lifetime witness before its scratch can go.
            worker_cleanup = CleanupState.UNKNOWN
            got = run_service(argv, cwd=scratch, environment=environment,
                              deadline=None, output_bytes=log_bytes,
                              network=network, cancelled=cancelled)
            worker_cleanup = got.cleanup_state
    except Exception as error:  # noqa: BLE001 -- a failure is not a cleanup witness
        errors.insert(0, error)

    cleanup = owner_cleanup()
    codes = [error.code if isinstance(error, (RecordError, AttemptInterrupted))
             else namespace + "-supervision-" + type(error).__name__ for error in errors]
    interruption = None
    if got is not None and got.status == "exited" and got.returncode == 2:
        try:
            interruption = fields(parse_json(got.stdout), {"interruption"})["interruption"]
            if type(interruption) is not str or not interruption:
                interruption = None
        except (TypeError, ValueError):
            pass
    receipt = {"schema": f"behave.{namespace}-settlement/2", "cleanup": cleanup.value,
               "status": "supervisor-failed" if got is None else got.status,
               "execution_status": None if got is None else got.execution_status,
               "returncode": None if got is None else got.returncode,
               "elapsed_seconds": time.monotonic() - began, "interruption": interruption,
               "supervision_errors": codes}
    # Exception messages may contain secrets. Only typed codes and bounded
    # fixed-worker streams are retained. Storage failure leaves a dangling request.
    logs = () if got is None else (journal.objects.put(got.stdout), journal.objects.put(got.stderr))
    if scratch is not None and not cleanup.quiescent:
        logs += (journal.objects.put(retention_record(scratch, cleanup)),)
    return receipt, logs


def completed_attempt(spec: AttemptSpec, journal: AttemptJournal, *, namespace: str) -> AttemptResult | None:
    # Consume the owned worker's terminal metadata, not a second full audit of
    # every workspace version and response payload it already published.
    events = journal.load(verify_artifacts=False)
    if not events or events[-1].kind != "terminal":
        return None
    row = fields(dict(events[-1].payload), {"reason", "turns", "workspace", "final_action"})
    if type(row["turns"]) is not int or not 0 <= row["turns"] <= spec.max_turns:
        raise RecordError(namespace + "-terminal-invalid", "invalid completed turn count")
    return AttemptResult(row["reason"], row["turns"], BlobRef.from_dict(dict(row["workspace"])),
                         None if row["final_action"] is None else metadata(row["final_action"]),
                         BlobRef.of(events[-1].to_bytes()))


def settled_attempt(*, namespace: str, spec: AttemptSpec, journal: AttemptJournal,
                    supervision: AttemptJournal, payload: dict, dependencies: tuple[BlobRef, ...],
                    launch: Callable[[], tuple[dict, tuple[BlobRef, ...]]]) -> AttemptResult:
    """Journal one launch and consume its terminal only after confirmed cleanup."""
    tape = Tape(supervision)
    if tape.begin(namespace + "_requested", payload, dependencies):
        if journal.load(verify_artifacts=False):
            tape.record(namespace + "_settled", {
                "status": "preexisting-attempt", "returncode": None, "elapsed_seconds": 0.,
                "interruption": None, "schema": f"behave.{namespace}-settlement/2",
                "execution_status": None,
                "cleanup": CleanupState.UNKNOWN.value, "supervision_errors": []})
            raise AttemptInterrupted(namespace + "-preexisting-attempt")
        receipt, logs = launch()
        tape.record(namespace + "_settled", receipt, logs)
    else:
        tape.take(namespace + "_settled")
    tape.finish()
    receipt = worker_receipt(tape.events, namespace=namespace)
    if receipt["status"] == "preexisting-attempt":
        raise AttemptInterrupted(namespace + "-preexisting-attempt")
    CleanupState(receipt["cleanup"]).require_quiescent(namespace)
    completed = completed_attempt(spec, journal, namespace=namespace)
    if completed is not None:
        return completed
    errors = receipt["supervision_errors"]
    code = receipt["interruption"] or (errors[0] if errors else namespace + "-worker-" + receipt["status"])
    raise AttemptInterrupted(code)
