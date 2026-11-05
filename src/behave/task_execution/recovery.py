"""Cold admission checks, independent of result/score and replay semantics.

Callers own the run before reading. These are local lifetime-owner witnesses,
not remote-worker fencing. Preserve the complete run directory for recovery;
an offline report alone cannot establish that uncommitted services have exited.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from behave.agent.journal import Event
from behave.agent.process_worker import worker_receipt
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, fields, thaw_json

from .judgment import ComponentJudgment
from .run_store import RunStore
from .tb_report import TBObservation


@dataclass(frozen=True, slots=True)
class PhaseCleanup:
    phase: str
    head: BlobRef | None
    state: CleanupState


def _dispatch_cleanup(events: tuple[Event, ...], *, namespace: str, terminal: str,
                      passive: frozenset[str], outcome: Callable[[object], CleanupState]) -> CleanupState:
    """Fold serial requests/settlements; a result or progress event never closes a request."""
    if not events:
        return CleanupState.COMPLETE
    if events[0].kind != namespace + "_started":
        raise RecordError("recovery-history-invalid", "missing service initialization")
    cleanup, pending, decided, finished = CleanupState.COMPLETE, False, False, False
    for event in events[1:]:
        kind = event.kind
        if finished:
            raise RecordError("recovery-history-invalid", "events after service termination")
        if kind == namespace + "_requested" and not pending:
            pending, decided = True, False
        elif kind == namespace + "_settled" and pending:
            row = fields(thaw_json(event.payload), {"result"})
            cleanup = CleanupState.combine(cleanup, outcome(row["result"]))
            pending = False
        elif kind == "judge_score_decided" and namespace == "judge" and pending and not decided:
            # Concrete scoring can finish before optional analysis or cleanup.
            # In particular a score's default COMPLETE is not a lifetime witness.
            decided = True
        elif kind == "component_retained" and namespace == "judge" and not pending:
            # A targeted rejudgment did not launch this component again. Keep
            # its recorded cleanup state; retaining a score cannot improve it.
            row = fields(thaw_json(event.payload), {"component", "result"})
            cleanup = CleanupState.combine(cleanup, outcome(row["result"]))
        elif kind == terminal and not pending:
            finished = True
        elif kind in passive and not pending:
            pass  # Missing artifacts and pre-dispatch declines launched no service.
        else:
            raise RecordError("recovery-history-invalid", "invalid service request/settlement ordering")
    return CleanupState.combine(cleanup, CleanupState.UNKNOWN if pending else CleanupState.COMPLETE)


def task_cleanup(run: RunStore) -> tuple[PhaseCleanup, ...]:
    """Inspect the solver owner, judge and TB, including unfinished phase prefixes.

    The solver settlement covers its worker AND model/development relays. The
    role's terminal, provider billing and task result cannot replace that receipt.
    Reads verify journal chains and dependencies but do not resample or settle work.
    """
    supervision = run.journal("supervision").load()
    if not supervision:
        # Orphan child journals are not evidence that no worker ever started.
        orphaned = any(run.journal(p).load() for p in ("solver", "provider", "development"))
        solver = CleanupState.UNKNOWN if orphaned else CleanupState.COMPLETE
    elif len(supervision) == 1 and supervision[0].kind == "solver_requested":
        solver = CleanupState.UNKNOWN
    else:
        solver = CleanupState(worker_receipt(supervision, namespace="solver")["cleanup"])

    def receipt(phase, events, state):
        return PhaseCleanup(phase, BlobRef.of(events[-1].to_bytes()) if events else None, state)

    judge, tb = (run.journal(p).load() for p in ("judge", "testbench"))
    return (
        receipt("supervision", supervision, solver),
        receipt("judge", judge, _dispatch_cleanup(
            judge, namespace="judge", terminal="judgment_finished",
            passive=frozenset({"component_missing", "component_unavailable"}),
            outcome=lambda value: ComponentJudgment.from_dict(value).cleanup)),
        receipt("testbench", tb, _dispatch_cleanup(
            tb, namespace="tb", terminal="tb_finished", passive=frozenset({"tb_unavailable"}),
            outcome=lambda value: TBObservation.from_dict(value).cleanup)),
    )


def require_task_quiescent(run: RunStore, *, run_key: str) -> None:
    """Suspend admission, not scores, until all old service owners are quiescent."""
    for phase in task_cleanup(run):
        if not phase.state.quiescent:
            head = "absent" if phase.head is None else phase.head.sha256
            raise RecordError("batch-recovery-cleanup-" + phase.state.value,
                              f"candidate {run_key}, phase {phase.phase}, head {head}: "
                              "old execution requires external fencing/reconciliation")
