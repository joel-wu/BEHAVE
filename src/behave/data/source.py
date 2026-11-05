"""Pure source-reference helpers shared by audit and acquisition preparation.

These parse recorded labels; they do not fetch, execute, or approve sources.
The repository/file exclusion key deliberately ignores revisions and preserves
file-name case. It is not the content identity used by the acquisition corpus.

Source projection stops at every section header, including non-source sections,
so interleaved construction instructions cannot enter the source-only export.
Historical inventory framing and split chunk hashing remain separate policies.
"""

from __future__ import annotations

import re
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from urllib.parse import urlsplit, urlunsplit

from behave.agent.admission import ResourcePool
from behave.agent.attempt import AttemptInterrupted, AttemptResult, AttemptSpec
from behave.agent.cpu_resources import cpu_job
from behave.agent.journal import AttemptJournal
from behave.agent.lifetime import AttemptControl
from behave.agent.model import ModelBackend
from behave.agent.prompting import render, role_messages
from behave.agent.tools import Tool, ToolResult, ToolUnavailable, current_invocation, object_schema
from behave.core.artifacts import BlobRef, ContentReader, FileData, FileRef, read_verified
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, thaw_json, version
from behave.task_execution.feedback import comparison_feedback

from .author_check import check_guidance
from .checks import check_sources
from .contract import ContractRevision, binding_revision, contract_guidance, revise_contract, revision_inputs
from .drafts import (
    LoadedDraft,
    Reconstruction,
    ScopeProposal,
    draft_closure,
    load_draft,
    load_scope,
    load_source_artifact,
)
from .evidence import evidence_files
from .reviews import DraftCheck
from .spec_format import without_behavior_interface

SOURCE_FILES = ("src/behave/data/source.py",)
HEADERS = re.compile(r"^=== (.*?) ===[ \t]*\r?$", re.MULTILINE)
EXCERPT_FOOTER = "Selected excerpt only; complete unchanged file is hash-bound in provenance.archive."


def repo_url(value: str) -> str:
    """Normalize public repository spelling without leaking URL credentials."""
    value = value.strip()
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.split(":", 1)[1]
    elif re.fullmatch(r"[\w.-]+/[\w.-]+", value):
        value = "https://github.com/" + value
    parsed = urlsplit(value)
    if parsed.scheme:
        value = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
    return value.rstrip("/").removesuffix(".git")


def source_key(repository: str, file: str) -> str:
    # A different revision of a held-out file is not an independent resource.
    repo = repository.strip().rstrip("/").removesuffix(".git").lower()
    return f"{repo}:{file.strip().removeprefix('./')}"


def resolve_reference(label: str, registry: dict, provenance: list[dict]) -> tuple[str, str, str]:
    pinned = re.fullmatch(r"(https://github\.com/[^/]+/[^/@]+)@([^:]+):(.+)", label)
    if pinned:
        return repo_url(pinned[1]), pinned[3], pinned[2]
    parsed = urlsplit(label)
    if parsed.hostname == "raw.githubusercontent.com":
        parts = parsed.path.strip("/").split("/")
        return repo_url("/".join(parts[:2])), "/".join(parts[3:]), parts[2]
    for p in provenance:
        file = p.get("file", p.get("path", ""))
        if label == file or label.endswith("/" + file):
            return repo_url(p["repository"]), file, p.get("revision", p.get("commit", ""))
    alias, sep, file = label.partition("/")
    alias, _, revision = alias.partition("@")
    if sep and alias in registry:
        return registry[alias]["repository"], file, revision
    return "", label, revision


def source_blocks(material: str) -> list[tuple[str, str]]:
    """Keep reference sections, never construction preambles/directive sections."""
    marks = list(HEADERS.finditer(material))
    blocks = []
    for i, mark in enumerate(marks):
        label = mark.group(1)
        if label.startswith("reference: "):
            label = label.removeprefix("reference: ")
        elif not label.startswith(("https://", "http://")):
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(material)
        body = material[mark.end():end].strip("\r\n")
        body = body.removesuffix(EXCERPT_FOOTER).rstrip("\r\n")
        if body:
            blocks.append((label, body))
    return blocks


