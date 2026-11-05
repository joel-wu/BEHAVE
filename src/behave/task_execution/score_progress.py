"""Read a published score prefix without claiming worker or analysis completion.

This is evidence from the trusted judge coordinator, not a new scoring rule or
a takeover token. The immutable head may be exported while analysis runs. A
pending dispatch still requires external reconciliation/fencing after a crash.
"""

from dataclasses import replace

from behave.agent.journal import load_chain
from behave.core.artifacts import BlobRef, ContentReader, read_verified, validate_sha256_digest
from behave.core.records import fields, parse_json, thaw_json, version

from .capture import FrozenSubmission
from .judge_policy import JudgePolicy
from .judgment import ComponentJudgment


def validate_decision(result: ComponentJudgment) -> None:
    if type(result) is not ComponentJudgment:
        raise TypeError("score publication requires ComponentJudgment")
    if result.score is None or result.analysis is not None or not result.cleanup.quiescent:
        raise ValueError("score publication requires a decided concrete result")
    validate_sha256_digest(result.detail.get("evaluation"))


def decision_matches(decided: ComponentJudgment, final: ComponentJudgment) -> bool:
    """Analysis and lifecycle receipts may extend, but never rewrite, a score."""
    return (final.evidence[:len(decided.evidence)] == decided.evidence
            and replace(final, analysis=None, evidence=decided.evidence, cleanup=decided.cleanup,
                        elapsed_seconds=decided.elapsed_seconds) == decided)


def load_score_progress(reader: ContentReader, head: BlobRef) -> dict:
    events = load_chain(reader, head)
    if len(events) < 3 or events[0].kind != "judge_started" or events[-1].kind != "judge_score_decided":
        raise ValueError("expected a decided-score journal prefix")
    if events[-2].kind != "judge_requested":
        raise ValueError("score must immediately follow its dispatch")
    identity = BlobRef.from_dict(fields(thaw_json(events[0].payload), {"inputs"})["inputs"])
    dispatch = fields(thaw_json(events[-2].payload), {"component", "dispatch", "inputs"})
    if BlobRef.from_dict(dispatch["inputs"]) != identity or dispatch["component"] not in {"behavior", "rtl"}:
        raise ValueError("score dispatch belongs to different judge inputs")
    inputs = fields(parse_json(read_verified(reader, identity)), {
        "schema", "backend", "admission", "submission", "task_id", "release", "revision",
        "contract", "run_policy", "judge_policy", "references"})
    version(inputs["schema"], "behave.judge-inputs/2")
    submission = BlobRef.from_dict(inputs["submission"])
    frozen = FrozenSubmission.from_bytes(read_verified(reader, submission))
    if (frozen.task_id, frozen.release, frozen.revision) != (
            inputs["task_id"], BlobRef.from_dict(inputs["release"]), BlobRef.from_dict(inputs["revision"])):
        raise ValueError("score belongs to another task revision")
    if getattr(frozen, dispatch["component"]).artifact is None:
        raise ValueError("missing source cannot produce an analysis checkpoint")
    policy = JudgePolicy.from_bytes(read_verified(reader, BlobRef.from_dict(inputs["judge_policy"])))
    if policy.analysis is None or type(dispatch["dispatch"]) is not int or not 0 <= dispatch["dispatch"] <= policy.max_retries:
        raise ValueError("invalid score publication phase")
    result = ComponentJudgment.from_dict(fields(thaw_json(events[-1].payload), {"result"})["result"])
    validate_decision(result)
    if not set(result.closure) <= set(events[-1].artifacts):
        raise ValueError("score evidence is outside the published closure")
    return {"schema": "behave.score-progress/1", "head": head.as_dict(), "submission": submission.as_dict(),
            "task_id": frozen.task_id, "component": dispatch["component"], "dispatch": dispatch["dispatch"],
            "score": result.score, "code": result.code, "evaluation": result.detail["evaluation"],
            "evidence": [ref.as_dict() for ref in result.evidence], "phase": "score-decided",
            "execution_settled": False}
