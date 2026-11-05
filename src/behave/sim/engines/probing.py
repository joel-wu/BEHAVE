"""What a local *generation* probe returns.

Two items of section 4B, in one module because the second counts the first.

**G0. A probe result that cannot become a verdict.** A stimulus engine's local
solve is a *candidate generator*. It asks whether some input, from **one sampled
state**, satisfies a query built out of **one observed path prefix**, and an
unsatisfiable answer to that says nothing about the artifact: another state, or
another prefix from the same state, may well reach the goal. The authority for
`unreachable` belongs to the horizon closure query, which ranges over every
D-admissible trace from Init_D, and the difference is not that the probe leaves a
state free -- leaving a state free establishes nothing on its own -- it is the set
quantified over.

So `ProbeResult` is a separate type from `GoalEvidence` and records the **local
scope** of the query it answers.  There is intentionally no conversion to a goal
status and no horizon scope in this module.  Horizon-wide answers belong to
``sim.core.analysis``; keeping them out of the generation algebra makes a local
UNSAT structurally incapable of masquerading as bounded unreachability.

This is a guard rather than a repair. Nothing wires a local UNSAT into evidence
today -- the RTL explorer's `flip_branch` returns `None` and the caller moves on --
so a reader looking for the bug will not find one. It is here because G2's
unification of the two backends behind one loop is exactly the change that would
introduce it, and a guard added after that change is a guard added after the
window it was for.

**G1. The baseline, before anything is made faster.** Everything else in 4B is
either a new provider or a migration, and both are judged by comparison against
what the search does now. A baseline taken afterwards is not a baseline.

`Tally` holds it, and the identity is **asserted rather than reported**:

    SAT = target-hit + other-hit + novel-state-only + rejected

The four are not naturally exclusive -- one candidate can hit the target it was
asked for, hit a second new target, *and* reach a novel state -- so they are
assigned by **priority, first match wins**, in that order. The overlaps are worth
knowing and are counted **separately and non-exclusively**: folded into the
identity they would break it, and dropped they would hide how often one candidate
does several jobs at once.

Asserted, because a misclassified probe silently moves counts between the very
columns G10's migration compares, and because the standing example is in this same
document: P1's total was dominated by two models and moved the wrong way, and only
the per-model spread said which half of it to believe.
"""

from __future__ import annotations

from enum import Enum

from ..generation_protocol import Outcome, ProbeResult, Scope

__all__ = ["Hit", "Outcome", "ProbeResult", "Scope", "Tally"]


class Hit(Enum):
    """What a satisfiable probe's candidate turned out to do.

    Priority order, and the order is the definition: a candidate that hits its
    target *and* reaches a novel state is a `TARGET`, counted once. The overlaps
    are on `Tally.overlaps`.

    **There are five and G1 specified four.** The item's identity was

        SAT = target-hit + other-hit + novel-state-only + rejected

    and it cannot be satisfied honestly, which the first baseline run showed: the
    search **discards most of its satisfiable candidates before replaying them**.
    An advancing goal whose post-state is already banked is dropped where it stands,
    so nothing ever asks whether that candidate reached the goal it was solved for.
    Counting those as `rejected` would state that a replay happened and bought
    nothing. Counting them as `novel-state-only` would be false by construction.
    Either way a measurement would have been invented, and the column G10 compares
    would be the one carrying the invention.

    So `NOT_REPLAYED` is its own class, and on the first run it was **98.3% of every
    satisfiable probe**. That is the baseline's main finding rather than a
    bookkeeping detail: the search throws away 98 of every 100 solver answers
    unexamined, and no measurement of "how often a local solve reaches its target"
    can be read off a run that never checked.
    """

    TARGET = "target-hit"              # the goal the query was built for
    OTHER = "other-hit"                # some other goal not reached before
    NOVEL_STATE = "novel-state-only"   # no new goal, but a state not seen
    REJECTED = "rejected"              # replayed, and reached nothing new
    NOT_REPLAYED = "not-replayed"      # discarded before anything checked it