@contextmanager
def settled_source_run(run, configuration, kind, *, control, model_pool):
    if kind not in {"discovery", "selection"}:
        raise ValueError("unknown historical source coordinator")
    if control is not None and type(control) is not AttemptControl:
        raise TypeError("source replay control requires AttemptControl")
    if model_pool is not None and (type(model_pool) is not ResourcePool or
                                  model_pool.policy != configuration.model_admission):
        raise ValueError("source replay pool differs from configured admission")
    journal = run.journal(kind)
    if not journal.directory.exists():
        raise ValueError("legacy discovery/selection agents are retired; use Bash acquisition over the source library")
    with run.owner():
        events = journal.load()
        if not events:
            raise ValueError("legacy discovery/selection agents are retired; use Bash acquisition over the source library")
        expected = {"configuration": BlobRef.of(configuration.to_bytes()).as_dict()}
        if events[0].kind != kind + "_started" or thaw_json(events[0].payload) != expected:
            raise RecordError("source-replay-configuration-mismatch", "saved source configuration differs")
        if events[-1].kind != kind + "_finished":
            raise AttemptInterrupted("legacy-source-run-unfinished")
        head, _ = run.seal(kind)
        yield head


REVIEW_VERSION = "source-review/5"


@dataclass(frozen=True, slots=True)
class Citation:
    file: FileRef
    first: int
    last: int

    def __post_init__(self):
        if type(self.file) is not FileRef:
            raise TypeError("citation requires a pinned file")
        if type(self.first) is not int or type(self.last) is not int or not 1 <= self.first <= self.last:
            raise ValueError("citation requires a nonempty one-based line span")

    def as_dict(self):
        return {"file": self.file.as_dict(), "first": self.first, "last": self.last}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"file", "first", "last"})
        return cls(FileRef.from_dict(row["file"]), row["first"], row["last"])


@dataclass(frozen=True, slots=True)
class CapabilityAssessment:
    capability: int
    conclusion: str
    rationale: str
    citations: tuple[Citation, ...]

    def __post_init__(self):
        if type(self.capability) is not int or self.capability < 0:
            raise ValueError("capability must be a zero-based scope index")
        if self.conclusion not in {"supported", "unsupported", "unclear"}:
            raise ValueError("unknown capability conclusion")
        text(self.rationale, "review rationale")
        if type(self.citations) is not tuple or any(type(c) is not Citation for c in self.citations):
            raise TypeError("citations require Citation values")
        if not self.citations:
            raise ValueError("review needs cited evidence, including for an unclear conclusion")

    def as_dict(self):
        return {"capability": self.capability, "conclusion": self.conclusion, "rationale": self.rationale,
                "citations": [c.as_dict() for c in self.citations]}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"capability", "conclusion", "rationale", "citations"})
        return cls(row["capability"], row["conclusion"], row["rationale"],
                   tuple(Citation.from_dict(c) for c in array(row["citations"])))


