"""Exact logical queries, kept separately from their per-goal summary."""

from __future__ import annotations

import enum
import json
import math

from .frozen import Frozen
from .query_telemetry import QueryKind, QueryTelemetry

__all__ = [
    "FeasibilityCall",
    "QueryCall",
    "SolverAnswer",
    "decide_setwise",
    "iter_decide_setwise",
    "collect_verdicts",
]


class _FeasibilityDetailError(Exception):
    """A legacy provider answered, but its detail has no finite wire image."""

    __slots__ = ("answer", "why")

    def __init__(self, answer, why):
        super().__init__(why)
        self.answer = answer
        self.why = why


class SolverAnswer(enum.Enum):
    SAT = "sat"
    UNSAT = "unsat"
    UNKNOWN = "unknown"

    def __bool__(self):
        raise TypeError("a solver answer is not a truth value")


class FeasibilityCall(Frozen):
    """One exact domain-inhabitation call, compatible with pair unpacking.

    The public feasibility API historically returned ``(Feasible, detail)``.  This
    closed record retains that unpacking surface while giving the isolated worker a
    place to carry the call's typed telemetry through the wire protocol.
    """

    __slots__ = ("_detail", "answer", "telemetry")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("FeasibilityCall is a closed solver record")

    def __init__(self, answer, detail, *, telemetry=None):
        # Importing here keeps the query/evidence layer independent of frontend code
        # while still requiring the exact closed enum used by BoundedInstance.
        from .instance import Feasible

        if type(answer) is not Feasible:
            raise TypeError("a feasibility call needs the exact Feasible enum")
        try:
            snapshot = _json_snapshot(
                detail, "feasibility detail", allow_tuple=True)
            self._detail = json.dumps(
                snapshot, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False)
        except RecursionError:
            raise ValueError(
                "a feasibility detail has no finite JSON image") from None
        if telemetry is not None:
            if type(telemetry) is not QueryTelemetry:
                raise TypeError(
                    "feasibility telemetry must be the exact QueryTelemetry record")
            if telemetry.kind is not QueryKind.FEASIBILITY:
                raise ValueError("feasibility telemetry has the wrong query kind")
            expected = {
                Feasible.YES: SolverAnswer.SAT.value,
                Feasible.NO: SolverAnswer.UNSAT.value,
                Feasible.UNRESOLVED: SolverAnswer.UNKNOWN.value,
            }[answer]
            if telemetry.outcome != expected:
                raise ValueError(
                    "feasibility telemetry and the exact answer disagree")
        self.answer = answer
        self.telemetry = telemetry
        self._freeze()

    @property
    def detail(self):
        return json.loads(self._detail)

    def __iter__(self):
        return iter((self.answer, self.detail))

    def __len__(self):
        return 2

    def __getitem__(self, index):
        return (self.answer, self.detail)[index]

    def as_dict(self):
        out = {"answer": self.answer.value, "detail": self.detail}
        if self.telemetry is not None:
            out["telemetry"] = self.telemetry.as_dict()
        return out


