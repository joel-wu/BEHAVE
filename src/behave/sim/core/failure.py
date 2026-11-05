"""Typed attribution for a run that produced no comparable result.

The evaluator must decide *who* failed before aggregation.  Candidate failures are
determinate failures of the submitted artifact and therefore score zero.  Failures of
the reference, verifier, or execution service establish no score.  Resource limits are
the only policy-dependent case, so their record names the policy and its published
disposition explicitly.
"""

from __future__ import annotations

import enum

from .catalogue import CatalogueManifest, catalogue_subset
from .frozen import Frozen
from .refusal import Refusal, Refused

__all__ = [
    "Broken",
    "CandidateFault",
    "FailureDisposition",
    "FailureOrigin",
    "FailureRaised",
    "ObservedGoals",
    "RunFailure",
    "call_candidate",
]


class Broken(Exception):
    """An execution boundary stopped answering, with its original typed reason.

    This carries transport evidence, not candidate/reference attribution. The
    caller owns that classification. Frontends re-export the same exception so
    the common comparator need not import a language-specific implementation.
    """

    def __init__(self, message="", *, code="", detail=""):
        super().__init__(message)
        self.code = str(code)
        self.detail = str(detail)


class ObservedGoals(Frozen):
    """Positive goal observations from a run that has no complete trace.

    The frontend supplies this only after validating its execution evidence.
    The manifest contains reached goals and their original occurrence identities;
    absence says nothing about unexecuted steps. Raw evidence stays in the failure.
    """

    __slots__ = ("stimulus", "catalogue")

    def __init__(self, stimulus, catalogue):
        if type(stimulus) is not str or not stimulus:
            raise ValueError("observed goals need their stimulus identity")
        if type(catalogue) is not CatalogueManifest:
            raise TypeError("observed goals need their declaration manifest")
        self.stimulus = stimulus
        self.catalogue = catalogue
        self._freeze()

    def check_catalogue(self, catalogue):
        if not set(self.catalogue) <= set(catalogue) or \
                catalogue_subset(catalogue, self.catalogue) != self.catalogue:
            raise ValueError("observed goals belong to another catalogue")

    def as_dict(self):
        return {"stimulus": self.stimulus, "catalogue": self.catalogue.as_dict()}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"stimulus", "catalogue"}:
            raise ValueError("observed goals need a stimulus and declaration manifest")
        return cls(value["stimulus"], CatalogueManifest.from_dict(value["catalogue"]))


class FailureOrigin(enum.Enum):
    """The component responsible for one failed run phase."""

    CANDIDATE = "candidate"
    REFERENCE = "reference"
    VERIFIER = "verifier"
    INFRASTRUCTURE = "infrastructure"
    RESOURCE_POLICY = "resource-policy"

    def __bool__(self):
        raise TypeError("a failure origin is a category, not a truth value")


class FailureDisposition(enum.Enum):
    """Whether the attributed fact determines the candidate's score."""

    SCORE_ZERO = "score-zero"
    NO_RESULT = "no-result"

    def __bool__(self):
        raise TypeError("a failure disposition is a category, not a truth value")


