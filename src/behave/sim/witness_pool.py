"""A secondary witness pool derived after canonical concrete evaluation.

The paper's authoritative set is ``Xi_A``.  It is never minimized: its score, raw
records and fingerprint remain the concrete result.  This module derives a separately
named storage/inspection pool while preserving the *set* of observable contributions:
goal hits, exact mismatch records, infrastructure outcomes, completion classes and
schedule classes.

The reduction is deterministic greedy set cover.  It is not evidence authority and it
does not recompute a score; consumers wanting either must use ``ConcreteEvaluation``.
"""

from __future__ import annotations

from .core.frozen import Frozen, FrozenMap
from .core.outcome import ConcreteEvaluation
from .core.refusal import Refusal, Refused
from .core.run_record import RunResult
from .core.stimulus import canonical_json
from .generation_protocol import stimulus_bytes

__all__ = [
    "MinimizedWitnessPool",
    "minimize_witnesses",
    "secondary_pool_report",
]

POLICY = "semantics-preserving-witness-pool/3"

# Only ``minimize_witnesses`` may construct the derived record.  This is not a
# security boundary; it keeps ordinary callers from assembling a pool whose displayed
# evaluation digest, kept runs and contribution rows were never checked together.
_DERIVED = object()


def _frozen_mapping(value, *, what):
    """Read one mapping produced by ``core.frozen.freeze`` without guessing shapes."""

    if type(value) is not tuple:
        raise ValueError(f"{what} is not a frozen mapping")
    pairs = []
    for item in value:
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
            raise ValueError(f"{what} contains a malformed field")
        pairs.append(item)
    if len({key for key, _value in pairs}) != len(pairs):
        raise ValueError(f"{what} contains a duplicate field")
    return dict(pairs)


def _schedule_image(result):
    image = result.stimulus.as_dict()
    image.pop("queues", None)
    image.pop("source", None)
    return canonical_json(image)


def _fault_feature(fault):
    kind, position, expected, observed = fault
    return "mismatch:" + canonical_json([
        kind,
        position,
        None if expected is None else list(expected),
        None if observed is None else list(observed),
    ])


def _monitor_hits(evaluation):
    """Return per-result monitor hits, refusing telemetry that cannot attribute them."""

    by_result = [set() for _ in evaluation.results]
    try:
        telemetry = _frozen_mapping(
            evaluation.telemetry, what="evaluation telemetry")
    except ValueError as exc:
        raise Refused(Refusal(
            "minimization-monitor-evidence-malformed",
            "the secondary pool cannot preserve monitor findings because the "
            "evaluation telemetry is malformed",
            error=str(exc))) from None
    if "monitor_hits" in telemetry:
        rows = telemetry["monitor_hits"]
        channel = "monitor_hits"
    else:
        # Compatibility with result versions before non-violation monitor scenarios
        # had their own attributed hit channel.
        rows = telemetry.get("monitor_violations", ())
        channel = "monitor_violations"
    try:
        if type(rows) is not tuple:
            raise ValueError(f"{channel} is not a finite frozen sequence")
        for row in rows:
            if type(row) is not tuple or len(row) != 2:
                raise ValueError(f"{channel} contains a malformed attributed row")
            index, frozen_hit = row
            if type(index) is not int or not 0 <= index < len(by_result):
                raise ValueError("invalid monitor run index")
            hit = _frozen_mapping(frozen_hit, what=f"{channel} hit")
            allowed = ({"goal", "slot", "endpoint"}
                       if channel == "monitor_hits"
                       else {"goal", "slot", "endpoint", "why"})
            required = ({"goal", "slot", "endpoint"}
                        if channel == "monitor_hits" else {"goal", "slot"})
            if not required <= set(hit) or set(hit) - allowed:
                raise ValueError(f"{channel} contains an unknown or missing field")
            goal = hit.get("goal")
            if type(goal) is not str or not goal:
                raise ValueError("a monitor hit has no goal")
            slot = hit.get("slot")
            if type(slot) is not int or slot < 0:
                raise ValueError("a monitor hit has no nonnegative exact slot")
            if "endpoint" in hit and type(hit["endpoint"]) is not str:
                raise ValueError("a monitor hit endpoint is not an exact string")
            if "why" in hit and type(hit["why"]) is not str:
                raise ValueError("a monitor violation explanation is not an exact string")
            by_result[index].add(goal)
    except (TypeError, ValueError) as exc:
        raise Refused(Refusal(
            "minimization-monitor-evidence-malformed",
            "the secondary pool cannot preserve monitor findings because their "
            "per-run attribution is malformed",
            error=str(exc))) from None
    return by_result


