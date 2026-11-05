"""Replayable concrete checks for construction drafts, not task admission.

A check compares one candidate with a fixed reference under fixed policies.
It neither approves task intent nor declares which of two disagreeing models
implements the spec. No solver reward, auxiliary RTL or bounded analysis is
required. The future synthesis supervisor owns retries and release decisions.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, replace

from behave.agent.admission import ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import AttemptJournal, load_chain
from behave.agent.replay import Tape
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, fields, thaw_json, version
from behave.task_execution.comparison import COMPARISON_BACKEND, ComparisonInputs, compare_sources
from behave.task_execution.judgment import ComponentJudgment


@dataclass(frozen=True, slots=True)
class ConcreteCheck:
    inputs: BlobRef
    result: ComponentJudgment
    event: BlobRef

    def __post_init__(self) -> None:
        if type(self.inputs) is not BlobRef or type(self.event) is not BlobRef:
            raise TypeError("a concrete check requires immutable input/event references")
        if type(self.result) is not ComponentJudgment or self.result.analysis is not None:
            raise TypeError("a concrete check contains only the concrete result")

    @property
    def outcome(self) -> str:
        if self.result.score is None:
            return "unavailable"
        return "passed" if self.result.score == 1 else "failed"

    def as_dict(self) -> dict:
        return {"schema": "behave.concrete-check/1", "inputs": self.inputs.as_dict(),
                "result": self.result.as_dict(), "event": self.event.as_dict()}

    @classmethod
    def from_dict(cls, value: object) -> ConcreteCheck:
        row = fields(value, {"schema", "inputs", "result", "event"})
        version(row["schema"], "behave.concrete-check/1")
        return cls(BlobRef.from_dict(row["inputs"]), ComponentJudgment.from_dict(row["result"]),
                   BlobRef.from_dict(row["event"]))


def load_check(reader: ContentReader, head: BlobRef) -> ConcreteCheck:
    """Read a completed sealed check, binding its summary to its settled outcome."""
    events = load_chain(reader, head)
    kinds = tuple(e.kind for e in events)
    if kinds not in (("check_started", "check_requested", "check_settled", "check_finished"),
                     ("check_started", "check_declined", "check_finished")):
        raise RecordError("check-evidence-invalid", "check history is not a completed invocation")
    start = fields(thaw_json(events[0].payload), {"inputs", "backend", "admission"})
    inputs = BlobRef.from_dict(start["inputs"])
    if kinds[1] == "check_requested":
        request = fields(thaw_json(events[1].payload), {"inputs"})
        if request["inputs"] != inputs.as_dict():
            raise RecordError("check-evidence-invalid", "request differs from the fixed check inputs")
    settled = fields(thaw_json(events[-2].payload), {"result"})
    finished = fields(thaw_json(events[-1].payload), {"result"})
    if settled != finished:
        raise RecordError("check-evidence-invalid", "check summary differs from its settled result")
    return ConcreteCheck(inputs, ComponentJudgment.from_dict(settled["result"]), head)


def check_cleanup(journal: AttemptJournal) -> CleanupState:
    """Recover the service witness, including a request without a settlement.

    A fresh coordinator must quarantine its pool too: an empty result list is
    not evidence that the old service exited. This does not fence that service.
    """
    cleanup = CleanupState.COMPLETE
    for event in journal.load():
        if event.kind == "check_requested":
            cleanup = CleanupState.UNKNOWN
        elif event.kind in {"check_settled", "check_declined"}:
            cleanup = ComponentJudgment.from_dict(thaw_json(event.payload)["result"]).cleanup
    return cleanup


def check_sources(inputs: ComparisonInputs, *, journal: AttemptJournal,
                  admission: ResourcePool | None = None,
                  cancelled: Callable[[], bool] = lambda: False,
                  cpu_pool: dict | None = None) -> ConcreteCheck:
    """Commit/replay one check; never retry an unknown or completed invocation.

    The trusted caller owns this journal exclusively, as for existing attempts.
    A later draft or retry needs a new check journal. Input identity includes all
    submitted source bytes and policies; editing a workspace cannot rebind it.
    """
    from behave.isolation.scratch import ScratchDirectory, retention_record

    if type(inputs) is not ComparisonInputs or inputs.policy.analysis is not None:
        raise ValueError("synthesis checks require a concrete-only comparison policy")
    if not callable(cancelled) or (admission is not None and type(admission) is not ResourcePool):
        raise TypeError("invalid comparison execution control")
    identity, closure = inputs.stage(journal.objects)
    tape = Tape(journal)
    tape.record("check_started", {"inputs": identity.as_dict(), "backend": COMPARISON_BACKEND,
                                  "admission": None if admission is None else admission.policy.identity}, closure)
    if admission is not None:
        admission.quarantine(check_cleanup(journal))
    if tape.cursor < len(tape.events):
        if tape.events[tape.cursor].kind == "check_declined":
            result = ComponentJudgment.from_dict(thaw_json(tape.take("check_declined").payload)["result"])
        else:
            tape.record("check_requested", {"inputs": identity.as_dict()})
            result = ComponentJudgment.from_dict(thaw_json(tape.take("check_settled").payload)["result"])
        if admission is not None:
            admission.quarantine(result.cleanup)
    else:
        dispatched = False
        permit = nullcontext() if admission is None else admission.acquire(cancelled=cancelled)
        try:
            with permit:
                if cancelled():
                    raise AttemptInterrupted("comparison-cancelled")
                cleanup = CleanupState.COMPLETE
                with ScratchDirectory(prefix="behave-comparison-", cleanup=lambda: cleanup) as scratch:
                    tape.record("check_requested", {"inputs": identity.as_dict()})
                    dispatched = True
                    cleanup = CleanupState.UNKNOWN
                    try:
                        result = compare_sources(inputs, objects=journal.objects, workdir=scratch,
                                                 cancelled=cancelled,
                                                 **({"cpu_pool": cpu_pool} if cpu_pool is not None else {}))
                        if type(result) is not ComponentJudgment or result.analysis is not None:
                            raise TypeError("comparison returned an invalid concrete result")
                        cleanup = result.cleanup
                    finally:
                        if admission is not None:
                            admission.quarantine(cleanup)
                    for ref in result.closure:
                        read_verified(journal.objects, ref)
                    if not cleanup.quiescent:
                        retained = journal.objects.put(retention_record(scratch, cleanup))
                        result = replace(result, evidence=(*result.evidence, retained))
                    tape.record("check_settled", {"result": result.as_dict()}, result.closure)
        except AttemptInterrupted as error:
            if dispatched:
                raise  # A dangling request still requires fencing, not a new check.
            result = ComponentJudgment(None, error.code, "infrastructure")
            tape.record("check_declined", {"result": result.as_dict()})
    terminal = tape.record("check_finished", {"result": result.as_dict()}, result.closure)
    tape.finish()
    return ConcreteCheck(identity, result, BlobRef.of(terminal.to_bytes()))
