"""Construction-only interface documentation, using the current public schema.
"""

from copy import deepcopy
from dataclasses import dataclass, replace

from behave.agent.prompting import render
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, FileData, FileRef, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json

from .checks import ConcreteCheck, load_check
from .drafts import load_draft
from .reviews import reconstruction_inputs


def contract_guidance(prefix: str) -> tuple[FileData, ...]:
    """Examples are editable starting points, not task or runtime defaults."""
    from behave.sim import api

    files = {"task_contract.md": render("synthesis/task_contract").encode()}
    adapters = {
        "ready_valid": lambda direction: api.ready_valid(direction, direction + "_valid", direction + "_ready"),
        "sampled_edge": api.sampled_edge,
        "settled": api.settled,
    }
    for style, adapter in adapters.items():
        clocked = style != "settled"
        endpoints = tuple(api.Endpoint(
            name, direction, (api.Field(field, api.FiniteType(16, signed=True), port=field),),
            adapter(direction),
            "main" if clocked else None, kind=api.Kind.TRANSACTION,
            reset_policy=api.ResetPolicy.CLEARS,
            answers_per_transaction=1 if direction == "out" else None,
        ) for name, direction, field in (("input", "in", "in_x"), ("output", "out", "out_y")))
        clocks = (api.ClockDomain("main", "clk", "rst_n", reset_active_low=True),) if clocked else ()
        files[f"contract_{style}.json"] = canonical_json(api.TaskContract(endpoints, clocks).as_dict())
    return tuple(FileData(FileRef(prefix + name, BlobRef.of(data)), data) for name, data in files.items())


def validate_binding_revision(original: bytes, revised: bytes):
    """Only physical pin spellings may change, never domains or observations.

    In particular field names/types, endpoints, adapters, timing, reset semantics,
    configuration, input rules and scoring policies are not editable here.
    """
    from behave.sim import api

    before, after = parse_json(original), parse_json(revised)
    try:
        api.TaskContract.from_dict(after)
    except api.Refused as error:
        raise RecordError("contract-repair-invalid", str(error)) from error

    def without_pin_spellings(row):
        row = deepcopy(row)
        for clock in row.get("clocks", []):
            for key in ("clock", "reset"):
                if clock.get(key) is not None:
                    clock[key] = "<physical-pin>"
        for endpoint in row.get("endpoints", []):
            for field in endpoint.get("fields", []):
                field["port"] = "<physical-pin>"
            adapter = endpoint.get("adapter")
            if adapter is not None:
                for key in ("artifact_controls", "environment_controls"):
                    if key in adapter:
                        adapter[key] = ["<physical-pin>" for _ in adapter[key]]
        return row

    if without_pin_spellings(before) != without_pin_spellings(after):
        raise RecordError("contract-repair-needs-builder",
            "Direct repair only corrects physical pin bindings under the unchanged specification. "
            "Changes to types, protocol, timing, inputs or observations must go to Builder.")


@dataclass(frozen=True)
class ContractRevision:
    draft: BlobRef
    comparison: ConcreteCheck

    def as_dict(self):
        return {"draft": self.draft.as_dict(), "comparison": self.comparison.as_dict()}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"draft", "comparison"})
        return cls(BlobRef.from_dict(row["draft"]), ConcreteCheck.from_dict(row["comparison"]))


def revise_contract(reader, objects, draft, content):
    loaded = load_draft(reader, draft)
    task = loaded.record.task
    validate_binding_revision(loaded.file(task.contract.path).data, content)
    contract = FileRef(task.contract.path, objects.put(content))
    previous = WorkspaceSnapshot.from_bytes(read_verified(reader, loaded.record.workspace))
    snapshot = replace(previous, files=tuple(contract if f.path == contract.path else f for f in previous.files))
    revised = replace(loaded.record, task=replace(task, contract=contract),
                      workspace=objects.put(snapshot.to_bytes()), parent=draft)
    return objects.put(revised.to_bytes())


def binding_revision(reader, original, revised):
    """Load a derived draft that differs only in its physical pin bindings."""
    before, after = load_draft(reader, original), load_draft(reader, revised)
    new_task = after.record.task
    expected = replace(before.record, task=replace(before.record.task, contract=new_task.contract),
                       workspace=after.record.workspace, parent=original)
    if after.record != expected or new_task.contract.path != before.record.task.contract.path:
        raise RecordError("contract-repair-changed-task", "Contract repair changed more than its contract file")
    old_files = {f.reference.path: f.reference for f in before.files}
    new_files = {f.reference.path: f.reference for f in after.files}
    old_files[new_task.contract.path] = new_task.contract
    if old_files != new_files:
        raise RecordError("contract-repair-changed-workspace", "Contract repair changed another task file")
    previous = WorkspaceSnapshot.from_bytes(read_verified(reader, before.record.workspace))
    current = WorkspaceSnapshot.from_bytes(read_verified(reader, after.record.workspace))
    if replace(current, files=previous.files) != previous:
        raise RecordError("contract-repair-changed-workspace", "Contract repair changed workspace metadata")
    contract = after.file(new_task.contract.path)
    validate_binding_revision(before.file(new_task.contract.path).data, contract.data)
    return after


def revision_inputs(reader, original, reconstruction, revised):
    after = binding_revision(reader, original, revised)
    contract = after.file(after.record.task.contract.path)
    # Deliberately retain the original reconstruction and its original input
    # identity. This is a recheck of unchanged code, not a new blind B2 attempt.
    return replace(reconstruction_inputs(reader, original, reconstruction), contract=contract)


def validate_contract_revision(reader, original, reconstruction, revision):
    expected = revision_inputs(reader, original, reconstruction, revision.draft)
    check = revision.comparison
    if read_verified(reader, check.inputs) != canonical_json(expected.as_dict()):
        raise RecordError("contract-repair-check-mismatch", "Repair check used different task inputs")
    if load_check(reader, check.event) != check:
        raise RecordError("contract-repair-check-mismatch", "Repair check differs from its recorded result")
    return load_draft(reader, revision.draft)


def effective_draft(reader, drafts, phases):
    """The author history stays intact; a final Reviewer may replace bindings."""
    from .source import SourceReview

    latest = drafts[-1]
    review_phase = next((p for p in reversed(phases)
                        if p.role == "source_review" and p.revision == len(drafts) - 1), None)
    if review_phase is not None:
        from behave.agent.journal import Event

        terminal = Event.from_bytes(read_verified(reader, review_phase.head))
        action = terminal.payload.get("final_action")
        if action is not None and "source_review" in action:
            review = SourceReview.from_bytes(read_verified(reader, BlobRef.from_dict(dict(action["source_review"]))))
            if review.contract_revision is not None:
                return review.contract_revision.draft
    return latest
