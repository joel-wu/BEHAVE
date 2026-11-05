"""Locally owned trainer update ledger; no optimizer or implicit redispatch.

Only a fresh begin returns permission to dispatch. A pending update after a
crash needs backend reconciliation and fencing, not begin again. Once recovered
externally, its exact state can be committed with the pending request reference.
NFS/advisory ownership is not cross-node fencing of a live trainer.
"""

from dataclasses import dataclass, field, replace
from hashlib import sha256
from types import MappingProxyType
from typing import BinaryIO

from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import Event, load_chain
from behave.agent.run_store import JournalDirectory
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, ContentReader, read_verified, relative_paths
from behave.core.records import (
    RecordError,
    array,
    canonical_json,
    fields,
    metadata,
    parse_json,
    text,
    thaw_json,
    version,
)
from behave.core.storage import DirectoryStore

from .learning_report import load_training_report
from .plan import TrainingManifest
from .training_data import TokenVerifier
from .update_records import TrainerContract, UpdateRequest


@dataclass(frozen=True, slots=True)
class StateFile:
    component: str
    path: str
    content: BlobRef
    chunks: tuple[BlobRef, ...]

    def __post_init__(self):
        text(self.component, "trainer state component")
        relative_paths((self.path,))
        if type(self.content) is not BlobRef or type(self.chunks) is not tuple:
            raise TypeError("state files require content and a tuple of chunk references")
        if any(type(ref) is not BlobRef or ref.size_bytes <= 0 for ref in self.chunks):
            raise RecordError("checkpoint-chunks-invalid", "chunks must be nonempty blob references")
        if sum(ref.size_bytes for ref in self.chunks) != self.content.size_bytes:
            raise RecordError("checkpoint-size-mismatch", "chunk sizes must sum to the file size")

    def as_dict(self):
        return {
            "component": self.component,
            "path": self.path,
            "content": self.content.as_dict(),
            "chunks": [ref.as_dict() for ref in self.chunks],
        }

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"component", "path", "content", "chunks"})
        return cls(
            row["component"],
            row["path"],
            BlobRef.from_dict(row["content"]),
            tuple(BlobRef.from_dict(ref) for ref in array(row["chunks"])),
        )


@dataclass(frozen=True, slots=True)
class TrainingState:
    policy: BlobRef
    files: tuple[StateFile, ...]
    cursor: MappingProxyType

    def __post_init__(self):
        if type(self.policy) is not BlobRef or type(self.files) is not tuple:
            raise TypeError("training state requires policy identity and a tuple of state files")
        if not self.files or any(type(file) is not StateFile for file in self.files):
            raise RecordError("checkpoint-files-invalid", "expected nonempty state files")
        relative_paths(tuple(file.path for file in self.files))
        object.__setattr__(self, "files", tuple(sorted(self.files, key=lambda file: file.path)))
        object.__setattr__(self, "cursor", metadata(self.cursor))

    def to_bytes(self):
        data = canonical_json(
            {
                "schema": "behave.training-state/1",
                "policy": self.policy.as_dict(),
                "files": [file.as_dict() for file in self.files],
                "cursor": thaw_json(self.cursor),
            }
        )
        return data

    @classmethod
    def from_bytes(cls, data):
        row = fields(parse_json(data), {"schema", "policy", "files", "cursor"})
        version(row["schema"], "behave.training-state/1")
        result = cls(
            BlobRef.from_dict(row["policy"]), tuple(StateFile.from_dict(f) for f in array(row["files"])), row["cursor"]
        )
        if result.to_bytes() != data:
            raise RecordError("record-not-canonical", "training state must be canonical")
        return result

    @property
    def references(self):
        return tuple(dict.fromkeys(ref for file in self.files for ref in file.chunks))


def store_state_file(
    source: BinaryIO,
    objects: DirectoryStore,
    *,
    component: str,
    path: str,
    chunk_bytes: int = defaults.CHECKPOINT_CHUNK_BYTES,
) -> StateFile:
    """Read a caller-owned binary stream, without loading an entire weight file."""
    relative_paths((path,))
    text(component, "trainer state component")
    if type(chunk_bytes) is not int or chunk_bytes < 1:
        raise ValueError("chunk size must be positive")
    digest, size, refs = sha256(), 0, []
    while True:
        data = source.read(chunk_bytes)
        if type(data) is not bytes:
            raise TypeError("state input must be a binary stream")
        if not data:
            break
        size += len(data)
        digest.update(data)
        refs.append(objects.put(data))
    return StateFile(component, path, BlobRef(digest.hexdigest(), size), tuple(refs))