def _features(evaluation):
    monitor = _monitor_hits(evaluation)
    features = {}
    attributed_hits = set()
    # Compact, deterministic and collision-free within this evaluation.  Numbering
    # sorted exact images avoids both a report-sized copy of every slot schedule and a
    # digest whose collision could merge two required schedule classes.
    schedule_images = tuple(sorted({
        _schedule_image(result) for result in evaluation.results}))
    schedule_classes = {
        image: f"schedule:{index}"
        for index, image in enumerate(schedule_images)}
    for index, result in enumerate(evaluation.results):
        if type(result) is not RunResult:
            raise TypeError("ConcreteEvaluation contains a non-RunResult")
        own = {schedule_classes[_schedule_image(result)]}
        if result.ran:
            trace_hits = {
                str(goal)
                for record in result.trace.records
                for goal, reached in record.hits
                if reached
            }
            trace_hits.update(monitor[index])
            attributed_hits.update(trace_hits)
            own.update(f"goal:{goal}" for goal in trace_hits)
            own.add(f"completion:{result.trace.completion.value}")
        if result.run_failure is not None:
            failure = result.run_failure
            own.add(
                "failure:run:"
                f"{failure.origin.value}:{failure.disposition.value}:"
                f"{failure.phase}:{failure.kind}")
        if result.comparison_failure is not None:
            failure = result.comparison_failure
            own.add(
                "failure:comparison:"
                f"{failure.origin.value}:{failure.disposition.value}:"
                f"{failure.phase}:{failure.kind}")
        own.update(_fault_feature(fault) for fault in result.faults)
        if result.was_compared:
            own.update("memory-mismatch:" + canonical_json(fault)
                       for fault in result.comparison.memory_faults)
        features[result.stimulus.fingerprint] = frozenset(own)

    if attributed_hits != set(evaluation.hits):
        raise Refused(Refusal(
            "minimization-hit-attribution-incomplete",
            "the concrete result contains goal hits that cannot be attributed to one "
            "raw run, so a reduced pool could silently delete their only witness",
            unattributed=sorted(set(evaluation.hits) - attributed_hits),
            undeclared=sorted(attributed_hits - set(evaluation.hits))))
    return features


class MinimizedWitnessPool(Frozen):
    """A deterministic secondary pool bound to one concrete-result fingerprint."""

    __slots__ = ("contributions", "dropped", "evaluation", "policy", "results")

    def __init_subclass__(cls, **kwargs):
        raise TypeError(
            "MinimizedWitnessPool is a closed derived record and cannot be subclassed")

    def __init__(self, evaluation, results, dropped, contributions, *, _token=None):
        if _token is not _DERIVED:
            raise TypeError(
                "a minimized pool is derived only by minimize_witnesses() from one "
                "complete ConcreteEvaluation")
        if type(evaluation) is not ConcreteEvaluation:
            raise TypeError(
                "a minimized pool needs the exact ConcreteEvaluation it was derived "
                "from")
        if type(results) not in (tuple, list) \
                or any(type(result) is not RunResult for result in results):
            raise TypeError("a minimized pool contains only exact RunResult records")
        results = tuple(results)
        kept = tuple(result.stimulus.fingerprint for result in results)
        if len(set(kept)) != len(kept):
            raise ValueError("a minimized pool cannot contain one stimulus twice")
        if tuple(sorted(results, key=lambda result: stimulus_bytes(result.stimulus))) \
                != results:
            raise ValueError("a minimized pool's results must be in canonical order")
        if type(dropped) not in (tuple, list) or any(
                type(item) is not str or len(item) != 64
                or any(ch not in "0123456789abcdef" for ch in item)
                for item in dropped):
            raise TypeError("dropped members must be full stimulus fingerprints")
        dropped = tuple(dropped)
        kept_set = set(kept)
        if len(set(dropped)) != len(dropped) or kept_set & set(dropped):
            raise ValueError("kept and dropped stimulus fingerprints must be disjoint")

        source = {
            result.stimulus.fingerprint: result for result in evaluation.results}
        if any(source.get(result.stimulus.fingerprint) is not result
               for result in results):
            raise ValueError(
                "every kept run must be the exact immutable RunResult from the named "
                "ConcreteEvaluation")
        ordered_source = tuple(sorted(
            evaluation.results,
            key=lambda result: stimulus_bytes(result.stimulus)))
        expected_dropped = tuple(
            result.stimulus.fingerprint for result in ordered_source
            if result.stimulus.fingerprint not in kept_set)
        if dropped != expected_dropped:
            raise ValueError(
                "dropped must be exactly the canonical complement of the kept runs")
        if type(contributions) is not dict or set(contributions) != set(kept):
            raise ValueError(
                "the contribution map must have exactly one row for every kept run")
        for fingerprint, rows in contributions.items():
            if type(fingerprint) is not str or type(rows) is not tuple \
                    or not rows or any(type(row) is not str or not row for row in rows):
                raise TypeError(
                    "each kept fingerprint maps to a nonempty tuple of feature names")
            if rows != tuple(sorted(set(rows))):
                raise ValueError("contribution feature names are canonical and unique")

        source_features = _features(evaluation)
        expected_contributions = {
            fingerprint: tuple(sorted(source_features[fingerprint]))
            for fingerprint in kept}
        if contributions != expected_contributions:
            raise ValueError(
                "the contribution map must be the exact post-replay evidence of each "
                "kept run")
        source_universe = set().union(*source_features.values())
        kept_universe = set().union(*(
            source_features[fingerprint] for fingerprint in kept))
        if kept_universe != source_universe:
            raise ValueError(
                "the minimized pool does not preserve every post-replay contribution")

        self.evaluation = evaluation.fingerprint
        self.policy = POLICY
        self.results = results
        self.dropped = dropped
        self.contributions = FrozenMap(contributions)
        self._freeze()

    @property
    def stimuli(self):
        return tuple(result.stimulus for result in self.results)

    def as_dict(self):
        return {
            "evaluation": self.evaluation,
            "policy": self.policy,
            "kept": [result.stimulus.fingerprint for result in self.results],
            "dropped": list(self.dropped),
            "contributions": {
                fingerprint: list(rows)
                for fingerprint, rows in self.contributions.items()
            },
        }


