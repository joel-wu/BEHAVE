"""Atomic release commit after admission and verified closure installation.

Only trusted operators/coordinators call this API. It does not fetch sources,
execute models, judge artifacts, select a dataset split, or move legacy files.
Every requested package receives a disposition; insufficient evidence is kept
pending. A named run directory is immutable once committed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from behave.agent.journal import load_chain
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, ContentReader, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, thaw_json
from behave.core.storage import DirectoryStore

from .admission import Admission, admit_package
from .published import PublishedReleaseReader, ReleaseManifest, TaskRevision
from .release_records import HumanApproval, ReleasePolicy, ReleaseRequest

PUBLISHER = "behave.dataset-publisher/1"


@dataclass(frozen=True, slots=True)
class Publication:
    release: BlobRef
    head: BlobRef


def audit_release(request: ReleaseRequest, reader: ContentReader, *,
                  verify_history: bool = True) -> tuple[Admission, ...]:
    """Read-only preflight, shared by CLI, publisher and offline verification."""
    if type(request) is not ReleaseRequest:
        raise TypeError("admission requires an explicit ReleaseRequest")
    rules = ReleasePolicy.from_bytes(read_verified(reader, request.policy))
    approved = {}
    for ref in request.approvals:
        approval = HumanApproval.from_bytes(read_verified(reader, ref))
        if (
            approval.policy != request.policy
            or approval.package not in request.packages
            or approval.package in approved
        ):
            raise RecordError("publication-approval-mismatch", "approval is duplicate or outside this request")
        approved[approval.package] = ref
    admissions = tuple(
        admit_package(package=ref, policy=request.policy, reader=reader, approval=approved.get(ref),
                      verify_history=verify_history)
        for ref in request.packages
    )
    task_ids = [admission.disposition.task_id for admission in admissions]
    if len(set(task_ids)) != len(task_ids):
        raise RecordError("publication-task-conflict", "choose one package per task id, explicitly")
    if rules.corpus is not None:
        from .corpus import guard_admissions

        admissions = guard_admissions(reader, rules.corpus, admissions)
    return admissions


def release_manifest(
    request: ReleaseRequest, rules: ReleasePolicy, admissions: tuple[Admission, ...]
) -> ReleaseManifest:
    """Pure manifest projection; authority comes from audit_release's evidence."""
    revisions = tuple(
        TaskRevision(a.task.task_id, BlobRef.of(a.task.to_bytes()))
        for a in admissions
        if a.disposition.outcome == "include"
    )
    return ReleaseManifest(
        request.release_id,
        revisions,
        request.policy,
        {
            "publisher": PUBLISHER,
            "assurance": rules.mode,
            "dispositions": [BlobRef.of(a.disposition.to_bytes()).as_dict() for a in admissions],
            "requested": len(admissions),
            "included": len(revisions),
            "pending": len(admissions) - len(revisions),
            **({"corpus": rules.corpus.as_dict()} if rules.corpus is not None else {}),
        },
    )