def copy_state_file(reader: ContentReader, file: StateFile, destination: BinaryIO | None = None) -> None:
    """Verify ordered chunks and the whole-file hash; optionally stream a copy."""
    digest = sha256()
    for ref in file.chunks:
        data = read_verified(reader, ref)
        digest.update(data)
        if destination is not None and destination.write(data) != len(data):
            raise OSError("short checkpoint write")
    if digest.hexdigest() != file.content.sha256:
        raise RecordError("checkpoint-file-mismatch", "ordered chunks do not match the complete file")


def verify_state(reader: ContentReader, state: TrainingState, components: tuple[str, ...]) -> None:
    if {file.component for file in state.files} != set(components):
        raise RecordError("checkpoint-components-mismatch", "state must contain exactly the declared components")
    for file in state.files:
        copy_state_file(reader, file)


def record(reader: ContentReader, ref: BlobRef) -> bytes:
    return read_verified(reader, ref)


@dataclass(frozen=True, slots=True)
class PendingUpdate:
    reference: BlobRef
    request: UpdateRequest
    examples: tuple[MappingProxyType, ...]
    invocation: BlobRef | None = None
    receipt: BlobRef | None = None

    @property
    def exposures(self):
        # Key by the actual result, not by a report's label or grouping.
        return frozenset((row["result"]["sha256"], row["turn"], row["exposure"]) for row in self.examples)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    reference: BlobRef  # Committed event, not just the state file manifest.
    state_ref: BlobRef
    state: TrainingState


@dataclass
class Evidence:
    """One verifier/contract's cache; no process or filesystem ownership."""

    contract: TrainerContract
    verifier: TokenVerifier | None
    references: set[BlobRef] = field(default_factory=set)
    reports: dict = field(default_factory=dict)
    files: set[StateFile] = field(default_factory=set)
    aborted: dict[str, BlobRef] = field(default_factory=dict)
    checkpoint: Checkpoint | None = None
    pending: PendingUpdate | None = None
    completed: dict[str, tuple[BlobRef, Checkpoint]] = field(default_factory=dict)
    consumed: set[tuple[str, int, str]] = field(default_factory=set)

    def dispatch(self, reader: ContentReader, ref: BlobRef) -> None:
        from .trainer import verify_invocation

        if self.pending is None or self.pending.invocation is not None:
            raise RecordError("trainer-already-dispatched", "dispatch requires a fresh pending update")
        _, refs = verify_invocation(reader, ref, self.contract, self.pending, self.checkpoint)
        self.references.update(refs)
        self.pending = replace(self.pending, invocation=ref)

    def returned(self, reader: ContentReader, ref: BlobRef) -> None:
        from .trainer import verify_receipt

        if self.pending is None or self.pending.invocation is None or self.pending.receipt is not None:
            raise RecordError("trainer-return-unexpected", "receipt requires one unresolved dispatch")
        _, refs = verify_receipt(reader, ref, self.pending.invocation)
        self.references.update(refs)
        self.pending = replace(self.pending, receipt=ref)

    def check_returned_state(self, reader: ContentReader, state: TrainingState) -> None:
        from .trainer import TrainerProcess, response_state, verify_receipt

        if self.pending is None or self.pending.invocation is None:
            return  # The original external-fencing/manual-commit API.
        if self.pending.receipt is None:
            raise RecordError("trainer-return-missing", "process-managed update has no execution receipt")
        result, _ = verify_receipt(reader, self.pending.receipt, self.pending.invocation, require_state=True)
        process = TrainerProcess.from_dict(parse_json(record(reader, self.pending.invocation))["process"])
        if response_state(result, self.pending.reference, process) != state:
            raise RecordError("trainer-state-mismatch", "checkpoint differs from the trainer's recorded response")

    def state(self, reader: ContentReader, ref: BlobRef) -> TrainingState:
        state = TrainingState.from_bytes(record(reader, ref))
        if {file.component for file in state.files} != set(self.contract.components):
            raise RecordError("checkpoint-components-mismatch", "state must contain exactly the declared components")
        for file in state.files:
            if file not in self.files:
                copy_state_file(reader, file)
                self.files.add(file)
        self.references.update((ref, *state.references))
        return state

    def prepare(self, reader: ContentReader, request: UpdateRequest) -> PendingUpdate:
        if self.checkpoint is None or request.parent != self.checkpoint.reference:
            raise RecordError("training-update-parent-mismatch", "update must use the current committed checkpoint")
        record(reader, request.decision)
        self.references.add(request.decision)
        examples = []
        for use in request.uses:
            if use.report not in self.reports:
                report = load_training_report(
                    reader, use.report, approved_manifest=self.contract.manifest, verifier=self.verifier
                )
                events = load_chain(reader, use.report)
                self.references.update(
                    ref for event in events for ref in (*event.artifacts, BlobRef.of(event.to_bytes()))
                )
                self.reports[use.report] = report
            report = self.reports[use.report]
            if use.candidate >= len(report["candidates"]):
                raise RecordError("training-example-missing", "candidate does not exist in its training report")
            candidate = report["candidates"][use.candidate]
            rollout = candidate["examples"]
            if rollout is None:
                raise RecordError("training-examples-unavailable", "candidate has no recorded rollout examples")
            turn = next((row for row in rollout["turns"] if row["turn"] == use.turn), None)
            if turn is None or turn["status"] != "eligible":
                raise RecordError("training-example-ineligible", "selected turn is missing, excluded or unavailable")
            if "tokens" not in turn:
                response = BlobRef.from_dict(turn["response"])
                turn = {**turn, "tokens": parse_json(record(reader, response))["token_data"]}
                self.references.add(response)
            examples.append(
                metadata(
                    {
                        "report": use.report.as_dict(),
                        "candidate": use.candidate,
                        "task_id": candidate["task_id"],
                        "attempt_id": candidate["attempt_id"],
                        "group_id": candidate["group_id"],
                        "result": rollout["result"],
                        "reward": rollout["reward"],
                        "exposure": use.exposure,
                        **turn,
                    }
                )
            )
        pending = PendingUpdate(BlobRef.of(request.to_bytes()), request, tuple(examples))
        if len(pending.exposures) != len(examples) or pending.exposures & self.consumed:
            raise RecordError("training-exposure-duplicate", "this result/turn/exposure was already used")
        self.references.add(pending.reference)
        return pending