def minimize_witnesses(evaluation, *, max_checks=None):
    """Derive a contribution-preserving pool with an optional selection-work limit.

    One check compares a remaining candidate with the uncovered feature set.
    ``max_checks`` bounds greedy selection, not record decoding or wall time.
    Exhaustion refuses the optional pool; it never returns a partial cover.
    """

    if max_checks is not None and (type(max_checks) is not int or max_checks < 1):
        raise ValueError("max_checks must be a positive exact integer or None")

    if type(evaluation) is not ConcreteEvaluation:
        raise Refused(Refusal(
            "minimization-concrete-record-invalid",
            "a secondary witness pool is derived only from a complete typed concrete "
            "evaluation",
            got=type(evaluation).__name__))
    if not evaluation.results:
        raise Refused(Refusal(
            "minimization-stimulus-set-empty",
            "there is no authoritative witness set from which to derive a secondary "
            "pool"))

    # The immutable catalogue is already bound by its digest.  Serialising its complete
    # manifest here can dominate an otherwise tiny secondary-pool reduction, so the
    # read-only check compares the evaluation fields that could actually be mutated.
    before_image = canonical_json(evaluation.as_record_reference())
    before_raw = canonical_json([
        result.trace.as_dict() if result.ran else None
        for result in evaluation.results])
    before = evaluation.fingerprint
    features = _features(evaluation)
    ordered = tuple(sorted(evaluation.results,
                           key=lambda result: stimulus_bytes(result.stimulus)))
    uncovered = set().union(*(features[result.stimulus.fingerprint]
                              for result in ordered))
    selected = []
    remaining = list(ordered)
    checks = 0
    while uncovered:
        if max_checks is not None and checks + len(remaining) > max_checks:
            raise Refused(Refusal(
                "minimization-budget-exhausted",
                "the optional pool exceeded its greedy candidate-check budget",
                max_checks=max_checks, completed_checks=checks,
                next_round_checks=len(remaining)))
        checks += len(remaining)
        # Remaining candidates retain canonical order, so min's first tie is
        # already the smallest stimulus. Serialize that key only for the sort.
        best = min(
            remaining,
            key=lambda result: -len(features[result.stimulus.fingerprint] & uncovered),
        )
        gained = features[best.stimulus.fingerprint] & uncovered
        if not gained:
            raise RuntimeError("witness minimization failed to cover its own universe")
        selected.append(best)
        uncovered.difference_update(gained)
        remaining.remove(best)

    selected.sort(key=lambda result: stimulus_bytes(result.stimulus))
    kept = {result.stimulus.fingerprint for result in selected}
    dropped = tuple(result.stimulus.fingerprint for result in ordered
                    if result.stimulus.fingerprint not in kept)
    after_raw = canonical_json([
        result.trace.as_dict() if result.ran else None
        for result in evaluation.results])
    if canonical_json(evaluation.as_record_reference()) != before_image \
            or after_raw != before_raw \
            or evaluation.fingerprint != before:
        raise RuntimeError("witness minimization mutated the authoritative evaluation")
    return MinimizedWitnessPool(
        evaluation, selected, dropped,
        {result.stimulus.fingerprint:
         tuple(sorted(features[result.stimulus.fingerprint]))
         for result in selected},
        _token=_DERIVED)


def secondary_pool_report(evaluation, *, max_checks=None):
    """Return the closed report shape for the optional post-replay pool.

    The helper is deliberately total over ordinary implementation failures.  A typed
    inability to minimize and a reducer bug both become a typed refusal in the same
    two-variant report algebra; neither is allowed to reinterpret the concrete result.
    Process-level termination is not caught, so callers must persist the concrete
    verdict before invoking this helper.
    """

    try:
        reduced = (minimize_witnesses(evaluation) if max_checks is None
                   else minimize_witnesses(evaluation, max_checks=max_checks))
        return {"status": "available", "pool": reduced.as_dict()}
    except Refused as exc:
        return {"status": "refused", "refusal": exc.refusal.as_dict()}
    except Exception as exc:  # noqa: BLE001 - optional derived-view boundary
        refusal = Refusal(
            "secondary-witness-pool-failed",
            "the optional post-replay pool could not be derived; the full evaluation "
            "and its score remain the authority",
            error=f"{type(exc).__name__}: {exc}")
        return {"status": "refused", "refusal": refusal.as_dict()}