@dataclass(frozen=True, slots=True)
class SourceReview:
    draft: BlobRef
    guidance: BlobRef
    assessments: tuple[CapabilityAssessment, ...]
    findings: tuple[str, ...]
    check: BlobRef | None = None
    reconstruction: BlobRef | None = None
    builder_feedback: str = ""
    implementer_feedback: str = ""
    # Archived process-only example receipts. New reviews use the shared
    # DraftCheck and normal tool journal; this field preserves old records.
    challenges: tuple[BlobRef, ...] = ()
    rtl_findings: BlobRef | None = None
    contract_revision: ContractRevision | None = None

    def __post_init__(self):
        validate_report_fields(self.draft, self.guidance, self.assessments, self.findings)
        if self.check is not None and type(self.check) is not BlobRef:
            raise TypeError("review check must be an immutable reference")
        if self.reconstruction is not None and type(self.reconstruction) is not BlobRef:
            raise TypeError("review reconstruction must be an immutable reference")
        if any(type(value) is not str for value in (self.builder_feedback, self.implementer_feedback)):
            raise TypeError("role-specific repair messages must be text")
        if type(self.challenges) is not tuple or any(type(ref) is not BlobRef for ref in self.challenges):
            raise TypeError("challenges must reference tool journal events")
        if self.reconstruction is None and (self.builder_feedback or self.implementer_feedback or self.challenges):
            raise ValueError("mediated feedback requires its reconstruction")
        if self.rtl_findings is not None and (type(self.rtl_findings) is not BlobRef or self.reconstruction is None):
            raise ValueError("RTL findings require a pinned candidate and reconstruction")
        if self.contract_revision is not None and (type(self.contract_revision) is not ContractRevision
                                                  or self.reconstruction is None):
            raise ValueError("contract revision requires the original reconstruction")

    def to_bytes(self):
        row = {"schema": "behave.source-review/1", "draft": self.draft.as_dict(),
            "guidance": self.guidance.as_dict(), "assessments": [a.as_dict() for a in self.assessments],
            "findings": list(self.findings)}
        if self.check is not None:
            row.update(schema="behave.source-review/2", check=self.check.as_dict())
        if self.reconstruction is not None:
            row.update(schema="behave.source-review/3", reconstruction=self.reconstruction.as_dict(),
                       check=None if self.check is None else self.check.as_dict(),
                       builder_feedback=self.builder_feedback, implementer_feedback=self.implementer_feedback,
                       challenges=[ref.as_dict() for ref in self.challenges])
        if self.rtl_findings is not None:
            row.update(schema="behave.source-review/4", rtl_findings=self.rtl_findings.as_dict())
        if self.contract_revision is not None:
            row.update(schema="behave.source-review/5", contract_revision=self.contract_revision.as_dict(),
                       rtl_findings=None if self.rtl_findings is None else self.rtl_findings.as_dict())
        return canonical_json(row)

    @property
    def supported(self) -> bool:
        """Disposition only; callers must first validate the complete capability set."""
        return (bool(self.assessments) and not self.findings and not self.builder_feedback.strip()
                and not self.implementer_feedback.strip() and all(a.conclusion == "supported" for a in self.assessments))

    @property
    def quoted_feedback_field(self) -> str | None:
        return next((name for name in ("builder_feedback", "implementer_feedback")
                     if getattr(self, name).strip() == '""'), None)

    @classmethod
    def from_bytes(cls, payload):
        value = parse_json(payload)
        revised = value.get("schema") == "behave.source-review/5"
        rtl = revised or value.get("schema") == "behave.source-review/4"
        mediated = rtl or value.get("schema") == "behave.source-review/3"
        checked = mediated or value.get("schema") == "behave.source-review/2"
        row = fields(value, {"schema", "draft", "guidance", "assessments", "findings"}
                     | ({"check"} if checked else set())
                     | ({"reconstruction", "builder_feedback", "implementer_feedback", "challenges"} if mediated else set())
                     | ({"rtl_findings"} if rtl else set())
                     | ({"contract_revision"} if revised else set()))
        version(row["schema"], f"behave.source-review/{5 if revised else 4 if rtl else 3 if mediated else 2 if checked else 1}")
        result = cls(BlobRef.from_dict(row["draft"]), BlobRef.from_dict(row["guidance"]),
                     tuple(CapabilityAssessment.from_dict(a) for a in array(row["assessments"])),
                     tuple(array(row["findings"])), BlobRef.from_dict(row["check"]) if row.get("check") is not None else None,
                     BlobRef.from_dict(row["reconstruction"]) if mediated else None,
                     row.get("builder_feedback", ""), row.get("implementer_feedback", ""),
                     tuple(BlobRef.from_dict(ref) for ref in array(row.get("challenges", []))),
                     BlobRef.from_dict(row["rtl_findings"]) if row.get("rtl_findings") is not None else None,
                     ContractRevision.from_dict(row["contract_revision"]) if revised else None)
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "source review must be canonical")
        return result


def validate_report_fields(subject, guidance, assessments, findings) -> None:
    if type(subject) is not BlobRef or type(guidance) is not BlobRef:
        raise TypeError("review requires exact subject and guidance references")
    if type(assessments) is not tuple or any(type(a) is not CapabilityAssessment for a in assessments):
        raise TypeError("assessments require CapabilityAssessment values")
    if len({a.capability for a in assessments}) != len(assessments):
        raise ValueError("duplicate capability assessment")
    if type(findings) is not tuple:
        raise TypeError("findings require a tuple")
    for finding in findings:
        text(finding, "finding")


