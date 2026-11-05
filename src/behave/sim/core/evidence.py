"""What is known about a goal, and on what evidence.

One status per goal, with the evidence beside it. The alternative, a set per
outcome, lets a goal sit in two of them: a query can leave a goal unresolved
while the witness of some other goal reaches it, and then the counts disagree
with each other and a reader has to guess which set wins.

The three statuses describe what bounded evidence establishes for one goal:

``REACHED``      some trace reaches it, and the trace is here.
``UNREACHABLE``  no admissible trace reaches it, at the bound stated.
``UNRESOLVED``   neither was established.

Disagreement between two semantic readings is a run-level analysis outcome.  It never
creates a partial per-goal partition.

A safety goal reached is not a fifth status. It is ``REACHED`` on a goal whose
class is safety, and what makes it bad news rather than good is the class, not
the status. Keeping that in the class is what lets the same record carry both
kinds of goal without a reader having to know which sets mean what.

Nothing here knows what a goal is made of. The class of each goal comes from
the catalogue that declared it, so that a frontend can add families and
providers without this module changing.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType

from .catalogue import COVERAGE, SAFETY, catalogue_of
from .conditional_evidence import QueryEvidence
from .frozen import Frozen
from .record_value import (
    FrozenValueMap,
    freeze_record_value,
    thaw_record_value,
)

__all__ = ["COVERAGE", "SAFETY", "Closure", "GoalEvidence", "GoalStatus", "Origin"]


class GoalStatus(Enum):
    REACHED = "reached"
    UNREACHABLE = "unreachable"
    UNRESOLVED = "unresolved"


class Origin(Enum):
    """Where the evidence came from, which is how strong it is."""

    POOL = "pool"                  # a trace that was going to be run anyway
    QUERY = "query"                # a solver answer, replayed before believed
    CONSTRUCTION = "construction"  # the shape of the source rules it out
    BUDGET = "budget"              # the query ran out of its budget
    NOT_ASKED = "not asked"        # nothing was run and nothing was asked


def _trace_view(value):
    """Detach one frozen trace while retaining tuple-shaped trace boundaries."""
    if type(value) is FrozenValueMap:
        return {name: _trace_view(item) for name, item in value.items()}
    if type(value) is tuple:
        return tuple(_trace_view(item) for item in value)
    return thaw_record_value(value)


class GoalEvidence(Frozen):
    """One goal's status and what supports it."""

    __slots__ = ("_witness", "detail", "origin", "status")

    def __init_subclass__(cls, **kwargs):
        raise TypeError(
            "GoalEvidence is a closed closure record and cannot be subclassed")

    def __init__(self, status, origin, witness=None, detail=None):
        if type(status) is not GoalStatus:
            raise TypeError("a goal-evidence status must be an exact GoalStatus")
        if type(origin) is not Origin:
            raise TypeError("a goal-evidence origin must be an exact Origin")
        if detail is not None and type(detail) is not str:
            raise TypeError("a goal-evidence detail must be an exact string or absent")
        self.status = status
        self.origin = origin
        self._witness = (None if witness is None else
                         freeze_record_value(witness, "goal-evidence witness"))
        self.detail = detail        # why, in words, where words are what there is
        self._freeze()

    @property
    def witness(self):
        """A detached finite trace, where this status carries one."""
        return None if self._witness is None else _trace_view(self._witness)

    def __reduce__(self):
        return (GoalEvidence, (
            self.status, self.origin, self.witness, self.detail))

    def __repr__(self):
        return (f"GoalEvidence({self.status.value}, {self.origin.value}"
                + (f", {self.detail}" if self.detail else "") + ")")


