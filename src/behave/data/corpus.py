"""Deterministic corpus audit shared by release and training membership gates.

Exact byte/selector keys and operator-assigned families are deliberately separate.
No parser guesses semantic similarity, rewrites tasks or chooses a winning copy.
"""

from collections import defaultdict
from dataclasses import dataclass

from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, version

from .drafts import SourceRecord, source_closure
from .published import TaskManifest
from .published.files import task_files
from .selection_records import content_key

PARTITIONS = frozenset({"train-original", "train-acquired", "final-test"})


def partition(value: str) -> None:
    if type(value) is not str or value not in PARTITIONS:
        raise ValueError("partition must be train-original, train-acquired or final-test")


@dataclass(frozen=True, slots=True)
class CorpusEntry:
    task_id: str
    revision: BlobRef
    source: BlobRef
    family: str
    partition: str
    source_key: str
    spec_key: str
    computation_key: str

    def __post_init__(self):
        text(self.task_id, "corpus task id")
        text(self.family, "operator-assigned family")
        partition(self.partition)
        if type(self.revision) is not BlobRef or type(self.source) is not BlobRef:
            raise TypeError("corpus entry requires task revision and source record")
        for key in (self.source_key, self.spec_key, self.computation_key):
            BlobRef(key, 0)  # Reuse the strict SHA-256 syntax contract.

    def as_dict(self):
        return {"task_id": self.task_id, "revision": self.revision.as_dict(), "source": self.source.as_dict(),
                "family": self.family, "partition": self.partition, "source_key": self.source_key,
                "spec_key": self.spec_key, "computation_key": self.computation_key}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, set(cls.__dataclass_fields__))
        return cls(**{**row, "revision": BlobRef.from_dict(row["revision"]),
                      "source": BlobRef.from_dict(row["source"])})


@dataclass(frozen=True, slots=True)
class CorpusIndex:
    name: str
    curator: str
    rationale: str
    entries: tuple[CorpusEntry, ...]

    def __post_init__(self):
        for value in (self.name, self.curator, self.rationale):
            text(value, "corpus provenance")
        if (type(self.entries) is not tuple or not self.entries
                or any(type(entry) is not CorpusEntry for entry in self.entries)):
            raise TypeError("corpus index requires explicit entries")
        if len({entry.task_id for entry in self.entries}) != len(self.entries):
            raise ValueError("choose one revision and partition per corpus task id")
        object.__setattr__(self, "entries", tuple(sorted(self.entries, key=lambda entry: entry.task_id)))

    def to_bytes(self):
        payload = canonical_json({"schema": "behave.corpus-index/1", "name": self.name, "curator": self.curator,
                                  "rationale": self.rationale, "entries": [e.as_dict() for e in self.entries]})
        return payload

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "name", "curator", "rationale", "entries"})
        version(row["schema"], "behave.corpus-index/1")
        result = cls(row["name"], row["curator"], row["rationale"],
                     tuple(CorpusEntry.from_dict(e) for e in array(row["entries"])))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "corpus index must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class CorpusGuard:
    index: BlobRef
    partition: str

    def __post_init__(self):
        if type(self.index) is not BlobRef:
            raise TypeError("corpus guard requires a pinned index")
        partition(self.partition)

    def as_dict(self):
        return {"index": self.index.as_dict(), "partition": self.partition}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"index", "partition"})
        return cls(BlobRef.from_dict(row["index"]), row["partition"])


def record(reader: ContentReader, ref: BlobRef) -> bytes:
    return read_verified(reader, ref)


def task_keys(task: TaskManifest) -> tuple[str, str]:
    """Ignore task names/provenance, not executable selectors or supporting files."""
    computation = canonical_json({"schema": "behave.corpus-computation-key/1",
        "contract": task.contract.content.as_dict(), "behavior": task.reference_behavior.as_dict(),
        "public_files": [f.as_dict() for f in task.public_files]})
    return task.spec.content.sha256, BlobRef.of(computation).sha256