class QueryCall(Frozen):
    """One logical ``Phi[D,S]`` attempt and its exact outcome.

    One logical attempt may use several dependency-cone solver calls.  It may also
    remain UNKNOWN without launching one when the enclosing artifact budget is already
    spent or when a bound semantic answer is served from cache.  ``solver_calls``
    records physical work in this invocation; it is telemetry, not part of the logical
    answer.  Keeping only the final goal partition would lose which exact attempt
    supports each bounded-unreachable or unresolved status.
    """

    __slots__ = (
        "_witness", "answer", "goals", "reason", "solver_calls", "telemetry")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("QueryCall is a closed solver record and cannot be subclassed")

    def __init__(self, goals, answer, *, witness=None, reason="", telemetry=None,
                 solver_calls=1):
        if type(goals) not in (tuple, list, set, frozenset):
            raise TypeError("an exact query needs a finite built-in goal set")
        goals = tuple(goals)
        if not goals or any(type(goal) is not str or not goal for goal in goals) \
                or len(goals) != len(set(goals)):
            raise ValueError("an exact query needs a nonempty set of goals")
        self.goals = tuple(sorted(goals))
        if type(answer) is not SolverAnswer:
            raise TypeError("an exact query needs a SolverAnswer")
        self.answer = answer
        if type(solver_calls) is not int or solver_calls < 0:
            raise ValueError("a logical exact query records natural solver calls")
        self.solver_calls = solver_calls
        if self.answer is SolverAnswer.SAT:
            if witness is None or reason:
                raise ValueError("SAT carries a model witness and no unknown reason")
            try:
                snapshot = _json_snapshot(witness)
                self._witness = json.dumps(
                    snapshot, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False, allow_nan=False)
            except RecursionError:
                raise ValueError(
                    "a solver witness has no finite JSON image: nested too deeply") \
                    from None
        else:
            if witness is not None:
                raise ValueError("only SAT carries a model witness")
            self._witness = ""
        if type(reason) is not str:
            raise TypeError("a solver reason must be a string")
        if self.answer is SolverAnswer.UNKNOWN and not reason:
            raise ValueError("an UNKNOWN solver answer must carry its reason")
        if self.answer is not SolverAnswer.UNKNOWN and reason != "":
            raise ValueError("only UNKNOWN carries an unresolved reason")
        self.reason = reason
        if telemetry is not None:
            if type(telemetry) is not QueryTelemetry:
                raise TypeError(
                    "query-call telemetry must be the exact QueryTelemetry record")
            if telemetry.goals_asked != self.goals:
                raise ValueError(
                    "query-call telemetry names a different asked goal set")
            if telemetry.outcome != self.answer.value:
                raise ValueError(
                    "query-call telemetry and solver answer disagree")
        self.telemetry = telemetry
        self._freeze()

    @property
    def witness(self):
        return None if not self._witness else json.loads(self._witness)

    def as_dict(self):
        out = {
            "goals": list(self.goals),
            "answer": self.answer.value,
            "solver_calls": self.solver_calls,
        }
        if self.witness is not None:
            out["witness"] = self.witness
        if self.reason:
            out["reason"] = self.reason
        if self.telemetry is not None:
            out["telemetry"] = self.telemetry.as_dict()
        return out

    def with_telemetry(self, telemetry):
        """Copy this semantic call with one non-semantic measurement record."""

        return QueryCall(
            self.goals, self.answer, witness=self.witness, reason=self.reason,
            telemetry=telemetry, solver_calls=self.solver_calls)

    @classmethod
    def from_dict(cls, value):
        """Decode the closed job-wire record shared by Behavior and RTL."""
        if type(value) is not dict or set(value) - {
                "goals", "answer", "solver_calls", "witness", "reason",
                "telemetry"}:
            raise TypeError("a query-call image has only its closed fields")
        if set(value) not in (
                {"goals", "answer", "solver_calls"},
                {"goals", "answer", "solver_calls", "witness"},
                {"goals", "answer", "solver_calls", "reason"},
                {"goals", "answer", "solver_calls", "telemetry"},
                {"goals", "answer", "solver_calls", "witness", "telemetry"},
                {"goals", "answer", "solver_calls", "reason", "telemetry"}):
            raise ValueError("a query-call image has the fields required by its answer")
        return cls(
            value["goals"], SolverAnswer(value["answer"]),
            witness=value.get("witness"), reason=value.get("reason", ""),
            solver_calls=value["solver_calls"],
            telemetry=(None if "telemetry" not in value else
                       QueryTelemetry.from_dict(value["telemetry"])))


def _checked_attempt(batch, attempt):
    """Validate one frontend solve before it can steer the residual worklist.

    A frontend returns the exact :class:`QueryCall` that becomes evidence and the
    subset of this call's goals that its SAT model makes true.  The latter is only a
    worklist fact: canonical replay remains the authority for published hits.
    """

    if type(attempt) is not tuple or len(attempt) != 2:
        raise TypeError(
            "a set-wise solver callback returns one (QueryCall, model-hits) pair")
    call, hits = attempt
    if type(call) is not QueryCall or call.goals != tuple(sorted(batch)):
        raise ValueError(
            "a set-wise solver callback answered a different exact goal set")
    if type(hits) not in (tuple, list, set, frozenset) or any(
            type(goal) is not str or not goal for goal in hits):
        raise TypeError("model hits are a finite collection of exact goal names")
    if len(hits) != len(set(hits)):
        raise ValueError("model-evaluated hits must not contain duplicates")
    hits = tuple(sorted(hits))
    outside = sorted(set(hits) - set(batch))
    if outside:
        raise ValueError(
            f"a set-wise SAT model reports goals outside its query: {outside[:8]}")
    if call.answer is SolverAnswer.SAT:
        if not hits:
            raise ValueError(
                "a SAT disjunction must make at least one of its queried goals true")
    elif hits:
        raise ValueError("only a SAT solver call may carry model-evaluated hits")
    return call, hits