class RunFailure(Frozen):
    """One phase failure with its origin and scoring consequence fixed at source."""

    __slots__ = ("disposition", "origin", "phase", "policy", "reason", "observed")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("RunFailure is a closed evidence record")

    def __init__(self, origin, phase, reason, *, disposition=None, policy="", observed=None):
        if type(origin) is not FailureOrigin:
            raise TypeError("a run failure needs the exact FailureOrigin enum")
        if type(phase) is not str or not phase.strip():
            raise ValueError("a run failure needs a nonempty phase")
        if type(reason) is not Refusal:
            raise TypeError("a run failure needs the exact typed Refusal reason")
        if disposition is None:
            disposition = (
                FailureDisposition.SCORE_ZERO
                if origin is FailureOrigin.CANDIDATE
                else FailureDisposition.NO_RESULT
            )
        if type(disposition) is not FailureDisposition:
            raise TypeError(
                "a run failure needs the exact FailureDisposition enum")
        if type(policy) is not str:
            raise TypeError("a run failure policy must be an exact string")

        if origin is FailureOrigin.CANDIDATE:
            required = FailureDisposition.SCORE_ZERO
        elif origin in {
                FailureOrigin.REFERENCE,
                FailureOrigin.VERIFIER,
                FailureOrigin.INFRASTRUCTURE,
        }:
            required = FailureDisposition.NO_RESULT
        else:
            required = None
        if required is not None and disposition is not required:
            raise ValueError(
                f"{origin.value} failures have disposition {required.value}")
        if origin is FailureOrigin.RESOURCE_POLICY:
            if not policy.strip():
                raise ValueError(
                    "a resource-policy failure must name the published policy")
        elif policy:
            raise ValueError(
                "only a resource-policy failure carries a policy identity")

        self.origin = origin
        self.phase = phase
        self.reason = reason
        self.disposition = disposition
        self.policy = policy
        if observed is not None and type(observed) is not ObservedGoals:
            raise TypeError("failure observations must be an ObservedGoals record")
        self.observed = observed
        self._freeze()

    @classmethod
    def candidate(cls, phase, reason, *, observed=None):
        return cls(FailureOrigin.CANDIDATE, phase, reason, observed=observed)

    @classmethod
    def reference(cls, phase, reason):
        return cls(FailureOrigin.REFERENCE, phase, reason)

    @classmethod
    def verifier(cls, phase, reason, *, observed=None):
        return cls(FailureOrigin.VERIFIER, phase, reason, observed=observed)

    @classmethod
    def infrastructure(cls, phase, reason):
        return cls(FailureOrigin.INFRASTRUCTURE, phase, reason)

    @classmethod
    def resource(cls, phase, reason, *, policy, disposition):
        return cls(
            FailureOrigin.RESOURCE_POLICY,
            phase,
            reason,
            policy=policy,
            disposition=disposition,
        )

    @property
    def kind(self):
        return self.reason.kind

    @property
    def why(self):
        return self.reason.why

    @property
    def determines_score(self):
        return self.disposition is FailureDisposition.SCORE_ZERO

    def as_dict(self):
        out = {
            "origin": self.origin.value,
            "phase": self.phase,
            "disposition": self.disposition.value,
            "reason": self.reason.as_dict(),
        }
        if self.policy:
            out["policy"] = self.policy
        if self.observed is not None:
            out["observed"] = self.observed.as_dict()
        return out

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict:
            raise TypeError("a run-failure image must be a mapping")
        required = {"origin", "phase", "disposition", "reason"}
        allowed = required | {"policy", "observed"}
        if set(value) < required or set(value) - allowed:
            raise ValueError("a run-failure image has missing or unknown fields")
        reason = value["reason"]
        if type(reason) is not dict or not {"kind", "why"} <= set(reason) \
                or set(reason) - {"kind", "why", "detail"}:
            raise ValueError("a run-failure image carries no typed refusal")
        detail = reason.get("detail", {})
        if type(detail) is not dict:
            raise TypeError("a run-failure refusal detail must be a mapping")
        got = cls(
            FailureOrigin(value["origin"]),
            value["phase"],
            Refusal(reason["kind"], reason["why"], **detail),
            disposition=FailureDisposition(value["disposition"]),
            policy=value.get("policy", ""),
            observed=(ObservedGoals.from_dict(value["observed"])
                      if "observed" in value else None),
        )
        if got.as_dict() != value:
            raise ValueError("a run-failure image is not canonical")
        return got

    def __str__(self):
        return f"{self.origin.value}:{self.reason.kind}"

    def __repr__(self):
        return (
            f"RunFailure({self.origin.value}, {self.phase!r}, "
            f"{self.reason.kind!r}, {self.disposition.value})"
        )


class FailureRaised(RuntimeError):
    """Exception transport for a fully attributed :class:`RunFailure`."""

    def __init__(self, failure):
        if type(failure) is not RunFailure:
            raise TypeError("FailureRaised takes an exact RunFailure")
        super().__init__(str(failure))
        self.failure = failure

    def __reduce__(self):
        return type(self), (self.failure,)


class CandidateFault(Refused):
    """A source callback or validated candidate observation establishes a fault.

    Unlike an unqualified refusal from the driver or decoder, this is positive
    evidence about the artifact. Protocol validators raise it at the offending
    observation; shared orchestration must not infer it from exception text.
    """


def call_candidate(operation, /, *args, **kwargs):
    """Attribute exceptions from a device callback, not from its surrounding driver."""
    try:
        return operation(*args, **kwargs)
    except FailureRaised:
        raise
    except Refused as exc:
        raise FailureRaised(RunFailure.candidate(
            "candidate-run", exc.refusal)) from None
    except Exception as exc:  # noqa: BLE001 - candidate callback boundary
        raise FailureRaised(RunFailure.candidate(
            "candidate-run", Refusal(
                "candidate-runtime-failed",
                "the candidate callback raised while executing an admitted stimulus",
                error=type(exc).__name__))) from None
