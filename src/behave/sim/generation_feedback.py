"""Invocation-local coverage feedback, never a score or reachability certificate.

Only a source replay can advance this ledger. Solver answers, local UNSAT and
missing/unknown hit bits cannot retire a goal. Each provider owns a separate
ledger, including when two providers happen to use the same public goal names.
"""

from .core.refusal import Refusal, Refused
from .generation_protocol import ReplayFeedback as ReplayFeedback


def selection_targets(available, targets=None):
    """Restrict a provider's targets without changing its declaration or order.

    ``None`` selects the complete capability; an empty set is an observation-only
    Select. The source still reports all actual hits, not just these targets.
    """
    if targets is None:
        return tuple(available)
    selected = frozenset(targets)
    if not selected.issubset(available):
        raise ValueError("Select targets must belong to the provider's capability")
    return tuple(goal for goal in available if goal in selected)


class SearchCoverage:
    """Per-direction positive coverage used only to schedule remaining targets."""

    def __init__(self, manifest, targets):
        self.manifest = manifest
        self.declared = frozenset(manifest)
        self.targets = frozenset(targets)
        self.hits = set()
        self.replays = {}

    def observe(self, seed, feedback):
        if (type(feedback) is not ReplayFeedback
                or feedback.stimulus != seed.fingerprint
                or feedback.catalogue_digest != self.manifest.digest()
                or set(feedback.hits) - self.declared):
            raise Refused(Refusal(
                "generation-replay-feedback-mismatch",
                "search feedback must name this seed and source catalogue, with "
                "only declared, positively observed goals"))
        # The owner validates actual source execution before issuing feedback.
        # Keep the record identity for diagnostics, not an interchangeable score.
        self.replays[seed.fingerprint] = feedback
        self.hits.update(feedback.hits)

    def confirmation(self, stimulus, targets, named_goal):
        """Qualify a proposal with its own replay, never the pool's hit union."""
        replay = self.replays.get(stimulus)
        if replay is None:
            return {"status": "not-observed", "record": None,
                    "hit_targets": None, "named_goal_hit": None}
        hit = sorted(set(targets).intersection(replay.hits))
        named_hit = None if named_goal is None else named_goal in replay.hits
        status = ("named-goal-hit" if named_hit else
                  "requested-goal-hit" if named_goal is None and hit else
                  "other-requested-goal-hit" if hit else "no-requested-goal-hit")
        return {"status": status, "record": replay.record,
                "hit_targets": hit, "named_goal_hit": named_hit}

    @property
    def remaining(self):
        return self.targets - self.hits

    def restrict(self, request):
        remaining = self.remaining
        targets = tuple(goal for goal in request.targets if goal in remaining)
        if targets == request.targets:
            return request
        return request.with_targets(targets) if targets else None

    def as_dict(self):
        # A missing query implementation must not erase a native observation.
        # ``remaining`` is the actionable worklist; ``unhit`` describes the fixed
        # catalogue, including goals outside this provider's search capability.
        return {"version": "generation-coverage/2", "catalogue": self.manifest.digest(),
                "declared": sorted(self.declared), "targetable": sorted(self.targets),
                "observed": len(self.replays), "hits": sorted(self.hits),
                "unhit": sorted(self.declared - self.hits),
                "not_targeted": sorted(self.declared - self.targets),
                "remaining": sorted(self.remaining),
                "records": {seed: replay.record for seed, replay in self.replays.items()}}
