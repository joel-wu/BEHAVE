"""Freeze the shared external material, independently of task admission.

The Raw route receives only ``documents``. Acquisition facts, generated tasks,
candidate code and notices stay in the researcher evidence store. A rejected
task does not remove the source that was acquired to construct it.
"""

from dataclasses import dataclass, replace

from behave.agent.journal import load_chain
from behave.core.artifacts import BlobRef, ContentReader, FileData, FileRef, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, text, thaw_json, version
from behave.core.storage import DirectoryStore
from behave.data.role import input_closure

from .acquisition import AcquisitionConfiguration, load_acquisition_report, material_source_ids
from .library import LibraryIndex, LookupPolicy


@dataclass(frozen=True, slots=True)
class MaterialRequest:
    round_id: str
    checkpoint: BlobRef
    library: BlobRef
    feedback: BlobRef
    model_transport: str
    acquisitions: tuple[BlobRef, ...]
    supplements: tuple[BlobRef, ...] = ()
    feedback_request: BlobRef | None = None

    def __post_init__(self):
        text(self.round_id, "round id")
        text(self.model_transport, "acquisition model transport")
        if any(type(ref) is not BlobRef for ref in (self.checkpoint, self.library, self.feedback)):
            raise TypeError("material requests require checkpoint, library and feedback identities")
        if type(self.acquisitions) is not tuple or any(type(ref) is not BlobRef for ref in self.acquisitions):
            raise TypeError("acquisitions must be settled stage heads")
        if len(set(self.acquisitions)) != len(self.acquisitions):
            raise ValueError("an acquisition stage cannot be counted twice")

    def to_bytes(self):
        row = {"schema": "behave.round-material-request/1", "round_id": self.round_id,
            "checkpoint": self.checkpoint.as_dict(), "library": self.library.as_dict(),
            "feedback": self.feedback.as_dict(), "model_transport": self.model_transport,
            "acquisitions": [ref.as_dict() for ref in self.acquisitions]}
        if self.supplements:
            row.update(schema="behave.round-material-request/2",
                       supplements=[ref.as_dict() for ref in self.supplements])
        if self.feedback_request is not None:
            row.update(schema="behave.round-material-request/3",
                       supplements=[ref.as_dict() for ref in self.supplements],
                       feedback_request=self.feedback_request.as_dict())
        return canonical_json(row)

    @classmethod
    def from_bytes(cls, payload):
        value = parse_json(payload)
        imported = value.get("schema") == "behave.round-material-request/3"
        extended = imported or value.get("schema") == "behave.round-material-request/2"
        keys = {"schema", *cls.__dataclass_fields__} - {"supplements", "feedback_request"}
        row = fields(value, keys | ({"supplements"} if extended else set())
                     | ({"feedback_request"} if imported else set()))
        version(row["schema"], f"behave.round-material-request/{3 if imported else 2 if extended else 1}")
        result = cls(row["round_id"], *(BlobRef.from_dict(row[key]) for key in
                     ("checkpoint", "library", "feedback")),
                     row["model_transport"],
                     tuple(BlobRef.from_dict(ref) for ref in row["acquisitions"]),
                     tuple(BlobRef.from_dict(ref) for ref in row.get("supplements", ())),
                     BlobRef.from_dict(row["feedback_request"]) if imported else None)
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "round material request must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class FrozenMaterials:
    request: BlobRef
    documents: tuple[FileRef, ...]

    def to_bytes(self):
        return canonical_json({"schema": "behave.round-materials/1", "request": self.request.as_dict(),
                               "documents": [file.as_dict() for file in self.documents]})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "request", "documents"})
        version(row["schema"], "behave.round-materials/1")
        result = cls(BlobRef.from_dict(row["request"]), tuple(FileRef.from_dict(file) for file in row["documents"]))
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "frozen material record must be canonical")
        return result


def freeze_materials(request: MaterialRequest, reader: ContentReader, *,
                     acquisition_reports=None) -> FrozenMaterials:
    """Recompute the acquired sources plus explicitly supplied source libraries.

    The controller supplies successful acquisition stages. The round journal
    retains and checks all outcomes, including terminal failures that selected
    no material. This function cannot discover deliberately omitted successes.
    Empty selections are valid. An unfinished/interrupted stage is not silently
    converted into an empty successful acquisition.

    The live coordinator passes the reports it has already settled and committed.
    Reuse those reports without rereading model/tool payloads. Standalone imports
    still reconstruct the reports from their recorded evidence.
    """
    from behave.agent.workspace import WorkspaceSnapshot

    read_verified(reader, request.checkpoint)
    workspace = WorkspaceSnapshot.from_bytes(read_verified(reader, request.feedback))
    feedback_file = workspace.file("feedback.json")
    if feedback_file is None:
        raise RecordError("material-feedback-missing", "round feedback has no facts")
    facts = parse_json(read_verified(reader, feedback_file.content))
    if request.feedback_request is not None:
        from .feedback import FeedbackRequest, read_feedback_request
        producer = read_feedback_request(read_verified(reader, request.feedback_request))
        checkpoint = producer.checkpoint.as_dict() if isinstance(producer, FeedbackRequest) else None
        if (facts["request"] != request.feedback_request.as_dict()
                or facts["round_id"] != producer.round_id
                or facts["checkpoint"] != checkpoint):
            raise RecordError("material-context-mismatch", "feedback differs from its recorded producer")
    elif facts["round_id"] != request.round_id or facts["checkpoint"] != request.checkpoint.as_dict():
        raise RecordError("material-context-mismatch", "feedback belongs to another round or checkpoint")
    index = LibraryIndex(reader, request.library, LookupPolicy())
    selected = set()
    reports = (load_acquisition_report(reader, head) for head in request.acquisitions) \
        if acquisition_reports is None else acquisition_reports
    for head, report in zip(request.acquisitions, reports, strict=True):
        if report["head"] != head.as_dict():
            raise RecordError("material-acquisition-mismatch", "report belongs to another acquisition stage")
        if report["status"] != "completed":
            raise RecordError("material-acquisition-unfinished", "settle acquisition before freezing material")
        events = load_chain(reader, head, verify_artifacts=False)
        config_ref = BlobRef.from_dict(thaw_json(events[0].payload)["configuration"])
        config = AcquisitionConfiguration.from_bytes(read_verified(reader, config_ref))
        if (config.job.inputs.library, config.job.inputs.feedback) != (request.library, request.feedback):
            raise RecordError("material-context-mismatch", "acquisition used different feedback or source access")
        if config.job.model.identity != request.model_transport:
            raise RecordError("material-model-mismatch", "acquisition used an unbound model transport")
        if acquisition_reports is None:
            input_closure(config.job, reader)
        selected.update(material_source_ids(report["materials"]["materials"]))
    documents = {index.units[key].content.content: index.units[key].content for key in sorted(selected)}
    for reference in request.supplements:
        extra = LibraryIndex(reader, reference, LookupPolicy())
        for key in sorted(extra.units):
            file = extra.units[key].content
            documents.setdefault(file.content, file)
    return FrozenMaterials(BlobRef.of(request.to_bytes()), tuple(documents.values()))