def check_abort(reader: ContentReader, pending: PendingUpdate | None, request: BlobRef) -> None:
    """Only a recorded, fully cleaned failure can discard an uncommitted update."""
    from behave.core.lifecycle import CleanupState

    from .trainer import read_record, verify_receipt

    if pending is None or pending.reference != request or pending.invocation is None or pending.receipt is None:
        raise RecordError("trainer-abort-unavailable", "abort requires a matching recorded execution outcome")
    result, _ = verify_receipt(reader, pending.receipt, pending.invocation)
    receipt = parse_json(read_record(reader, pending.receipt))
    if result.cleanup_state != CleanupState.COMPLETE or receipt["state"] is not None:
        raise RecordError("trainer-abort-unsafe", "reconcile a successful state; fence an uncertain process first")


def recover(
    reader: ContentReader,
    events: tuple[Event, ...],
    contract: TrainerContract,
    verifier: TokenVerifier | None,
) -> Evidence:
    evidence = Evidence(contract, verifier)
    if not events:
        return evidence
    start = fields(thaw_json(events[0].payload), {"contract", "state", "verifier"})
    contract_ref, state_ref = BlobRef.from_dict(start["contract"]), BlobRef.from_dict(start["state"])
    if events[0].kind != "trainer_started" or record(reader, contract_ref) != contract.to_bytes():
        raise RecordError("trainer-contract-mismatch", "resume requires the independently supplied trainer contract")
    if start["verifier"] != (None if verifier is None else verifier.identity):
        raise RecordError("training-verifier-mismatch", "resume requires the recorded token verifier")
    TrainingManifest.from_bytes(record(reader, contract.manifest))
    record(reader, contract.configuration)
    evidence.references.update((contract_ref, contract.configuration, contract.manifest))
    evidence.checkpoint = Checkpoint(BlobRef.of(events[0].to_bytes()), state_ref, evidence.state(reader, state_ref))
    expected = (contract_ref, contract.configuration, contract.manifest, state_ref)
    for index, event in enumerate(events):
        if index and event.kind == "update_requested" and evidence.pending is None:
            ref = BlobRef.from_dict(fields(thaw_json(event.payload), {"request"})["request"])
            request = UpdateRequest.from_bytes(record(reader, ref))
            if request.key in evidence.completed or request.key in evidence.aborted:
                raise RecordError("training-update-duplicate", "closed update keys cannot be reused")
            evidence.pending = evidence.prepare(reader, request)
            expected = (ref,)
        elif index and event.kind == "trainer_dispatched":
            ref = BlobRef.from_dict(fields(thaw_json(event.payload), {"invocation"})["invocation"])
            evidence.dispatch(reader, ref)
            expected = (ref,)
        elif index and event.kind == "trainer_returned":
            ref = BlobRef.from_dict(fields(thaw_json(event.payload), {"receipt"})["receipt"])
            evidence.returned(reader, ref)
            expected = (ref,)
        elif index and event.kind == "update_aborted" and evidence.pending is not None:
            row = fields(thaw_json(event.payload), {"request", "receipt"})
            ref, receipt = BlobRef.from_dict(row["request"]), BlobRef.from_dict(row["receipt"])
            check_abort(reader, evidence.pending, ref)
            if receipt != evidence.pending.receipt:
                raise RecordError("trainer-abort-receipt-mismatch", "abort must cite its failed dispatch receipt")
            evidence.aborted[evidence.pending.request.key] = ref
            evidence.pending = None
            expected = (ref, receipt)
        elif index and event.kind == "update_committed" and evidence.pending is not None:
            row = fields(thaw_json(event.payload), {"request", "state"})
            ref, state_ref = BlobRef.from_dict(row["request"]), BlobRef.from_dict(row["state"])
            if ref != evidence.pending.reference:
                raise RecordError("training-update-mismatch", "checkpoint belongs to another pending update")
            checkpoint = Checkpoint(BlobRef.of(event.to_bytes()), state_ref, evidence.state(reader, state_ref))
            evidence.check_returned_state(reader, checkpoint.state)
            evidence.completed[evidence.pending.request.key] = (ref, checkpoint)
            evidence.consumed.update(evidence.pending.exposures)
            evidence.checkpoint, evidence.pending = checkpoint, None
            expected = (ref, state_ref)
        elif index:
            raise RecordError("checkpoint-history-invalid", "expected alternating update request and state commit")
        if event.artifacts != tuple(dict.fromkeys(expected)):
            raise RecordError("checkpoint-dependencies-mismatch", "event dependency list differs from its record")
        evidence.references.add(BlobRef.of(event.to_bytes()))
    return evidence


