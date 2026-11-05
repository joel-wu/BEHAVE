"""Source screening records, distinct from scope approval and dataset admission."""

from dataclasses import dataclass

from behave.core.artifacts import BlobRef, FileRef
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, version

from .drafts import SourceRecord


def record_bytes(reader, ref):
    from behave.core.artifacts import read_verified

    return read_verified(reader, ref)


def decode(payload, schema, keys):
    row = fields(parse_json(payload), {"schema", *keys})
    version(row.pop("schema"), schema)
    return row


def canonical(payload, result):
    if result.to_bytes() != payload:
        raise RecordError("record-not-canonical", "selection records must be canonical")
    return result


def content_key(source: SourceRecord) -> str:
    """Exact selected bytes, paths and entrypoint; not provenance or semantic equivalence."""
    if type(source) is not SourceRecord:
        raise TypeError("source content key requires SourceRecord")
    return BlobRef.of(canonical_json({"schema": "behave.source-content-key/1",
                                     "selected": source.selected.as_dict()})).sha256


@dataclass(frozen=True, slots=True)
class SourceInventory:
    sources: tuple[BlobRef, ...]
    discovery: BlobRef | None = None

    def __post_init__(self):
        if (type(self.sources) is not tuple or not self.sources
                or any(type(s) is not BlobRef for s in self.sources)):
            raise ValueError("inventory requires explicit source references")
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("inventory source references must be unique")
        if self.discovery is not None and type(self.discovery) is not BlobRef:
            raise TypeError("inventory discovery requires a sealed receipt")

    def to_bytes(self):
        return canonical_json({"schema": "behave.source-inventory/1" if self.discovery is None else
                               "behave.source-inventory/2", "sources": [s.as_dict() for s in self.sources],
                               **({} if self.discovery is None else {"discovery": self.discovery.as_dict()})})

    @classmethod
    def from_bytes(cls, payload):
        extended = "discovery" in parse_json(payload)
        row = decode(payload, "behave.source-inventory/2" if extended else "behave.source-inventory/1",
                     {"sources", "discovery"} if extended else {"sources"})
        return canonical(payload, cls(tuple(BlobRef.from_dict(s) for s in array(row["sources"])),
                                      BlobRef.from_dict(row["discovery"]) if extended else None))


@dataclass(frozen=True, slots=True)
class SourceSpan:
    file: FileRef
    start: int
    end: int

    def __post_init__(self):
        if (type(self.file) is not FileRef or type(self.start) is not int or type(self.end) is not int
                or not 0 <= self.start < self.end):
            raise ValueError("citation requires a pinned nonempty half-open character span")

    def as_dict(self):
        return {"file": self.file.as_dict(), "start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"file", "start", "end"})
        return cls(FileRef.from_dict(row["file"]), row["start"], row["end"])


@dataclass(frozen=True, slots=True)
class SourceAssessment:
    source: BlobRef
    disposition: str
    rationale: str
    citations: tuple[SourceSpan, ...]

    def __post_init__(self):
        if type(self.source) is not BlobRef:
            raise TypeError("assessment requires a source reference")
        if self.disposition not in {"selected", "rejected", "deferred", "unreviewed"}:
            raise ValueError("unknown source disposition")
        text(self.rationale, "source rationale")
        if (type(self.citations) is not tuple
                or any(type(c) is not SourceSpan for c in self.citations)):
            raise TypeError("assessment requires source citations")
        if self.disposition in {"selected", "rejected"} and not self.citations:
            raise ValueError("selected/rejected sources require inspected code evidence")
        if self.disposition == "unreviewed" and self.citations:
            raise ValueError("unreviewed source cannot claim cited inspection")

    def as_dict(self):
        return {"source": self.source.as_dict(), "disposition": self.disposition, "rationale": self.rationale,
                "citations": [c.as_dict() for c in self.citations]}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"source", "disposition", "rationale", "citations"})
        return cls(BlobRef.from_dict(row["source"]), row["disposition"], row["rationale"],
                   tuple(SourceSpan.from_dict(c) for c in array(row["citations"])))


@dataclass(frozen=True, slots=True)
class SourceSelection:
    inventory: BlobRef
    guidance: BlobRef
    assessments: tuple[SourceAssessment, ...]

    def __post_init__(self):
        if any(type(ref) is not BlobRef for ref in (self.inventory, self.guidance)):
            raise TypeError("selection requires pinned inventory and guidance")
        if (type(self.assessments) is not tuple or not self.assessments
                or any(type(a) is not SourceAssessment for a in self.assessments)):
            raise ValueError("selection requires source assessments")
        if len({a.source for a in self.assessments}) != len(self.assessments):
            raise ValueError("source assessed more than once")

    @property
    def selected(self) -> tuple[BlobRef, ...]:
        return tuple(a.source for a in self.assessments if a.disposition == "selected")

    def to_bytes(self):
        # Existing zero/single-source decisions keep their original identities.
        schema = "behave.source-selection/2" if len(self.selected) > 1 else "behave.source-selection/1"
        return canonical_json({"schema": schema, "inventory": self.inventory.as_dict(),
            "guidance": self.guidance.as_dict(), "assessments": [a.as_dict() for a in self.assessments]})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "inventory", "guidance", "assessments"})
        if row["schema"] not in {"behave.source-selection/1", "behave.source-selection/2"}:
            version(row["schema"], "behave.source-selection/2")
        return canonical(payload, cls(BlobRef.from_dict(row["inventory"]), BlobRef.from_dict(row["guidance"]),
                                     tuple(SourceAssessment.from_dict(a) for a in array(row["assessments"]))))
