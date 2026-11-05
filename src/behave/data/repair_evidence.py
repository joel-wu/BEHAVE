"""Bounded, parent-bound repair evidence shared by process launch and role tools."""

from behave.core.artifacts import read_verified
from behave.core.records import RecordError
from behave.task_execution.feedback import comparison_feedback

from .drafts import Reconstruction
from .evidence import evidence_files
from .reviews import DraftCheck
from .source import SourceReview, review_inputs, validate_review


def _record(reader, ref, kind):
    return kind.from_bytes(read_verified(reader, ref))


def repair_closure(inputs, reader):
    if inputs.reader_findings is not None:
        record = _record(reader, inputs.reader_findings, Reconstruction)
        if record.draft != inputs.parent or record.behavior is not None:
            raise RecordError("repair-evidence-mismatch", "findings-only report must belong to the parent draft")
        return (inputs.reader_findings,)
    if inputs.check is None:
        return ()
    check = _record(reader, inputs.check, DraftCheck)
    reconstruction = _record(reader, check.reconstruction, Reconstruction)
    if check.draft != inputs.parent or reconstruction.draft != inputs.parent or reconstruction.behavior is None:
        raise RecordError("repair-evidence-mismatch", "check belongs to another parent draft")
    refs = (inputs.check, check.reconstruction, check.comparison.inputs, check.comparison.event,
            *(f.content for f in reconstruction.behavior.files), *check.comparison.result.closure)
    if inputs.source_review is not None:
        review = _record(reader, inputs.source_review, SourceReview)
        if review.check is not None:
            raise RecordError("repair-evidence-needs-mediation", "joint review cannot be forwarded as raw author feedback")
        if review.draft != inputs.parent:
            raise RecordError("repair-evidence-mismatch", "source review belongs to another parent draft")
        _, scope, files = review_inputs(reader, review.draft, review.guidance)
        validate_review(review, scope, files)
        refs += (inputs.source_review, review.guidance,
                 *(c.file.content for a in review.assessments for c in a.citations))
    for ref in dict.fromkeys(refs):
        read_verified(reader, ref)
    return tuple(dict.fromkeys(refs))


def repair_files(inputs, reader):
    """Parent-bound diagnostics for Bash, without another reading capability."""
    files = ()
    if inputs.reader_findings is not None:
        record = _record(reader, inputs.reader_findings, Reconstruction)
        files += evidence_files(".construction/reader_findings", record.to_bytes(), (), reader)
    if inputs.check is not None:
        report = _record(reader, inputs.check, DraftCheck)
        files += evidence_files(".construction/check", report.to_bytes(), report.comparison.result.evidence, reader)
        _, feedback_files = comparison_feedback(report.comparison.result, reader, prefix=".construction/comparison")
        files += feedback_files
    if inputs.source_review is not None:
        report = _record(reader, inputs.source_review, SourceReview)
        refs = (report.guidance, *dict.fromkeys(c.file.content for a in report.assessments for c in a.citations))
        files += evidence_files(".construction/source_review", report.to_bytes(), refs, reader)
    return files
