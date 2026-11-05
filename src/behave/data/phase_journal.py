"""Construction phase journals and receipts, without role dispatch.

The caller holds the coordinator's existing owner. Unknown process cleanup
prevents publication; a role's final action alone does not prove quiescence.
Event schemas, journal paths and artifact ordering are shared with old runs.
"""

from behave.agent.journal import AttemptJournal
from behave.agent.process_worker import worker_receipt
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError

from .synthesis_records import PhaseReceipt, ProcessReceipt

AUXILIARY = ("supervision", "provider", "development")


def auxiliary_path(revision: int, role: str, channel: str) -> tuple[str, ...]:
    return ("revisions", f"r{revision}", role + "_" + channel)


def seal_process(run: JournalDirectory, revision: int, role: str) -> tuple[ProcessReceipt, tuple[BlobRef, ...]]:
    # Check before touching any potentially still-live role or relay journal.
    events = run.journal(*auxiliary_path(revision, role, "supervision")).load()
    receipt = worker_receipt(events, namespace="construction")
    CleanupState(receipt["cleanup"]).require_quiescent("construction")
    heads, refs = {}, []
    for channel in AUXILIARY:
        head, closure = run.seal(*auxiliary_path(revision, role, channel))
        heads[channel] = head
        refs.extend(closure)
    return ProcessReceipt(**heads), tuple(dict.fromkeys(refs))


def seal_phase(run: JournalDirectory, revision: int, role: str, *,
               process_backed: bool) -> tuple[PhaseReceipt, tuple[BlobRef, ...]]:
    process, auxiliary = seal_process(run, revision, role) if process_backed else (None, ())
    head, closure = run.seal("revisions", f"r{revision}", role)
    return PhaseReceipt(revision, role, head, process), (*closure, *auxiliary)


def verify_process_seal(run: JournalDirectory, phase: PhaseReceipt) -> None:
    for channel in AUXILIARY:
        events = run.journal(*auxiliary_path(phase.revision, phase.role, channel)).load()
        head = BlobRef.of(events[-1].to_bytes()) if events else None
        if head != getattr(phase.process, channel):
            raise RecordError("synthesis-phase-mismatch", "auxiliary journal changed after termination")
        if channel == "supervision":
            receipt = worker_receipt(events, namespace="construction")
            CleanupState(receipt["cleanup"]).require_quiescent("construction")


class RevisionPhases:
    """Pair revision requests/seals under the coordinator's existing owner.

    Replay still belongs to Tape and each role's worker. An active phase cannot
    be replaced or cleared until all of its process owners have settled.
    """

    def __init__(self, run: JournalDirectory, tape: Tape, *, process_roles: frozenset[str]):
        self.run, self.tape, self.process_roles = run, tape, process_roles
        self.active: tuple[int, str] | None = None
        self.completed: list[PhaseReceipt] = []

    def begin(self, revision: int, role: str, inputs: dict) -> AttemptJournal:
        if self.active is not None:
            raise RuntimeError("cannot replace an unsettled synthesis phase")
        self.active = (revision, role)
        self.tape.record("phase_requested", {"revision": revision, "role": role, "inputs": inputs})
        return self.run.journal("revisions", f"r{revision}", role)

    def settle(self, output: dict | None = None, refs: tuple[BlobRef, ...] = ()) -> None:
        if self.active is None:
            raise RuntimeError("no synthesis phase to settle")
        revision, role = self.active
        receipt, closure = seal_phase(self.run, revision, role, process_backed=role in self.process_roles)
        self.completed.append(receipt)
        self.tape.record("phase_settled", {"phase": receipt.as_dict(), "output": output}, (*closure, *refs))
        self.active = None