def parse_assessments(values, by_path) -> tuple[CapabilityAssessment, ...]:
    """Model-facing paths become references to the exact read-only review view."""
    assessments = []
    for value in array(values):
        item = fields(value, {"capability", "conclusion", "rationale", "citations"})
        citations = []
        for value in array(item["citations"]):
            c = fields(value, {"path", "first", "last"})
            citations.append(Citation(by_path[c["path"]], c["first"], c["last"]))
        assessments.append(CapabilityAssessment(item["capability"], item["conclusion"], item["rationale"],
                                                tuple(citations)))
    return tuple(assessments)


def load_review_guidance(reader: ContentReader, guidance: BlobRef) -> bytes:
    """Validate trusted review configuration before any role consumes a turn."""
    body = read_verified(reader, guidance)
    try:
        content = body.decode("utf-8")
    except UnicodeError as error:
        raise RecordError("review-guidance-not-text", "review obligations must be UTF-8 text") from error
    if not content.strip():
        raise RecordError("review-guidance-empty", "review obligations must be explicit")
    return body


def review_check(reader: ContentReader, draft: BlobRef, check: BlobRef):
    """Load the same B2/check pair for the reviewer workspace and process export."""
    report = DraftCheck.from_bytes(read_verified(reader, check))
    reconstruction = Reconstruction.from_bytes(read_verified(reader, report.reconstruction))
    if report.draft != draft or reconstruction.draft != draft or reconstruction.behavior is None:
        raise RecordError("review-check-mismatch", "review needs a concrete check and reconstruction of this draft")
    source = load_source_artifact(reader, reconstruction.behavior)
    return report, reconstruction, source


def review_revision(draft: BlobRef, guidance: BlobRef, check: BlobRef | None = None,
                    reconstruction: BlobRef | None = None, rtl_findings: BlobRef | None = None) -> str:
    """One submission binding for execution and admission; the check identifies B2."""
    binding = {"draft": draft.as_dict(), "guidance": guidance.as_dict()}
    if check is not None:
        binding["check"] = check.as_dict()
    if reconstruction is not None:
        binding["reconstruction"] = reconstruction.as_dict()
    if rtl_findings is not None:
        binding["rtl_findings"] = rtl_findings.as_dict()
    return REVIEW_VERSION + ":" + BlobRef.of(canonical_json(binding)).sha256