class Tally:
    """G1's baseline for one backend, with the identity checked.

    Every count is per backend because that is the unit G10 compares. `queries`
    counts solver invocations rather than goals, since a goal retried with a fresh
    non-incremental solver is two.
    """

    __slots__ = (
        "backend",
        "build_failures",
        "by_target",
        "hits",
        "outcomes",
        "overlaps",
        "queries",
        "refusal_why",
        "refusals",
        "replay_failures",
        "replay_ms",
        "scopes",
        "solve_ms",
    )

    def __init__(self, backend):
        self.backend = backend
        self.queries = 0
        #: Target-hit rate split by whether the target had been reached before,
        #: which is the split that decides whether an exact provider is worth
        #: building. Without it the two are averaged and the average is a number
        #: about neither: a candidate for an already-reached target hits it almost
        #: always, and one for a goal nothing has reached is the hard case and the
        #: whole point of the search. G1's first figure of 37% was that average,
        #: taken over the replayed subset, and the subset is biased -- every primary
        #: candidate is replayed and an advancing one only if its state is novel.
        self.by_target = {"primary": {"sat": 0, "target_hit": 0, "replayed": 0},
                          "advancing": {"sat": 0, "target_hit": 0, "replayed": 0}}
        self.outcomes = {o: 0 for o in Outcome}
        #: ``"scope/outcome"`` to a count, since an UNSAT means different things by
        #: scope and a total over both is a number nobody can read. A string key
        #: rather than the pair, because this is reported as JSON and a tuple key
        #: is not serialisable -- found by the first run of the baseline probe,
        #: which dropped every model it measured with `TypeError: keys must be
        #: str, int, float, bool or None`.
        self.scopes = {}
        self.hits = {h: 0 for h in Hit}
        #: Non-exclusive, so these do not enter the identity. `target_and_other`
        #: is a candidate that reached its target and something else too.
        self.overlaps = {"target_and_other": 0, "target_and_novel_state": 0,
                         "other_and_novel_state": 0}
        #: Questions this search could not answer, **by `Refusal.kind`**, with no
        #: per-kind code here. Non-exclusive and outside the identity, like the
        #: overlaps.
        #:
        #: One dict rather than a counter per kind, and that is the reuse point rather
        #: than tidiness. `novelty_unavailable` and `not_asked` were two hand-written
        #: attributes for one idea, and adding the second cost seven edits inside this
        #: class: a `__slots__` entry, an `__init__` line, a branch in `record`, a term
        #: in `check`, a key in `as_dict`, plus the enum member and the closed-set test.
        #: todo3 says users will add providers, and a provider that cannot refuse
        #: without editing this file is a provider that will refuse by returning
        #: `False`. Now a new kind of refusal counts itself.
        self.refusals = {}
        #: The reason of the first refusal of each kind, so a report can say *why*
        #: rather than only how many. One per kind rather than all of them: the reason
        #: is a property of the kind, and keeping thousands of copies of one sentence
        #: would make this the largest thing in a baseline.
        self.refusal_why = {}
        #: A candidate the solver produced that could not be turned into a
        #: stimulus, and one that could not be replayed. Both are SAT answers that
        #: bought nothing, and both were invisible before: the code moved on.
        self.build_failures = 0
        self.replay_failures = 0
        self.solve_ms = 0.0
        self.replay_ms = 0.0

    # ── recording ──

    def record(self, result):
        """Count one probe result.

        Every member of ``Outcome`` is an answer to a query.  Selection omissions and
        relation-domain exclusions are ``Refusal`` diagnostics before ``probe`` and
        therefore never enter this algebra or this denominator.
        """
        self.queries += 1
        self.outcomes[result.outcome] += 1
        key = f"{result.scope.value}/{result.outcome.value}"
        self.scopes[key] = self.scopes.get(key, 0) + 1
        self.solve_ms += result.solve_ms

    def refused(self, refusal):
        """Count one question that had no answer, grouped by its kind.

        Takes a `Refusal` and not a string, so that the reason travels with the count
        and a caller cannot record a kind while dropping the why.
        """
        from ..core.refusal import Refusal

        if not isinstance(refusal, Refusal):
            raise TypeError(
                f"refused() takes a Refusal, not {type(refusal).__name__}: the reason "
                f"has to travel with the count or the count is a number nobody can act "
                f"on")
        self.refusals[refusal.kind] = self.refusals.get(refusal.kind, 0) + 1
        self.refusal_why.setdefault(refusal.kind, refusal.why)
        return refusal

    def discarded(self, *, primary=False):
        """Count one satisfiable candidate the search dropped before replaying it.

        Its own call rather than `classify(all False)`, because the three flags are
        answers and here there are none: nothing ran the candidate, so nothing knows
        what it would have reached.
        """
        self.hits[Hit.NOT_REPLAYED] += 1
        self.by_target["primary" if primary else "advancing"]["sat"] += 1
        return Hit.NOT_REPLAYED

    def classify(self, *, target_hit, other_hit, novel_state, primary=None):
        """Assign one replayed candidate to exactly one class, and return it.

        Priority, first match wins, which is what makes the identity hold. The
        overlaps are recorded on the way past.

        Only for a candidate that was actually replayed. One that was not goes to
        `discarded`, since every flag here would be an assumption.

        ``primary`` says whether the target had been reached before. Recorded because
        the two cases have very different hit rates and averaging them gives a number
        about neither.

        ``novel_state`` may be a **`Refusal`**, meaning the search has no snapshot basis
        to compare against, so the question was not asked. That is a third thing from
        `True` and `False`, and collapsing it into `False` is the failure this
        parameter exists to stop: it makes "we have no frontier" indistinguishable
        from "this candidate landed somewhere already banked", which is what the RTL
        side reported for want of anywhere else to put it. The candidate still lands in
        exactly one class -- the identity is over every satisfiable probe and does not
        care why a flag was unset -- and `refusals` counts, by kind, how many were
        decided without it.

        It was a bare `None` and a hand-written `novelty_unavailable` counter. A
        `Refusal` carries its own reason and counts itself, so the next provider with a
        question it cannot answer needs no change here.
        """
        from ..core.refusal import Refusal

        if isinstance(novel_state, Refusal):
            self.refused(novel_state)
        if primary is not None:
            kind = self.by_target["primary" if primary else "advancing"]
            kind["sat"] += 1
            kind["replayed"] += 1
            if target_hit:
                kind["target_hit"] += 1
        # `is True` throughout, not truthiness: a `Refusal` raises on `__bool__`, which
        # is the point, and these lines are exactly where a bare `and novel_state`
        # would have quietly meant "not novel".
        if target_hit and other_hit:
            self.overlaps["target_and_other"] += 1
        if target_hit and novel_state is True:
            self.overlaps["target_and_novel_state"] += 1
        if other_hit and novel_state is True:
            self.overlaps["other_and_novel_state"] += 1

        if target_hit:
            where = Hit.TARGET
        elif other_hit:
            where = Hit.OTHER
        elif novel_state is True:
            where = Hit.NOVEL_STATE
        else:
            # A `Refusal` lands here with `False`, and `refusals` above is what keeps
            # the two readable apart. `is True` rather than truthiness so that an
            # unanswerable novelty cannot be mistaken for an answer at this line
            # either -- and a `Refusal` would raise here rather than silently pass.
            where = Hit.REJECTED
        self.hits[where] += 1
        return where

    # ── the identity ──

    def check(self):
        """Raise unless the classification accounts for every satisfiable probe.

        Reported and not asserted, a misclassified probe moves counts between the
        columns a migration is judged by, and the move is silent: every number
        still adds up to something.

        Build and replay failures are counted **within** the SAT total, because a
        candidate that could not be built or replayed is still an answer the solver
        gave. A replay that threw is `rejected`: it ran and reached nothing.
        """
        classified = sum(self.hits.values())
        sat = self.outcomes[Outcome.SAT]
        if classified != sat:
            raise AssertionError(
                f"{self.backend}: {sat} satisfiable probes and {classified} "
                f"classified ({{{', '.join(f'{h.value}={n}' for h, n in self.hits.items())}}}). "
                f"The four classes are assigned by priority and every SAT gets "
                f"exactly one")
        total = sum(self.outcomes.values())
        if total != self.queries:
            raise AssertionError(
                f"{self.backend}: {self.queries} queries and {total} outcomes")
        return True

    def as_dict(self):
        self.check()
        return {
            "backend": self.backend,
            "queries": self.queries,
            "outcomes": {o.value: n for o, n in self.outcomes.items()},
            "by_scope": dict(self.scopes),
            "hits": {h.value: n for h, n in self.hits.items()},
            "overlaps": dict(self.overlaps),
            # By kind, with the reason beside it. A reader gets "how many" and "why"
            # from one place, and a new kind appears here without this line changing.
            "refusals": dict(self.refusals),
            "refusal_why": dict(self.refusal_why),
            "by_target": {k: dict(v) for k, v in self.by_target.items()},
            "build_failures": self.build_failures,
            "replay_failures": self.replay_failures,
            "solve_ms": round(self.solve_ms, 2),
            "replay_ms": round(self.replay_ms, 2),
        }

    def __repr__(self):
        return (f"Tally({self.backend}, {self.queries} queries, "
                f"{self.outcomes[Outcome.SAT]} sat, "
                f"{self.hits[Hit.TARGET]} target-hit)")
