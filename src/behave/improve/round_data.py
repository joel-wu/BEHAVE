"""Join a round's shared material with the existing task-admission publisher.

The two learning routes consume different views of this commit: Raw receives
only the material documents, while Task receives the admitted task release.
Pending tasks remain in the publication record and do not remove Raw material.
This stage neither selects a training curriculum nor runs a learner.
"""

from dataclasses import dataclass, replace

from behave.agent.journal import load_chain
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.data.drafts import SourceRecord
from behave.data.publication import load_publication, publish_release
from behave.data.published import ReleaseManifest
from behave.data.release_records import ReleaseDisposition, ReleaseRequest
from behave.train.plan import TrainingManifest, manifest_for_release

from .input_stores import ReadSet
from .library import LibraryIndex, LookupPolicy
from .materials import FrozenMaterials, MaterialRequest, load_materials


@dataclass(frozen=True, slots=True)
class RoundData:
    head: BlobRef
    materials: BlobRef
    publication: BlobRef | None
    release: BlobRef | None

    def as_dict(self):
        return {key: None if (ref := getattr(self, key)) is None else ref.as_dict()
                for key in self.__dataclass_fields__}


def committed_round_data(reader, head):
    """Read the owner's completed handoff, without repeating task admission.

    Publication already ran admission before this receipt was committed.
    Return its recorded closure for ownership transfer; load_round_data remains
    the explicit full audit of external/exported evidence.
    """
    events = load_chain(reader, head, verify_artifacts=False)
    if tuple(event.kind for event in events) != ("round_data_requested", "round_materials_ready", "round_data_ready"):
        raise RecordError("round-data-incomplete", "the shared material/task handoff has not committed")
    row = thaw_json(events[-1].payload)
    result = RoundData(head, BlobRef.from_dict(row["materials"]),
        None if row["publication"] is None else BlobRef.from_dict(row["publication"]),
        None if row["release"] is None else BlobRef.from_dict(row["release"]))
    closure = tuple(dict.fromkeys(ref for event in events
                    for ref in (BlobRef.of(event.to_bytes()), *event.artifacts)))
    return result, closure


# Historical imports retain the same implementation; new callers use input_stores.
_ReadSet = ReadSet


def admitted_training_manifest(reader, release):
    """Derive the Task view once, for execution and read-only round verification."""
    if release is None:
        return None
    published = ReleaseManifest.from_bytes(read_verified(reader, release))
    if not published.tasks:
        return None
    if "corpus" in published.metadata:
        return manifest_for_release(reader, release)
    return TrainingManifest(release, tuple((task.task_id, task.manifest) for task in published.tasks))


def _material_context(reader, reference, *, committed=False):
    material = (FrozenMaterials.from_bytes(read_verified(reader, reference))
                if committed else load_materials(reader, reference))
    request = MaterialRequest.from_bytes(read_verified(reader, material.request))
    return material, tuple(LibraryIndex(reader, ref, LookupPolicy())
                           for ref in (request.library, *request.supplements))


def _source_links(reader, release, material, libraries):
    manifest = ReleaseManifest.from_bytes(read_verified(reader, release))
    selected = {file.content for file in material.documents}
    for reference in thaw_json(manifest.metadata)["dispositions"]:
        disposition = ReleaseDisposition.from_bytes(read_verified(reader, BlobRef.from_dict(reference)))
        source = SourceRecord.from_bytes(read_verified(reader, disposition.source))
        # The exact original document and its attribution belong to C_r.
        # Notices/provenance are retained evidence, not extra Raw examples.
        for library in libraries:
            identifiers = tuple(key for key, unit in library.units.items()
                                if unit.content in source.selected.files)
            if (source.source_id in identifiers
                    and all(library.units[key].content.content in selected for key in identifiers)
                    and replace(source, acquisition=None) == library.source_record(source.source_id, identifiers)):
                break
        else:
            raise RecordError("round-source-mismatch", "constructed task used a source outside the round's shared material")