def iter_decide_setwise(goals, solve):
    """Apply the one bounded residual-set strategy shared by every frontend.

    ``solve(batch)`` poses exactly one formula for the nonempty goal tuple ``batch``
    and returns ``(QueryCall, model_hits)``.  One SAT model removes every queried goal
    it makes true, one UNSAT answer settles the entire queried set, and only an UNKNOWN
    multi-goal answer is retried as individual goals. Therefore changing worker count cannot
    silently replace one set query with several singleton queries.

    ``model_hits`` only drives this finite worklist.  The returned transcript retains
    the actual calls, and the closure independently decodes, replays and audits every
    SAT witness before any hit becomes evidence.
    """

    if type(goals) not in (tuple, list, set, frozenset):
        raise TypeError("a set-wise query needs a finite built-in goal collection")
    goals = tuple(goals)
    if not goals or any(type(goal) is not str or not goal for goal in goals) \
            or len(goals) != len(set(goals)):
        raise ValueError("a set-wise query needs distinct nonempty exact goal names")
    goals = tuple(sorted(goals))
    if not callable(solve):
        raise TypeError("a set-wise query needs one exact-solve callback")

    from .instance import Verdict

    def pose(batch):
        call, hits = _checked_attempt(batch, solve(tuple(batch)))
        verdict = Verdict(
            {goal: call.witness for goal in hits},
            batch if call.answer is SolverAnswer.UNSAT else (),
            {batch[0]: call.reason}
            if call.answer is SolverAnswer.UNKNOWN and len(batch) == 1 else {},
            queries=(call,))
        return call, hits, verdict

    remaining = list(goals)
    while remaining:
        call, hits, verdict = pose(remaining)
        yield verdict
        if call.answer is SolverAnswer.SAT:
            hit_set = set(hits)
            remaining = [goal for goal in remaining if goal not in hit_set]
            continue
        if call.answer is SolverAnswer.UNSAT:
            break
        if len(remaining) == 1:
            # This is already the individual goal query. Repeating the same
            # formula with the same solver/budget supplies no new strategy.
            break

        # A set-level UNKNOWN establishes no per-goal status.  Each singleton gets
        # exactly one independent call; only its own UNKNOWN may support unresolved.
        for goal in remaining:
            _call, _hits, verdict = pose((goal,))
            yield verdict
        break


def collect_verdicts(parts):
    """The aggregate API consumes the same incremental query worklist."""
    from .instance import Verdict

    reachable, unreachable, undecided, transcript = {}, set(), {}, []
    for part in parts:
        if type(part) is not Verdict or part.queries is None:
            raise TypeError("incremental queries require transcript-bearing verdicts")
        named = set(part.reachable) | set(part.unreachable) | set(part.undecided)
        if named & (set(reachable) | unreachable | set(undecided)):
            raise ValueError("incremental queries cannot settle the same goal twice")
        reachable.update(part.reachable)
        unreachable.update(part.unreachable)
        undecided.update(part.undecided)
        transcript.extend(part.queries)
    return Verdict(
        reachable, unreachable, undecided, queries=tuple(transcript))


def decide_setwise(goals, solve):
    """Return the complete partition, retaining the incremental strategy exactly."""
    return collect_verdicts(iter_decide_setwise(goals, solve))


def _json_snapshot(value, path="witness", active=None, *, allow_tuple=False):
    """Detach one witness in the exact, finite JSON value domain.

    ``json.dumps`` is deliberately permissive: it writes NaN and infinities,
    coerces non-string mapping keys and accepts Python-only sequence carriers.
    None of those bytes is a faithful JSON witness.  Validation therefore
    precedes serialisation and never falls back to ``repr`` or ``str``.
    """
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return value
    if kind is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if kind not in (dict, list) and not (allow_tuple and kind is tuple):
        raise TypeError(
            f"{path} contains a {kind.__name__}, which has no strict JSON image")

    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError(f"{path} contains a reference cycle")
    active.add(identity)
    try:
        if kind is dict:
            out = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError(
                        f"{path} contains a non-string mapping key")
                out[key] = _json_snapshot(
                    item, f"{path}.{key}", active,
                    allow_tuple=allow_tuple)
            return out
        return [
            _json_snapshot(
                item, f"{path}[{index}]", active,
                allow_tuple=allow_tuple)
            for index, item in enumerate(value)
        ]
    finally:
        active.discard(identity)