def review_inputs(reader: ContentReader, draft: BlobRef, guidance: BlobRef, *,
                  check: BlobRef | None = None, reconstruction: BlobRef | None = None,
                  rtl_findings: BlobRef | None = None,
                  ) -> tuple[LoadedDraft, ScopeProposal, tuple[FileData, ...]]:
    body = load_review_guidance(reader, guidance)
    loaded = load_draft(reader, draft)
    scope, source, source_files = load_scope(reader, loaded.record.scope)

    def file(path, data):
        return FileData(FileRef(path, BlobRef.of(data)), data)

    task = loaded.record.task
    run_policy = read_verified(reader, task.run_policy)
    refs = (task.spec, task.contract, *task.public_files, *task.reference_behavior.files)
    task_files = {r.path: loaded.file(r.path).data for r in refs}

    def selection(root, artifact):
        return {"files": [root + "/" + ref.path for ref in artifact.files],
                "entry_file": root + "/" + artifact.entry_file, "entry_symbol": artifact.entry_symbol}

    sim_request = {"behavior": selection("draft", task.reference_behavior),
                   "spec": "draft/" + task.spec.path,
                   "contract": "draft/" + task.contract.path, "run_policy": "review/run_policy.json"}
    files = (file("review/guidance.md", body), file("review/scope.json", scope.to_bytes()),
             file("review/source.json", source.to_bytes()),
             file("review/rtl_only_spec.md", without_behavior_interface(
                 task_files[task.spec.path].decode("utf-8")).encode("utf-8")),
             *(file("source/" + f.reference.path, f.data) for f in source_files),
             *(file("draft/" + path, data) for path, data in sorted(task_files.items())))
    if scope.brief is not None:
        files += (file("review/construction_brief.json", read_verified(reader, scope.brief)),)
    if loaded.record.adaptations is not None:
        files += (file("review/adaptations.json", canonical_json(list(loaded.record.adaptations))),)
    if check is not None:
        report, record, source = review_check(reader, draft, check)
        if reconstruction is not None and report.reconstruction != reconstruction:
            raise RecordError("review-check-mismatch", "check refers to another reconstruction")
        files += (file("review/check.json", report.to_bytes()),
                  file("review/reconstruction.json", record.to_bytes()),
                  file("review/implementations.json", canonical_json({
                      "builder": {"root": "draft", "artifact": task.reference_behavior.as_dict()},
                      "implementer": {"root": "reconstruction", "artifact": source.artifact.as_dict()},
                      "run_policy": None if run_policy.strip() == b"null" else parse_json(run_policy),
                  })),
                  *(file("reconstruction/" + f.reference.path, f.data) for f in source.files))
        files += evidence_files("review/evidence", report.to_bytes(), report.comparison.result.evidence, reader)
        _, feedback_files = comparison_feedback(report.comparison.result, reader, prefix="review/comparison")
        files += feedback_files
        replay = report.comparison.result.detail.get("replay_pool")
        if replay is not None:
            ref = BlobRef.from_dict(dict(replay))
            files += (FileData(FileRef("review/replay_stimuli.json", ref), read_verified(reader, ref)),)
        sim_request.update(behavior=selection("reconstruction", source.artifact),
                           reference_behavior=selection("draft", task.reference_behavior))
    elif reconstruction is not None:
        record = Reconstruction.from_bytes(read_verified(reader, reconstruction))
        if record.draft != draft or record.behavior is not None:
            raise RecordError("review-check-mismatch", "findings-only review requires this draft's findings")
        files += (file("review/reconstruction.json", record.to_bytes()),)
    if rtl_findings is not None:
        files += rtl_review_files(reader, draft, rtl_findings)
    files += (*contract_guidance("review/"),
              *check_guidance("review/", template="synthesis/review_check"),
              file("review/sim_check.json", canonical_json(sim_request)),
              file("review/run_policy.json", run_policy))
    return loaded, scope, files


def rtl_review_files(reader, draft, reference):
    """Expose the RTL author's findings and selected code, not its private workspace."""
    from .rtl import RtlCandidate

    candidate = RtlCandidate.from_bytes(read_verified(reader, reference))
    if not candidate.findings:
        raise RecordError("review-rtl-mismatch", "RTL findings must concern this draft")
    corrected = ()
    if candidate.draft != draft:
        # The optional RTL branch may have used a prior Reviewer's binding
        # correction while the author/B2 history still names the original draft.
        loaded = binding_revision(reader, draft, candidate.draft)
        contract = loaded.file(loaded.record.task.contract.path)
        corrected = (FileData(FileRef("review/rtl_contract.json", contract.reference.content), contract.data),)
    files = () if candidate.rtl is None else candidate.rtl.files
    return (FileData(FileRef("review/rtl_findings.json", reference), candidate.to_bytes()),
            *corrected, *(FileData(FileRef("rtl/" + item.path, item.content), read_verified(reader, item.content))
              for item in files))


def validate_review(review: SourceReview, scope: ScopeProposal, files: tuple[FileData, ...]) -> None:
    # Every supplied file is already bound to this read-only review view,
    # including comparison reports and replay diagnostics. These may supplement
    # the mandatory source/draft citations; they cannot replace them.
    validate_assessments(review.assessments, scope, files, prefixes={"source", "draft"},
                         optional_prefixes={f.reference.path.split("/", 1)[0] for f in files})


