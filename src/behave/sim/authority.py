"""The sole authority for concrete simulator outcomes.

The public command owns execution; evaluation_policy owns the verdict rule.
This module owns only versioned result types and their evidence invariants.

The canonical stimulus set and its ``ConcreteEvaluation`` are immutable.  Four closed
outcomes are possible, and only one carries a score:

* `concrete_evaluation` -- the complete mandatory baseline agrees and additional
  checks find no artifact failure, or a checked negative witness establishes zero.
  A checked negative witness may establish zero before the pool finishes.
* `refused` -- the task is unsupported or required information is absent; nothing ran;
* `partial` -- runs happened and not all of them could be carried through. There is
  evidence, and it is not a score: the denominator is not the stimulus set.
* `heuristic_smoke_test` -- diagnostic replay that establishes no score.

Coverage is telemetry under every kind, not evidence of correctness.
"""

from __future__ import annotations

import enum
import os
import uuid

from .core.frozen import Frozen, FrozenMap
from .core.refusal import Refusal, Refused
from .core.supported import SUPPORTED

__all__ = ["CONCRETE", "HEURISTIC", "PARTIAL", "REFUSED", "Authoritative", "Outcome"]


class Outcome(enum.Enum):
    """The closed outcome set of the authoritative path."""

    #: A determinate outcome over the explicitly recorded score scope.
    CONCRETE = "concrete_evaluation"
    #: The task is outside the supported range, or something it needs is absent. Nothing
    #: ran, and there is deliberately no fallback to the older number.
    REFUSED = "refused"
    #: Runs happened and not all could be carried through, so the denominator of any
    #: score is not the stimulus set.
    PARTIAL = "partial"
    #: Diagnostic replay: exercises the design but establishes no agreement.
    HEURISTIC = "heuristic_smoke_test"

    @property
    def authoritative(self):
        return self is Outcome.CONCRETE


#: The string forms, for the report and for callers that predate the enum.
CONCRETE = Outcome.CONCRETE.value
REFUSED = Outcome.REFUSED.value
PARTIAL = Outcome.PARTIAL.value
HEURISTIC = Outcome.HEURISTIC.value

#: Version of score, catalogue/domain and bounded-analysis evidence semantics.
#: Advance when record meaning or the admitted/generated evidence changes.
#: Historical rationale: docs/authority_versions.md; current status: docs/todo3.md.
RESULT_VERSION = "authority/104"

#: This process's run of the path. See `Authoritative.as_dict`.
INVOCATION = f"{os.getpid()}-{uuid.uuid4().hex}"


class Authoritative(Frozen):
    """What the authoritative path produced, and which of the four kinds it is.

    Frozen, like every other record a claim rests on: `result.kind = CONCRETE` on a
    refused run would turn "nothing was established" into a score, and the object that
    says which of the four this is has to be the one thing nothing downstream can edit.
    """

    __slots__ = ("evaluation", "kind", "refusals", "telemetry", "why")

    def __init_subclass__(cls, **kwargs):
        # Verdict and serialization methods are part of the sealed claim boundary.
        raise TypeError(
            "Authoritative is sealed: a subclass inherits the checks and overrides the "
            "answers, and what it answers for `passed` and `as_dict` becomes the "
            "persisted verdict. Build the real thing")

    def __init__(self, kind, why="", evaluation=None, refusals=(), telemetry=None):
        #: One of `Outcome`, kept as its string form for the report. Coerced through the
        #: enum so a kind this build has no meaning for is refused at construction
        #: rather than travelling into a report and being compared against a literal.
        self.kind = Outcome(kind).value if not isinstance(kind, Outcome) else kind.value
        self.why = str(why)
        # Concrete evidence has an exact type, a nonempty run set and complete
        # bindings. Candidate faults may score zero; no-result failures may not.
        if Outcome(self.kind) is Outcome.CONCRETE:
            from .core.outcome import ConcreteEvaluation

            if type(evaluation) is not ConcreteEvaluation:
                raise Refused(Refusal(
                    "authority-evidence-not-an-evaluation",
                    "a concrete result is the evaluation that establishes it, and this "
                    "one was built over something else; whatever that object answers "
                    "for `passed` is not evidence of anything",
                    got=type(evaluation).__name__))
            if not evaluation.results:
                raise Refused(Refusal(
                    "authority-evidence-empty",
                    "a concrete result over zero runs scores nothing: the set is "
                    "refused before this as authority-empty-stimulus-set, and evidence "
                    "with no run in it must not be constructible either"))
            if (not evaluation.required_complete
                    and evaluation.failure_witness is None):
                raise Refused(Refusal("authority-evidence-not-whole",
                                     "mandatory baseline tests have not completed"))
            if evaluation.no_result_failures and evaluation.failure_witness is None:
                raise Refused(Refusal(
                    "authority-evidence-not-whole",
                    "this evaluation carries failures that establish no result, so "
                    "it has no score over the complete stimulus set; declaring it "
                    "concrete here would skip that rule",
                    failures=len(evaluation.no_result_failures)))
            if evaluation.unbound and evaluation.failure_witness is None:
                # An unbound trace names no artifact or no domain, so "this artifact
                # failed in this domain" is not something the evidence can say -- and
                # `ConcreteEvaluation.passed` answers False over one, which under
                # CONCRETE read as a determinate failure of a design the trace cannot
                # even be tied to.
                raise Refused(Refusal(
                    "authority-evidence-unbound",
                    "this evaluation contains traces bound to no artifact or no "
                    "domain, so what it establishes is not about any nameable run; a "
                    "concrete verdict over it would fail a design the evidence cannot "
                    "be tied to",
                    unbound=list(evaluation.unbound)))
        self.evaluation = evaluation
        self.refusals = tuple(refusals)
        self.telemetry = FrozenMap(telemetry or {})
        self._freeze()

    @property
    def outcome(self):
        return Outcome(self.kind)

    @property
    def authoritative(self):
        """Whether this result may carry a `passed`. Only one kind may."""
        return self.outcome.authoritative

    @property
    def passed(self):
        if not self.authoritative:
            raise Refused(Refusal(
                "authority-no-score-under-this-kind",
                "only a concrete evaluation carries a score. This result is of a "
                "different kind, and reading a pass off it would attribute to the "
                "design a property nothing established",
                result_kind=self.kind, because=self.why))
        return self.evaluation.passed

    def as_summary_dict(self):
        """The fingerprint-bound verdict mirror used by reports and progress.

        The complete evaluation belongs only to the authoritative run record.
        A mirror retains its fingerprint and verdict fields without serialising
        a second copy of the evidence.
        """
        out = {"kind": self.kind, "version": RESULT_VERSION, "why": self.why,
               "authoritative": self.authoritative,
               "supported_range": SUPPORTED,
               # **Which run of this build produced it.** Two records under one name, at
               # one commit, over one task, were indistinguishable -- so a stale file
               # left by an earlier attempt read as this run's result.
               "invocation": INVOCATION,
               "refusals": [r.as_dict() for r in self.refusals],
               "telemetry": dict(self.telemetry)}
        if self.evaluation is not None:
            out["evaluation_fingerprint"] = self.evaluation.fingerprint
            out["score_scope"] = self.evaluation.score_scope
        if self.authoritative:
            out["passed"] = self.evaluation.passed
        return out

    def as_dict(self):
        """The complete authoritative record, including its sole evidence image."""
        out = self.as_summary_dict()
        if self.evaluation is not None:
            out["evaluation"] = self.evaluation.as_evidence_dict()
        return out

    def __repr__(self):
        return f"Authoritative({self.kind}{'' if not self.why else ': ' + self.why[:40]})"