def load_materials(reader: ContentReader, reference: BlobRef) -> FrozenMaterials:
    frozen = FrozenMaterials.from_bytes(read_verified(reader, reference))
    request = MaterialRequest.from_bytes(read_verified(reader, frozen.request))
    if freeze_materials(request, reader) != frozen:
        raise RecordError("material-evidence-mismatch", "frozen documents differ from the acquired source union")
    return frozen


def supplement_materials(*, materials: BlobRef, libraries: tuple[BlobRef, ...],
                         objects: DirectoryStore) -> BlobRef:
    """Create a new common C_r from explicit external-source additions.

    Import the additions with ``import_library`` into the same object store.
    Every unit in those libraries is selected; import only the intended additions,
    not a whole search corpus. Origins/notices remain evidence, not Raw examples.
    This researcher/controller API does not start acquisition, construction or
    learning. Pass its result to ``prepare_round_data`` before either learner.
    Existing material and learning records are never overwritten.
    """
    frozen = load_materials(objects, materials)
    request = MaterialRequest.from_bytes(read_verified(objects, frozen.request))
    request = replace(request, supplements=tuple(dict.fromkeys((*request.supplements, *libraries))))
    result = freeze_materials(request, objects)
    objects.put(request.to_bytes())
    return objects.put(result.to_bytes())


def raw_documents(reader: ContentReader, reference: BlobRef) -> tuple[FileData, ...]:
    """Explicit Raw learner input. Never export the feedback/evidence closure."""
    return tuple(FileData(file, read_verified(reader, file.content))
                 for file in load_materials(reader, reference).documents)


def construction_briefs(reader: ContentReader, reference: BlobRef) -> dict[str, bytes]:
    """Carry acquisition objectives to task construction, never to Raw learning."""
    material = load_materials(reader, reference)
    request = MaterialRequest.from_bytes(read_verified(reader, material.request))
    return source_briefs(request.library, (load_acquisition_report(reader, head)
                                          for head in request.acquisitions))


def source_briefs(library: BlobRef, reports) -> dict[str, bytes]:
    """Format already-committed Analyst outcomes, without replaying their tools."""
    objectives, supporting = {}, {}
    for report in reports:
        for item in report["materials"]["materials"]:
            objectives.setdefault(item["id"], []).append({
                "objective": item["objective"], "rationale": item["rationale"],
                "observation_ids": item["observation_ids"],
            })
            if "supporting_source_ids" in item:
                supporting.setdefault(item["id"], []).extend(item["supporting_source_ids"])
    return {identifier: canonical_json({"schema": "behave.construction-brief/1",
        "library": library.as_dict(), "source_id": identifier, "objectives": items,
        **({"supporting_source_ids": list(dict.fromkeys(supporting[identifier]))}
           if identifier in supporting else {})})
        for identifier, items in objectives.items()}


def construction_proposals(reader: ContentReader, reference: BlobRef) -> dict[str, bytes]:
    """One Analyst proposal per Builder, including distinct uses of one source."""
    material = load_materials(reader, reference)
    request = MaterialRequest.from_bytes(read_verified(reader, material.request))
    return proposal_briefs(request.library, (load_acquisition_report(reader, head)
                                            for head in request.acquisitions))


def proposal_briefs(library: BlobRef, reports) -> dict[str, bytes]:
    """Keep proposal identity/order from the coordinator's committed outcomes."""
    proposals = {}
    for worker, report in enumerate(reports):
        for number, item in enumerate(report["materials"]["materials"]):
            identifier = f"a{worker}-p{number}"
            proposals[identifier] = canonical_json({"schema": "behave.construction-brief/2",
                "library": library.as_dict(), "source_id": item["id"],
                "proposal_id": item.get("proposal_id", identifier),
                **({"supporting_source_ids": item["supporting_source_ids"]} if "supporting_source_ids" in item else {}),
                **{key: item[key] for key in ("objective", "rationale", "observation_ids")}})
    return proposals