def validate_assessments(assessments: tuple[CapabilityAssessment, ...], scope: ScopeProposal,
                         files: tuple[FileData, ...], *, prefixes: set[str],
                         optional_prefixes: frozenset[str] | set[str] = frozenset()) -> None:
    """Shared citation rules; semantic adequacy remains the independent reviewer's job."""
    if (len(assessments) != len(scope.capabilities)
            or {a.capability for a in assessments} != set(range(len(scope.capabilities)))):
        raise RecordError("review-capabilities-incomplete", "assess every declared capability exactly once")
    available = {f.reference: f.data for f in files if f.reference.path.split("/", 1)[0] in prefixes | optional_prefixes}
    line_counts = {}
    for assessment in assessments:
        cited = set()
        for citation in assessment.citations:
            payload = available.get(citation.file)
            location = f"{citation.file.path}:{citation.first}-{citation.last}"
            if payload is None:
                roots = ", ".join(root + "/" for root in sorted(prefixes | optional_prefixes))
                raise RecordError("review-citation-invalid",
                    f"capability {assessment.capability}: {location} is not a pinned citation input; "
                    f"use the supplied files under {roots}")
            if citation.file not in line_counts:
                line_counts[citation.file] = len(payload.decode("utf-8").splitlines())
            if citation.last > line_counts[citation.file]:
                raise RecordError("review-citation-invalid",
                    f"capability {assessment.capability}: {location} exceeds the file's "
                    f"{line_counts[citation.file]} lines; use one-based line numbers from this file")
            cited.add(citation.file.path.split("/", 1)[0])
        if not prefixes <= cited:
            missing = ", ".join(root + "/" for root in sorted(prefixes - cited))
            raise RecordError("review-citation-incomplete",
                f"capability {assessment.capability}: missing citations under {missing}; "
                "cite each required input group")


def source_review_submission_schema():
    """The runtime and prompt preview share one terminal-submission schema."""
    citation_schema = object_schema({"path": {"type": "string"}, "first": {"type": "integer", "minimum": 1},
                                     "last": {"type": "integer", "minimum": 1}})
    assessment_schema = object_schema({"capability": {"type": "integer", "minimum": 0},
        "conclusion": {"enum": ["supported", "unsupported", "unclear"]}, "rationale": {"type": "string"},
        "citations": {"type": "array", "items": citation_schema}})
    return object_schema({"assessments": {"type": "array", "items": assessment_schema},
                          "findings": {"type": "array", "items": {"type": "string"}},
                          "builder_feedback": {"type": "string"}, "implementer_feedback": {"type": "string"},
                          "revised_contract": {"type": ["string", "null"], "description":
                              "Optional workspace path to a contract with physical pin bindings corrected; "
                              "the public specification and both Behaviors remain unchanged."}},
                         required=("assessments", "findings", "builder_feedback", "implementer_feedback",
                                   "revised_contract"))