def load_round_data(reader, head: BlobRef) -> RoundData:
    events = load_chain(reader, head)
    if tuple(event.kind for event in events) != ("round_data_requested", "round_materials_ready", "round_data_ready"):
        raise RecordError("round-data-incomplete", "the shared material/task handoff has not committed")
    request = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(events[0].payload)["request"])))
    materials = BlobRef.from_dict(request["materials"])
    if thaw_json(events[1].payload) != {"materials": materials.as_dict()}:
        raise RecordError("round-data-mismatch", "installed material differs from the request")
    material, libraries = _material_context(reader, materials)
    final = thaw_json(events[-1].payload)
    publication = release = None
    if request["publication"] is not None:
        publication = BlobRef.from_dict(final["publication"])
        published = load_publication(reader, publication)
        release = published.release
        publication_events = load_chain(reader, publication)
        plan = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(publication_events[0].payload)["plan"])))
        expected = ReleaseRequest.from_bytes(canonical_json(request["publication"]))
        if any(plan[key] != expected.as_dict()[key] for key in ("release_id", "policy", "packages", "approvals")):
            raise RecordError("round-publication-mismatch", "task publication differs from the requested packages")
        _source_links(reader, release, material, libraries)
    result = RoundData(head, materials, publication, release)
    if final != {key: value for key, value in result.as_dict().items() if key != "head"}:
        raise RecordError("round-data-mismatch", "material/task result differs from its committed inputs")
    return result


def prepare_round_data(*, materials: BlobRef, publication: ReleaseRequest | None,
                       reader, run: JournalDirectory, material_boundary: BlobRef | None = None) -> RoundData:
    """Publish all requested dispositions and bind their sources to common C_r.

    ``publication=None`` records that no task packages were produced. A nonempty
    publication may admit zero tasks. Neither case changes the acquired corpus
    or invents a training sample; the round's learning policy decides what next.
    An owning round may supply its committed material boundary to transfer the
    retained closure without replaying acquisition. Standalone callers retain
    the full material audit; source linkage and admission run in both paths.
    """
    with run.owner():
        objects, tape = run.objects, Tape(run.journal("round_data"))
        request = objects.put(canonical_json({"materials": materials.as_dict(),
            "publication": None if publication is None else publication.as_dict(),
            **({"material_boundary": material_boundary.as_dict()} if material_boundary is not None else {})}))
        tape.record("round_data_requested", {"request": request.as_dict()}, (request,))
        if tape.events[-1].kind == "round_data_ready":
            tape.take("round_materials_ready")
            tape.take("round_data_ready")
            tape.finish()
            head, _ = run.seal("round_data")
            return committed_round_data(objects, head)[0]

        if tape.cursor < len(tape.events):
            tape.take("round_materials_ready")
            material, libraries = _material_context(objects, materials, committed=material_boundary is not None)
        elif material_boundary is not None:
            from .round_evidence import acquired_prefix

            events, _ = acquired_prefix(reader, material_boundary, verify_artifacts=False)
            if thaw_json(events[-1].payload)["materials"] != materials.as_dict():
                raise RecordError("round-material-boundary-mismatch", "material boundary names another selection")
            closure = tuple(dict.fromkeys(ref for event in events
                            for ref in (BlobRef.of(event.to_bytes()), *event.artifacts)))
            objects.publish_owned(reader, closure)
            material, libraries = _material_context(objects, materials, committed=True)
            tape.record("round_materials_ready", {"materials": materials.as_dict()}, closure)
        else:
            inputs = ReadSet(reader)
            material, libraries = _material_context(inputs, materials)
            inputs.publish(objects)
            tape.record("round_materials_ready", {"materials": materials.as_dict()}, tuple(inputs.references))
        publication_head = release = None
        closure = ()
        if publication is not None:
            destination = JournalDirectory(run.directory / "publication", objects=objects)
            published = publish_release(release_id=publication.release_id, policy=publication.policy,
                packages=publication.packages, approvals=publication.approvals, reader=reader, destination=destination,
                verify_history=material_boundary is None)
            publication_head, closure = destination.seal("publication")
            objects.publish_owned(destination.objects, closure)
            release = published.release
            _source_links(objects, release, material, libraries)

        final = {"materials": materials.as_dict(),
            "publication": None if publication_head is None else publication_head.as_dict(),
            "release": None if release is None else release.as_dict()}
        tape.record("round_data_ready", final, closure)
        tape.finish()
        head, _ = run.seal("round_data")
        return RoundData(head, materials, publication_head, release)


def main():
    import argparse
    import json
    from pathlib import Path

    from behave.core.storage import DirectoryStore

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objects", type=Path, required=True)
    parser.add_argument("--materials", type=Path, required=True, help="JSON reference to frozen C_r")
    parser.add_argument("--publication", type=Path, help="existing ReleaseRequest JSON, or no task packages")
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    result = prepare_round_data(materials=BlobRef.from_dict(json.loads(args.materials.read_text())),
        publication=None if args.publication is None else ReleaseRequest.from_bytes(args.publication.read_bytes()),
        reader=DirectoryStore(args.objects), run=JournalDirectory(args.run))
    print(json.dumps(result.as_dict(), indent=2))


if __name__ == "__main__":
    main()