def make_entry(reader: ContentReader, *, revision: BlobRef, source: BlobRef,
               family: str, partition: str) -> CorpusEntry:
    """Build a fingerprint from verified bytes, without executing source or tasks.

    A trusted curator supplies source association and family/partition. Publication
    later checks that the source association matches the admitted construction.
    """
    task = TaskManifest.from_bytes(record(reader, revision))
    original = SourceRecord.from_bytes(record(reader, source))
    files = task_files(task)
    refs = tuple(dict.fromkeys((*(f.content for f in files), task.run_policy, task.judge_policy)))
    for ref in refs:
        read_verified(reader, ref)
    source_closure(reader, source)
    spec, computation = task_keys(task)
    return CorpusEntry(task.task_id, revision, source, family, partition, content_key(original), spec, computation)


def conflicts(index: CorpusIndex) -> tuple[dict, ...]:
    """Return groups, not quadratic pairs; retain every implicated task id."""
    result = []
    for key in ("source_key", "spec_key", "computation_key", "family"):
        groups = defaultdict(list)
        for entry in index.entries:
            groups[getattr(entry, key)].append(entry)
        for value, entries in sorted(groups.items()):
            if len(entries) < 2:
                continue
            domains = {"test" if e.partition == "final-test" else "train" for e in entries}
            if key == "family" and len(domains) < 2:
                continue
            result.append({"kind": key.removesuffix("_key"), "key": value,
                           "tasks": [e.task_id for e in entries],
                           "cross_split": len(domains) > 1})
    return tuple(result)


def load_index(reader: ContentReader, ref: BlobRef, *, require_clean: bool = True) -> CorpusIndex:
    index = CorpusIndex.from_bytes(record(reader, ref))
    if require_clean and conflicts(index):
        raise RecordError("corpus-conflict", "exact duplicates or cross-split families require explicit curation")
    return index


def summary(reader: ContentReader, ref: BlobRef) -> dict:
    index = load_index(reader, ref, require_clean=False)
    found = conflicts(index)
    return {"schema": "behave.corpus-audit/1", "index": ref.as_dict(), "tasks": len(index.entries),
            "partitions": {p: sum(e.partition == p for e in index.entries)
                           for p in ("train-original", "train-acquired", "final-test")},
            "clean": not found, "conflicts": list(found), "semantic_deduplication": "not_checked"}


def assigned(entries: dict[str, CorpusEntry], guard: CorpusGuard, *, task: TaskManifest,
             source: BlobRef | None = None, source_key: str | None = None) -> CorpusEntry:
    entry = entries.get(task.task_id)
    if entry is None or entry.revision != BlobRef.of(task.to_bytes()):
        raise RecordError("corpus-task-unassigned", "task revision is not in this corpus snapshot")
    if entry.partition != guard.partition:
        raise RecordError("corpus-partition-mismatch", "task is assigned to another partition")
    if ((entry.spec_key, entry.computation_key) != task_keys(task)
            or (source is not None and entry.source != source)
            or (source_key is not None and entry.source_key != source_key)):
        raise RecordError("corpus-fingerprint-mismatch", "corpus fingerprint differs from the actual admitted content")
    return entry


def guard_admissions(reader: ContentReader, guard: CorpusGuard, admissions):
    """Check included revisions; existing pending evidence remains pending."""
    from dataclasses import replace

    index = load_index(reader, guard.index)
    entries = {e.task_id: e for e in index.entries}
    for admission in admissions:
        if admission.disposition.outcome == "include":
            source = admission.disposition.source
            original = SourceRecord.from_bytes(record(reader, source))
            assigned(entries, guard, task=admission.task, source=source, source_key=content_key(original))
    # The hash-only private index is portable; held-out task bodies are not copied.
    return tuple(replace(a, closure=tuple(dict.fromkeys((*a.closure, guard.index)))) for a in admissions)