def run_source_review(*, draft: BlobRef, guidance: BlobRef, reader: ContentReader, journal: AttemptJournal,
                      backend: ModelBackend, spec: AttemptSpec, control: AttemptControl | None = None,
                      model_admission: ResourcePool | None = None, check: BlobRef | None = None,
                      reconstruction: BlobRef | None = None,
                      rtl_findings: BlobRef | None = None,
                      cpu_endpoint: str | None = None,
                      development_tools: tuple[Tool, ...] = ()) -> AttemptResult:
    """Pinned sources and public Sim evidence, plus a private review workspace.

    The shared B/B check is supplied by construction. Development tools may
    provide additional checks, but no particular tool call is a review gate.
    """
    from behave.agent.loop import run_attempt

    if spec.role != "source_reviewer" or any(t.terminal for t in development_tools):
        raise ValueError("source review requires source_reviewer role and nonterminal development tools")
    _, scope, files = review_inputs(reader, draft, guidance, check=check, reconstruction=reconstruction,
                                   rtl_findings=rtl_findings)
    by_path = {f.reference.path: f.reference for f in files}

    def submit(workspace, arguments):
        row = fields(arguments, {"assessments", "findings"}
                     | (set(arguments) & {"builder_feedback", "implementer_feedback", "revised_contract"}))
        try:
            review = SourceReview(draft, guidance, parse_assessments(row["assessments"], by_path),
                                  tuple(array(row["findings"])), check, reconstruction,
                                  row.get("builder_feedback", ""), row.get("implementer_feedback", ""),
                                  rtl_findings=rtl_findings)
            validate_review(review, scope, files)
            if review.quoted_feedback_field is not None:
                return ToolResult({"error": "review-feedback-quoted-empty", "detail":
                    f"{review.quoted_feedback_field} contains two literal quote characters, not an empty string. "
                    "Submit an actual empty JSON string if no repair is requested, or describe "
                    "the required correction. This submission has not dispatched a repair."})
            if (reconstruction is not None and review.findings
                    and not review.builder_feedback.strip() and not review.implementer_feedback.strip()):
                return ToolResult({"error": "review-repair-recipient-missing", "detail":
                    "Unresolved findings need actionable builder_feedback for task/spec/interface/B1 changes "
                    "or implementer_feedback for B2-only changes. Choose the recipient; findings alone "
                    "do not dispatch a repair. Put resolved or non-blocking observations in assessment "
                    "rationales instead. If required source information is unavailable, describe that "
                    "gap to Builder; do not invent a repair or claim approval."})
            extra = ()
            if row.get("revised_contract") is not None:
                if reconstruction is None or check is None:
                    return ToolResult({"error": "contract-repair-needs-pair",
                        "detail": "A direct contract repair needs the existing B1/B2 pair."})
                if review.builder_feedback.strip() or review.implementer_feedback.strip() or review.findings:
                    return ToolResult({"error": "contract-repair-with-unresolved-findings",
                        "detail": "Resolve the binding correction here, or route substantive defects to their author. "
                                  "Do not submit a direct repair together with an author repair request."})
                original = Reconstruction.from_bytes(read_verified(reader, reconstruction))
                if workspace.store is not reader:
                    workspace.store.publish(reader, (*draft_closure(reader, draft), reconstruction,
                        *(f.content for f in original.behavior.files)))
                revised = revise_contract(workspace.store, workspace.store, draft,
                                          workspace.read(row["revised_contract"]))
                invocation = current_invocation()
                repair_journal = AttemptJournal(journal.directory.parent /
                    (journal.directory.name + "_contract_checks") / str(invocation.sequence), workspace.store)
                with cpu_job(cpu_endpoint) as lease, ExitStack() as resources:
                    pool = None
                    if lease is not None:
                        from behave.sim import api

                        pool = resources.enter_context(api.CpuPool(capacity=len(lease.cpus))).endpoint.as_dict()
                    comparison = check_sources(revision_inputs(workspace.store, draft, reconstruction, revised),
                        journal=repair_journal, cpu_pool=pool,
                        cancelled=(lambda: False) if control is None else control.cancelled)
                    if lease is not None:
                        lease.cleanup = comparison.result.cleanup
                _, check_refs = repair_journal.seal()
                extra = (*draft_closure(workspace.store, revised), *check_refs)
                if not comparison.result.cleanup.quiescent:
                    raise ToolUnavailable("contract-repair-cleanup-unconfirmed", artifacts=extra)
                if comparison.result.score != 1:
                    reply, reports = comparison_feedback(comparison.result, workspace.store,
                        prefix=f"review/contract_check/{invocation.sequence}")
                    for file in reports:
                        workspace.write(file.reference.path, file.data)
                    return ToolResult({"error": "contract-repair-check-failed", "comparison": reply,
                        "detail": "The revised contract did not pass automatic checks. Read the report and correct "
                                  "it, or send actionable feedback to the appropriate author."},
                        (*extra, *(f.reference.content for f in reports)))
                review = replace(review, contract_revision=ContractRevision(revised, comparison))
            payload = review.to_bytes()
        except (KeyError, ValueError, TypeError, UnicodeError) as error:
            return ToolResult({"error": getattr(error, "code", "review-arguments-invalid"),
                               "detail": str(error)})
        reference = workspace.store.put(payload)
        return ToolResult({"source_review": reference.as_dict()}, (reference, *extra), True)

    tool = Tool("submit_source_review", render("synthesis/source_reviewer/submit"),
                source_review_submission_schema(), submit,
                review_revision(draft, guidance, check, reconstruction, rtl_findings), terminal=True)
    protected = tuple(sorted({*spec.read_only_paths, *by_path}))
    messages = role_messages("synthesis/source_reviewer")
    if rtl_findings is not None:
        messages += ({"role": "user", "content": render("synthesis/source_reviewer/rtl_findings")},)
    return run_attempt(journal=journal, backend=backend, spec=replace(spec, read_only_paths=protected),
        initial_files=files, tools=(*development_tools, tool),
        control=control, model_admission=model_admission,
        initial_messages=messages)
