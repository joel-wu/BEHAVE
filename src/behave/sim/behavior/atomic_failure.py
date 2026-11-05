"""Source-attributed atomic failure evidence, shared by replay and audit.

An individual callback names its partial operation. A global slot may contain
several such failures; its hit set also includes successful coincident callbacks,
although none of their state or outputs commit.
"""

from dataclasses import dataclass

from ..core.catalogue import SAFETY
from ..core.failure import CandidateFault
from ..core.refusal import Refusal, Refused

SINGLE_FAILURE = "behavior-semantic-failure"
MULTIPLE_FAILURES = "behavior-atomic-semantic-failures"


@dataclass(frozen=True)
class SourceFailure:
    domain: str
    operation: str
    goal: str
    error: str

    def as_dict(self):
        return {"domain": self.domain, "operation": self.operation,
                "goal": self.goal, "error": self.error}


@dataclass(frozen=True)
class FailureEvidence:
    failures: tuple[SourceFailure, ...]
    hits: tuple[str, ...]


def source_failure_evidence(error, catalogue=None):
    """Decode the worker's failure channel, not an arbitrary Python exception."""
    if not isinstance(error, Refused) or error.refusal.kind not in (SINGLE_FAILURE, MULTIPLE_FAILURES):
        return None
    detail = error.refusal.as_dict().get("detail", {})

    def malformed():
        raise Refused(Refusal("behavior-semantic-failure-record-malformed",
                              "the atomic worker's failure records and declared hit vector disagree"))

    if error.refusal.kind == SINGLE_FAILURE:
        if set(detail) != {"domain", "operation", "goal", "error", "hits"}:
            malformed()
        rows = [{key: value for key, value in detail.items() if key != "hits"}]
    else:
        if set(detail) != {"failures", "hits"} or type(detail["failures"]) is not list \
                or len(detail["failures"]) < 2:
            malformed()
        rows = detail["failures"]
    failures = []
    for row in rows:
        if type(row) is not dict or set(row) != {"domain", "operation", "goal", "error"} \
                or any(type(value) is not str or not value for value in row.values()):
            malformed()
        failures.append(SourceFailure(**row))
    hits = detail["hits"]
    if type(hits) is not list or any(type(hit) is not str or not hit for hit in hits) \
            or hits != sorted(set(hits)) or any(failure.goal not in hits for failure in failures):
        malformed()
    if catalogue is not None and (not set(hits) <= set(catalogue) or
            {goal for goal in hits if catalogue[goal].cls == SAFETY} != {failure.goal for failure in failures}):
        malformed()
    return FailureEvidence(tuple(failures), tuple(hits))


def combined_failure(errors, effects):
    """Keep all failures and source hits; choose no winning domain or error."""
    evidence = [source_failure_evidence(error) for error in errors]
    failures = tuple(failure for item in evidence for failure in item.failures)
    hits = sorted({hit for item in evidence for hit in item.hits}
                  | {hit for effect in effects for hit in effect.hits})
    if len(failures) == 1:
        return CandidateFault(Refusal(SINGLE_FAILURE,
            "an atomic Behavior handler reached a declared partial operation",
            **failures[0].as_dict(), hits=hits))
    return CandidateFault(Refusal(MULTIPLE_FAILURES,
        "several coincident Behavior handlers reached declared partial operations",
        failures=[failure.as_dict() for failure in failures], hits=hits))