class Closure(Frozen):
    """Every goal of an artifact, each with one status.

    ``catalogue`` maps an identifier to the ``Goal`` that declared it. It is
    supplied rather than computed, since what class a goal has is a property of
    the catalogue and not of its name: recovering it from a prefix works while
    there is one frontend whose identifiers follow one convention, and an RTL
    goal is not named after a Python source position.
    """

    __slots__ = (
        "broken",
        "catalogue",
        "construction_claims",
        "domain",
        "evidence",
        "queries",
        "query_transcript_complete",
        "steps",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("Closure is a closed phase result and cannot be subclassed")

    def __init__(self, evidence, catalogue, steps, broken=(),
                 construction_claims=None, domain="", queries=(),
                 query_transcript_complete=True, *, allow_legacy=False):
        if type(evidence) is not dict:
            raise TypeError("closure evidence must be an exact mapping")
        if any(type(goal) is not str or not goal for goal in evidence):
            raise TypeError(
                "closure evidence may name only nonempty exact goal strings")
        if any(type(item) is not GoalEvidence for item in evidence.values()):
            raise TypeError(
                "closure evidence accepts only exact GoalEvidence records")
        self.evidence = MappingProxyType(dict(evidence))
        # A stored closure is an evidence boundary.  Bare ``{id: class}`` maps
        # discard the schema and policy whose goal set the closure classifies, so the
        # compatibility door is explicit and production leaves it closed.
        self.catalogue = catalogue_of(catalogue, allow_legacy=allow_legacy)
        if type(steps) is not int or steps < 0:
            raise ValueError("closure steps must be an exact nonnegative integer")
        self.steps = steps
        # Traces of the pool this side could not carry out. Not a finding about
        # any goal, so it is not in the evidence; a reason to distrust the
        # coverage the pool established, so it is not dropped either.
        if type(broken) not in (tuple, list):
            raise TypeError("closure broken traces must be a finite sequence")
        if any(type(item) not in (tuple, list) or len(item) != 2
               or type(item[0]) is not int or item[0] < 0
               or type(item[1]) is not str or not item[1]
               for item in broken):
            raise TypeError(
                "each broken closure trace must be an index and nonempty reason")
        self.broken = tuple((item[0], item[1]) for item in broken)
        #: Goals the frontend said its construct rules out, whatever the queries then
        #: made of them. A **claim**, kept apart from the statuses: these used to be
        #: written straight in as `UNREACHABLE` by `CONSTRUCTION` and were skipped by the
        #: query loop, so the frontend's word was the whole of the evidence. Recorded so
        #: a reader can see which goals were claimed and check that each one's status now
        #: rests on a query.
        claims = {} if construction_claims is None else construction_claims
        if type(claims) is not dict or any(
                type(goal) is not str or not goal
                or type(why) is not str or not why
                for goal, why in claims.items()):
            raise TypeError(
                "closure construction claims must map goal strings to reasons")
        self.construction_claims = MappingProxyType(dict(claims))
        #: The digest of the domain this closure was gathered under.
        #:
        #: A closure used to carry only `steps`, so nothing stopped one from being handed
        #: to `certify` with an instance bound to a **different** domain: same bound,
        #: different contract or frontend or policy, and the certificate would name the
        #: instance's domain over evidence gathered under another. The bound is one field
        #: of eight and equality of one field is not equality.
        if type(domain) is not str:
            raise TypeError("a closure domain identity must be an exact string")
        self.domain = domain
        #: Exact set-query request/response records.  The per-goal partition alone
        #: cannot distinguish a query that returned UNSAT from a caller that merely
        #: wrote ``unreachable`` into the result.
        if type(queries) not in (tuple, list) or any(
                type(query) is not QueryEvidence for query in queries):
            raise TypeError(
                "a closure query transcript accepts only exact QueryEvidence records")
        self.queries = tuple(queries)
        if type(query_transcript_complete) is not bool:
            raise TypeError(
                "closure query-transcript completeness must be an exact Boolean")
        self.query_transcript_complete = query_transcript_complete
        self._freeze()

    def __reduce__(self):
        return (Closure, (
            dict(self.evidence), self.catalogue, self.steps, self.broken,
            dict(self.construction_claims), self.domain, self.queries,
            self.query_transcript_complete))

    @property
    def goals(self):
        return sorted(self.evidence)

    def of(self, goal):
        return self.evidence[goal]

    @property
    def classes(self):
        """The class of each goal, for a reader that wants only that."""
        return {key: goal.cls for key, goal in self.catalogue.items()}

    def of_goal(self, key):
        """The catalogue entry, where a report wants more than the status."""
        return self.catalogue.get(key)

    def with_status(self, status, cls=None):
        return {g for g, e in self.evidence.items()
                if e.status is status
                and (cls is None or (g in self.catalogue
                                     and self.catalogue[g].cls == cls))}

    @property
    def reached(self):
        return self.with_status(GoalStatus.REACHED)

    @property
    def unreachable(self):
        return self.with_status(GoalStatus.UNREACHABLE)

    @property
    def unresolved(self):
        return self.with_status(GoalStatus.UNRESOLVED)

    def witnesses(self, status=GoalStatus.REACHED):
        return {g: e.witness for g, e in self.evidence.items()
                if e.status is status and e.witness is not None}

    def summary(self):
        counts = {f"n_{status.value}": len(self.with_status(status))
                  for status in GoalStatus}
        counts.update({
            "n_goals": len(self.evidence),
            "n_failures": len(self.with_status(GoalStatus.REACHED, SAFETY)),
            # Claims the frontend made at construction, **not** a partition of the
            # unreachable set. It used to be the second: `ruled_out` wrote `UNREACHABLE`
            # with `Origin.CONSTRUCTION` straight in and the query loop skipped those
            # goals, so the count was of statuses resting on the frontend's word alone.
            # Every one of them is now asked, so this is a count of what was claimed and
            # the statuses beside it say what the queries made of the claims.
            "n_ruled_out_by_construction": len(self.construction_claims),
            # Of those claims, the ones a query then confirmed. The gap between the two
            # is the frontend's simplifier being unsupported or wrong, and it was
            # invisible while the claim *was* the status.
            "n_construction_claims_proved": len(
                {g for g in self.construction_claims
                 if self.evidence.get(g) is not None
                 and self.evidence[g].status is GoalStatus.UNREACHABLE
                 and self.evidence[g].origin is Origin.QUERY}),
            "n_pool_traces_broken": len(self.broken),
            "query_transcript_complete": self.query_transcript_complete,
            "steps": self.steps,
        })
        # Every goal has exactly one status, so the three add up to the total.
        # A report that says so is one a reader can check rather than believe.
        counts["accounted_for"] = (
            sum(counts[f"n_{s.value}"] for s in GoalStatus)
            == len(self.evidence))
        return counts