def publish_release(
    *,
    release_id: str,
    policy: BlobRef,
    packages: tuple[BlobRef, ...],
    reader: ContentReader,
    destination: JournalDirectory,
    approvals: tuple[BlobRef, ...] = (),
    publish_inputs: Callable[[DirectoryStore, tuple[BlobRef, ...]], None] | None = None,
    verify_history: bool = True,
) -> Publication:
    """Install the exact evidence closure before exposing a terminal release head.

    Copies may survive a failed attempt; they do not constitute a release commit.
    Recovery reuses the same plan and bytes. A different policy/package/approval
    needs a new run, never a force-overwrite of this one.
    Trusted coordinators can skip old transcript-body readback while retaining
    the same handoff checks, dispositions and complete evidence closure.
    """
    request = ReleaseRequest(release_id, policy, packages, approvals)
    if type(destination) is not JournalDirectory:
        raise TypeError("publication requires a JournalDirectory")
    packages, approvals = request.packages, request.approvals
    with destination.owner():
        objects = destination.objects
        plan = objects.put(
            canonical_json(
                {
                    "publisher": PUBLISHER,
                    "release_id": release_id,
                    "chunk_refs": defaults.PUBLICATION_CHUNK_REFS,
                    "policy": policy.as_dict(),
                    "packages": [r.as_dict() for r in packages],
                    "approvals": [r.as_dict() for r in approvals],
                }
            )
        )
        tape = Tape(destination.journal("publication"))
        resumed = bool(tape.events)
        if resumed:
            tape.record("publication_started", {"plan": plan.as_dict()})
        if resumed and tape.events[-1].kind == "publication_finished":
            # Loading the journal verifies every installed content chunk. The
            # committed release is self-contained even if staging is offline.
            from .publication import load_publication

            head, _ = destination.seal("publication")
            return load_publication(objects, head, verify_history=verify_history)

        rules = ReleasePolicy.from_bytes(read_verified(reader, policy))
        if not resumed:
            tape.record("publication_started", {"plan": plan.as_dict()}, (plan,))

        # Validate every subject before copying a release, and keep pending rows.
        admissions = audit_release(request, reader, verify_history=verify_history)
        closure = tuple(dict.fromkeys(ref for admission in admissions for ref in admission.closure))
        if publish_inputs is not None:
            publish_inputs(objects, closure)
        elif isinstance(reader, DirectoryStore):
            objects.publish_owned(reader, closure)
        else:
            objects.publish(reader, closure)
        dispositions = []
        for admission in admissions:
            dispositions.append(objects.put(admission.disposition.to_bytes()))
            if admission.disposition.outcome == "include":
                objects.put(admission.task.to_bytes())
        manifest = release_manifest(request, rules, admissions)
        revisions = manifest.tasks
        release = objects.put(manifest.to_bytes())
        published = PublishedReleaseReader(objects, release)
        for revision in revisions:
            # Exercise the very reader eval/train will use, without importing code.
            published.load(revision.task_id)
        # Install and journal the corpus closure in batches before committing.
        references = tuple(
            sorted({release, *dispositions, *(r.manifest for r in revisions), *closure}, key=lambda ref: ref.sha256)
        )
        for offset in range(0, len(references), defaults.PUBLICATION_CHUNK_REFS):
            chunk = references[offset : offset + defaults.PUBLICATION_CHUNK_REFS]
            digest = BlobRef.of(canonical_json([r.as_dict() for r in chunk])).sha256
            tape.record("content_installed", {"offset": offset, "count": len(chunk), "digest": digest}, chunk)
        terminal = tape.record("publication_finished", {"release": release.as_dict()}, (release,))
        tape.finish()
        return Publication(release, BlobRef.of(terminal.to_bytes()))


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise RecordError("publication-evidence-mismatch", detail)


def load_publication(reader: ContentReader, head: BlobRef, *, verify_history: bool = True) -> Publication:
    events = load_chain(reader, head, verify_artifacts=verify_history)
    _require(
        len(events) >= 3 and events[0].kind == "publication_started" and events[-1].kind == "publication_finished",
        "publication has no complete commit",
    )
    plan_ref = BlobRef.from_dict(fields(thaw_json(events[0].payload), {"plan"})["plan"])
    _require(events[0].artifacts == (plan_ref,), "publication plan is not declared")
    plan_bytes = read_verified(reader, plan_ref)
    row = fields(parse_json(plan_bytes), {"publisher", "release_id", "chunk_refs", "policy", "packages", "approvals"})
    _require(row["publisher"] == PUBLISHER and canonical_json(row) == plan_bytes, "unknown publication plan")
    width = row["chunk_refs"]
    _require(type(width) is int and width > 0, "invalid publication chunk size")
    request = ReleaseRequest.from_bytes(
        canonical_json(
            {
                "schema": "behave.release-request/1",
                **{key: row[key] for key in ("release_id", "policy", "packages", "approvals")},
            }
        )
    )
    admissions = audit_release(request, reader, verify_history=verify_history)
    rules = ReleasePolicy.from_bytes(read_verified(reader, request.policy))
    manifest = release_manifest(request, rules, admissions)
    release = BlobRef.of(manifest.to_bytes())
    _require(
        thaw_json(events[-1].payload) == {"release": release.as_dict()} and events[-1].artifacts == (release,),
        "release differs from its admitted tasks and dispositions",
    )
    _require(read_verified(reader, release) == manifest.to_bytes(), "release bytes differ")

    references = {release, *(revision.manifest for revision in manifest.tasks)}
    for admission in admissions:
        references.update(admission.closure)
        disposition = BlobRef.of(admission.disposition.to_bytes())
        _require(read_verified(reader, disposition) == admission.disposition.to_bytes(), "disposition differs")
        references.add(disposition)
    ordered = tuple(sorted(references, key=lambda ref: ref.sha256))
    chunks = tuple(ordered[offset : offset + width] for offset in range(0, len(ordered), width))
    _require(len(events) == len(chunks) + 2, "publication content chunks are missing or duplicated")
    for index, (event, chunk) in enumerate(zip(events[1:-1], chunks, strict=True)):
        digest = BlobRef.of(canonical_json([ref.as_dict() for ref in chunk])).sha256
        _require(
            event.kind == "content_installed"
            and event.artifacts == chunk
            and thaw_json(event.payload) == {"offset": index * width, "count": len(chunk), "digest": digest},
            "publication content closure differs from the audited evidence",
        )
    return Publication(release, head)
