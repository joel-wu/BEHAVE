"""A task author's request to revise source intent beyond an ordinary task repair."""

from dataclasses import dataclass

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, version


@dataclass(frozen=True, slots=True)
class ScopeEscalation:
    task_id: str
    scope: BlobRef
    parent: BlobRef | None
    findings: tuple[str, ...]

    def __post_init__(self):
        text(self.task_id, "task id")
        if type(self.scope) is not BlobRef or (self.parent is not None and type(self.parent) is not BlobRef):
            raise TypeError("scope escalation requires immutable subject references")
        if type(self.findings) is not tuple or not self.findings:
            raise ValueError("scope escalation requires findings")
        for finding in self.findings:
            text(finding, "scope finding")

    def to_bytes(self):
        return canonical_json({"schema": "behave.scope-escalation/1", "task_id": self.task_id,
            "scope": self.scope.as_dict(), "parent": None if self.parent is None else self.parent.as_dict(),
            "findings": list(self.findings)})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "task_id", "scope", "parent", "findings"})
        version(row["schema"], "behave.scope-escalation/1")
        result = cls(row["task_id"], BlobRef.from_dict(row["scope"]),
                     None if row["parent"] is None else BlobRef.from_dict(row["parent"]),
                     tuple(array(row["findings"])))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "scope escalation must be canonical")
        return result