def load_checkpoint(
    reader: ContentReader,
    head: BlobRef,
    *,
    contract: TrainerContract,
    verifier: TokenVerifier | None = None,
) -> tuple[Checkpoint, PendingUpdate | None]:
    """Return verified state and any unresolved update, without dispatch/deserialization."""
    evidence = recover(reader, load_chain(reader, head), contract, verifier)
    if evidence.checkpoint is None:
        raise RecordError("checkpoint-missing", "a committed initial state is required")
    return evidence.checkpoint, evidence.pending


class TrainingLedger:
    def __init__(self, run: JournalDirectory, contract: TrainerContract, *, verifier: TokenVerifier | None = None):
        if type(run) is not JournalDirectory or type(contract) is not TrainerContract:
            raise TypeError("training ledger requires a run and an operator-supplied contract")
        self.run, self.contract, self.verifier = run, contract, verifier
        self._owner = None

    def __enter__(self):
        if self._owner is not None:
            raise RuntimeError("training ledger is already open")
        owner = self.run.owner()
        owner.__enter__()
        try:
            self._journal = self.run.journal("optimizer")
            self._events = self._journal.load()
            self._evidence = recover(self.run.objects, self._events, self.contract, self.verifier)
            self._needs_reload = False
        except BaseException:
            owner.__exit__(None, None, None)
            raise
        self._owner = owner
        return self

    def __exit__(self, *exc):
        owner, self._owner = self._owner, None
        return owner.__exit__(*exc)

    def _check_open(self):
        if self._owner is None:
            raise RuntimeError("use the training ledger inside its ownership context")
        if self._needs_reload:
            raise AttemptInterrupted("training-journal-outcome-unknown")

    @property
    def checkpoint(self) -> Checkpoint | None:
        self._check_open()
        return self._evidence.checkpoint

    @property
    def pending(self) -> PendingUpdate | None:
        self._check_open()
        return self._evidence.pending

    def _append(self, kind, payload, refs):
        try:
            event = self._journal.append(self._events, kind, payload, refs)
        except BaseException:
            # The write may have committed before losing its acknowledgement.
            # Even seal() must not publish an old, apparently settled head.
            self._needs_reload = True
            raise
        self._events += (event,)
        self._evidence.references.add(BlobRef.of(event.to_bytes()))
        return BlobRef.of(event.to_bytes())

    def _publish_state(self, state, reader):
        # Validate before publishing/committing; a failed copy leaves only blobs.
        data = state.to_bytes()
        verify_state(reader, state, self.contract.components)
        self.run.objects.publish(reader, state.references)
        ref = self.run.objects.put(data)
        self._evidence.references.update((ref, *state.references))
        self._evidence.files.update(state.files)
        return ref

    def initialize(self, state: TrainingState, reader: ContentReader) -> Checkpoint:
        self._check_open()
        if self._events:
            raise RecordError("checkpoint-already-initialized", "resume from checkpoint; do not reset training")
        TrainingManifest.from_bytes(record(reader, self.contract.manifest))
        record(reader, self.contract.configuration)
        self.run.objects.publish(reader, (self.contract.manifest, self.contract.configuration))
        contract_ref = self.run.objects.put(self.contract.to_bytes())
        state_ref = self._publish_state(state, reader)
        refs = (contract_ref, self.contract.configuration, self.contract.manifest, state_ref)
        self._evidence.references.update(refs)
        head = self._append(
            "trainer_started",
            {
                "contract": contract_ref.as_dict(),
                "state": state_ref.as_dict(),
                "verifier": None if self.verifier is None else self.verifier.identity,
            },
            refs,
        )
        self._evidence.checkpoint = Checkpoint(head, state_ref, state)
        return self.checkpoint

    def prepare(self, request: UpdateRequest, reader: ContentReader) -> PendingUpdate | Checkpoint:
        """Verify and retain immutable inputs without authorizing a dispatch.

        Preparation may be retried after a configuration or staging failure. It
        does not create a pending update or consume exposures. Only begin grants
        launch permission; it rechecks the selection against the current head.
        """
        self._check_open()
        ref = BlobRef.of(request.to_bytes())
        previous = self._evidence.completed.get(request.key)
        if previous is not None:
            if previous[0] != ref:
                raise RecordError("training-update-mismatch", "update key was already committed with different inputs")
            return previous[1]
        if request.key in self._evidence.aborted:
            raise RecordError("training-update-aborted", "use a new explicit update key after abort")
        if self.pending is not None:
            raise AttemptInterrupted("training-update-outcome-unknown")
        # Stage evidence separately: failed validation cannot poison the live cache.
        from .checkpoints import Evidence

        staging = Evidence(
            self.contract,
            self.verifier,
            reports=dict(self._evidence.reports),
            checkpoint=self.checkpoint,
            consumed=self._evidence.consumed,
        )
        pending = staging.prepare(reader, request)
        self.run.objects.put(request.to_bytes())
        staging.references.discard(ref)
        self.run.objects.publish(reader, tuple(staging.references))
        self._evidence.references.update((*staging.references, ref))
        self._evidence.reports.update(staging.reports)
        return pending

    def begin(self, request: UpdateRequest, reader: ContentReader) -> PendingUpdate | Checkpoint:
        """A committed retry returns its old checkpoint; an unresolved retry raises."""
        pending = self.prepare(request, reader)
        if isinstance(pending, Checkpoint):
            return pending
        ref = pending.reference
        self._append("update_requested", {"request": ref.as_dict()}, (ref,))
        self._evidence.pending = pending
        return pending

    def commit(self, request: BlobRef, state: TrainingState, reader: ContentReader) -> Checkpoint:
        """Commit a backend-supplied state; does not infer whether an update ran."""
        self._check_open()
        if self.pending is None:
            for ref, checkpoint in self._evidence.completed.values():
                if ref == request and checkpoint.state_ref == BlobRef.of(state.to_bytes()):
                    return checkpoint
            raise RecordError("training-update-not-pending", "state has no matching pending update")
        if self.pending.reference != request:
            raise RecordError("training-update-mismatch", "state belongs to another pending update")
        self._evidence.check_returned_state(self.run.objects, state)
        state_ref = self._publish_state(state, reader)
        head = self._append(
            "update_committed", {"request": request.as_dict(), "state": state_ref.as_dict()}, (request, state_ref)
        )
        checkpoint = Checkpoint(head, state_ref, state)
        self._evidence.completed[self.pending.request.key] = (request, checkpoint)
        self._evidence.consumed.update(self.pending.exposures)
        self._evidence.checkpoint, self._evidence.pending = checkpoint, None
        return checkpoint

    def record_dispatch(self, invocation: BlobRef, reader: ContentReader) -> None:
        """Persist verified process inputs before execute_update launches it."""
        from dataclasses import replace

        self._check_open()
        staging = replace(self._evidence, references=set(self._evidence.references))
        staging.dispatch(reader, invocation)
        refs = tuple(staging.references - self._evidence.references)
        self.run.objects.publish(reader, refs)
        self._append("trainer_dispatched", {"invocation": invocation.as_dict()}, (invocation,))
        self._evidence.pending = staging.pending
        self._evidence.references.update(refs)

    def abort(self, request: BlobRef) -> Checkpoint:
        """Explicitly close a cleaned failure, keeping the last committed state.

        This does not restart an optimizer or consume the failed exposures. A
        subsequent update must have a new key. Unknown remote cleanup and fully
        captured successful results cannot be discarded through this operation.
        """
        from .checkpoints import check_abort

        self._check_open()
        if request in self._evidence.aborted.values():
            return self.checkpoint
        check_abort(self.run.objects, self.pending, request)
        receipt, key = self.pending.receipt, self.pending.request.key
        self._append("update_aborted", {"request": request.as_dict(), "receipt": receipt.as_dict()}, (request, receipt))
        self._evidence.aborted[key] = request
        self._evidence.pending = None
        return self.checkpoint

    def record_return(self, receipt: BlobRef, reader: ContentReader) -> None:
        """Record even failed/uncertain exits; they never authorize a new launch."""
        from dataclasses import replace

        self._check_open()
        staging = replace(self._evidence, references=set(self._evidence.references))
        staging.returned(reader, receipt)
        refs = tuple(staging.references - self._evidence.references)
        self.run.objects.publish(reader, refs)
        self._append("trainer_returned", {"receipt": receipt.as_dict()}, (receipt,))
        self._evidence.pending = staging.pending
        self._evidence.references.update(refs)

    def seal(self) -> tuple[BlobRef, tuple[BlobRef, ...]]:
        """Export the explicit state-chunk/report closure, not just direct events."""
        self._check_open()
        if not self._events:
            raise RecordError("checkpoint-missing", "initialize before sealing")
        for event in self._events:
            self.run.objects.put(event.to_bytes())
        return BlobRef.of(self._events[-1].to_bytes()), tuple(
            sorted(self._evidence.references, key=lambda ref: ref.sha256)
        )


def restore_ledger(
    reader: ContentReader,
    head: BlobRef,
    *,
    run: JournalDirectory,
    contract: TrainerContract,
    verifier: TokenVerifier | None = None,
) -> tuple[Checkpoint, PendingUpdate | None]:
    """Install an exported history after the operator has fenced the old owner.

    Copies immutable evidence only; no model loading, optimizer call or pending
    update retry. A partial import resumes from its exact prefix. A divergent or
    newer destination is rejected, never overwritten or rolled back.
    """
    events = load_chain(reader, head)
    evidence = recover(reader, events, contract, verifier)
    if evidence.checkpoint is None:
        raise RecordError("checkpoint-missing", "restore requires a committed initial state")
    with run.owner():
        journal = run.journal("optimizer")
        installed = journal.load()
        if installed != events[: len(installed)]:
            raise RecordError("checkpoint-restore-conflict", "destination is not a prefix of the exported history")
        run.objects.publish(reader, tuple(evidence.references))
        for event in events[len(installed) :]:
            committed = journal.append(installed, event.kind, thaw_json(event.payload), event.artifacts)
            installed += (committed,)
    return evidence.checkpoint, evidence.pending
