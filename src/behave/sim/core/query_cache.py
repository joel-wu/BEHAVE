"""Semantic answer reuse for certificate-grade bounded queries.

The cache lives beside the bound-instance protocol, not in a frontend.  It sees only
closed feasibility/query records and a worker-authenticated
:class:`~behave.sim.core.query_telemetry.AnalysisCacheBinding`.  Consequently it can
reuse one complete exact answer without retaining a solver, an IR object, or mutable
frontend state.

Only decisive answers are retained.  SAT requests remain requests: callers still
decode, replay and audit the cached witness in the ordinary coordinator/closure path.
UNKNOWN and refusals depend on execution policy and are never semantic cache entries.
The process-local store is deliberately bounded; durable cache publication and its
cross-process locking are separate performance work.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from threading import RLock

from behave.core import runtime_defaults as defaults

from .query_telemetry import (
    AnalysisCacheBinding,
    CacheDisposition,
    QueryKind,
    QueryTelemetry,
    query_kind_for_goals,
)

__all__ = [
    "AnalysisRequestBudget",
    "cached_feasibility",
    "cached_query",
    "clear_analysis_answer_cache",
]


_MAX_ANSWERS = defaults.SIM_ANALYSIS_ANSWER_CACHE_ENTRIES


class AnalysisRequestBudget:
    """One non-semantic logical-request allowance for a bounded-analysis run.

    The owner is mutable because charging is runtime state; its limit is immutable and
    deliberately absent from :class:`AnalysisCacheBinding`.  A cache hit therefore
    saves solver work without manufacturing another request allowance.
    """

    __slots__ = ("_lock", "_maximum", "_used")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("AnalysisRequestBudget is a closed runtime owner")

    def __init__(self, max_requests=None):
        if max_requests is not None and (
                type(max_requests) is not int or max_requests < 1):
            raise ValueError(
                "an analysis request budget is positive or explicitly unlimited")
        object.__setattr__(self, "_maximum", max_requests)
        object.__setattr__(self, "_used", 0)
        object.__setattr__(self, "_lock", RLock())

    def __setattr__(self, name, value):
        raise AttributeError(
            "an analysis request budget has an immutable limit and owner")

    def __delattr__(self, name):
        raise AttributeError("an analysis request budget cannot lose runtime state")

    @property
    def max_requests(self):
        return self._maximum

    @property
    def used(self):
        with self._lock:
            return self._used

    def charge(self, count=1):
        if type(count) is not int or count < 1:
            raise ValueError("a logical analysis charge is a positive integer")
        with self._lock:
            if self.max_requests is not None \
                    and self._used + count > self.max_requests:
                from .refusal import Refusal, Refused

                raise Refused(Refusal(
                    "analysis-logical-request-budget-exhausted",
                    "the bounded analysis has spent its logical request budget; "
                    "cached and uncached requests consume the same allowance",
                    used=self._used, requested=count,
                    maximum=self.max_requests))
            object.__setattr__(self, "_used", self._used + count)


class _AnswerStore:
    __slots__ = ("_entries", "_lock")

    def __init__(self):
        self._entries = OrderedDict()
        self._lock = RLock()

    def get(self, identity):
        with self._lock:
            encoded = self._entries.get(identity)
            if encoded is None:
                return None
            self._entries.move_to_end(identity)
        # Every consumer gets a detached built-in value and runs the ordinary strict
        # decoder again.  A cached Python object is never a substitute for the wire
        # boundary that validates a cold answer.
        return json.loads(encoded)

    def put(self, identity, value):
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False)
        with self._lock:
            self._entries[identity] = encoded
            self._entries.move_to_end(identity)
            while len(self._entries) > _MAX_ANSWERS:
                self._entries.popitem(last=False)

    def discard(self, identity):
        with self._lock:
            self._entries.pop(identity, None)

    def clear(self):
        with self._lock:
            self._entries.clear()


_ANSWERS = _AnswerStore()


def clear_analysis_answer_cache():
    """Clear the process-local answer cache, chiefly for isolated measurements."""

    _ANSWERS.clear()


def _provenance(binding, timeout_ms):
    out = {
        "backend_version": binding.backend_version,
        "tool": binding.tool,
        "tool_version": binding.tool_version,
    }
    if timeout_ms is not None:
        out["timeout_ms"] = timeout_ms
    return out


def _telemetry(binding, *, kind, horizon, goals, outcome, identity,
               disposition, timeout_ms):
    settled = (
        goals if outcome == "unsat"
        or outcome == "unknown" and len(goals) == 1 else ())
    return QueryTelemetry(
        backend=binding.backend, kind=kind,
        semantic_instance=binding.semantic_instance, horizon=horizon,
        goals_asked=goals, goals_settled=settled, outcome=outcome,
        cache_identity=identity, cache=disposition,
        provenance=_provenance(binding, timeout_ms))


def _matching_telemetry(telemetry, binding, *, horizon, kind, goals):
    if telemetry is None:
        return True
    if telemetry.semantic_instance != binding.semantic_instance \
            or telemetry.backend != binding.backend \
            or telemetry.horizon != horizon \
            or telemetry.kind is not kind \
            or telemetry.goals_asked != tuple(sorted(goals)):
        return False
    provenance = telemetry.provenance
    return (provenance.get("backend_version") == binding.backend_version
            and provenance.get("tool") == binding.tool
            and provenance.get("tool_version") == binding.tool_version)


def _decorate_call(call, binding, disposition, identity, horizon, timeout_ms):
    from .query import QueryCall, SolverAnswer

    telemetry = call.telemetry
    kind = query_kind_for_goals(call.goals)
    if not _matching_telemetry(
            telemetry, binding, horizon=horizon, kind=kind,
            goals=call.goals):
        raise ValueError("a logical query and its semantic cache binding disagree")
    if telemetry is None:
        telemetry = _telemetry(
            binding, kind=kind, horizon=horizon,
            goals=call.goals, outcome=call.answer.value, identity=identity,
            disposition=disposition, timeout_ms=timeout_ms)
    else:
        telemetry = telemetry.with_cache(identity, disposition)
    return QueryCall(
        call.goals, call.answer,
        witness=(call.witness if call.answer is SolverAnswer.SAT else None),
        reason=call.reason, telemetry=telemetry,
        solver_calls=(0 if disposition is CacheDisposition.HIT
                      else call.solver_calls))


def _query_image(verdict):
    if verdict.queries is None:
        raise ValueError("an aggregate verdict without a transcript is not cacheable")
    image = verdict.as_record()
    # The logical cache must not carry measurements from an earlier execution.
    for call in image["queries"]:
        call.pop("telemetry", None)
    return image


def _query_from_image(value):
    from .instance import Verdict
    from .query import QueryCall, SolverAnswer

    if type(value) is not dict or set(value) != {
            "reachable", "unreachable", "undecided", "queries"} \
            or type(value["reachable"]) is not dict \
            or type(value["unreachable"]) is not list \
            or type(value["undecided"]) is not dict \
            or type(value["queries"]) is not list:
        raise TypeError("a cached query verdict has its complete finite fields")
    calls = []
    for row in value["queries"]:
        if type(row) is not dict or set(row) not in (
                {"goals", "answer", "solver_calls"},
                {"goals", "answer", "solver_calls", "witness"},
                {"goals", "answer", "solver_calls", "reason"}):
            raise TypeError("a cached logical query has its exact semantic fields")
        calls.append(QueryCall(
            row["goals"], SolverAnswer(row["answer"]),
            witness=row.get("witness"), reason=row.get("reason", ""),
            solver_calls=row["solver_calls"]))
    verdict = Verdict(
        value["reachable"], value["unreachable"], value["undecided"],
        queries=calls)
    if _query_image(verdict) != value:
        raise ValueError("a cached query verdict is not canonical")
    return verdict


def _decorate_verdict(verdict, binding, disposition, identity, horizon,
                      timeout_ms):
    from .instance import Verdict

    if verdict.queries is None:
        return verdict
    return Verdict(
        verdict.reachable, verdict.unreachable, verdict.undecided,
        queries=tuple(
            _decorate_call(
                call, binding, disposition, identity, horizon, timeout_ms)
            for call in verdict.queries))


def _charge_query_remainder(verdict, charge_remainder):
    if charge_remainder is None or verdict.queries is None \
            or len(verdict.queries) < 2:
        return
    charge_remainder(len(verdict.queries) - 1)


def cached_query(binding, horizon, goals, timeout_ms, invoke, *,
                 charge_remainder=None):
    """Run or reuse one exact goal request while preserving its full transcript."""

    from .instance import Verdict
    from .query import SolverAnswer

    if type(binding) is not AnalysisCacheBinding:
        raise TypeError("a cached query needs an exact AnalysisCacheBinding")
    if type(horizon) is not int or horizon < 1:
        raise ValueError("a cached query needs its positive semantic horizon")
    goals = tuple(sorted(goals))
    kind = query_kind_for_goals(goals)
    identity = binding.query_identity(kind, goals)
    cached = _ANSWERS.get(identity)
    if cached is not None:
        try:
            verdict = _query_from_image(cached)
        except (TypeError, ValueError, KeyError):
            # Corruption is a miss, never a semantic answer.  The cold strict provider
            # remains available and can republish the canonical image.
            _ANSWERS.discard(identity)
        else:
            # The caller charged the top-level request before lookup.  Charge every
            # fallback call represented by the transcript before the hit can return.
            _charge_query_remainder(verdict, charge_remainder)
            return _decorate_verdict(
                verdict, binding, CacheDisposition.HIT, identity, horizon,
                timeout_ms)

    verdict = invoke()
    if type(verdict) is not Verdict:
        return verdict
    # Publication happens only after the complete cold transcript fits the same
    # logical request budget a later hit would consume.  An over-budget provider answer
    # therefore cannot seed a cache entry that this run was not allowed to observe.
    _charge_query_remainder(verdict, charge_remainder)
    try:
        decorated = _decorate_verdict(
            verdict, binding, CacheDisposition.MISS, identity, horizon,
            timeout_ms)
        if verdict.queries is not None and verdict.queries and all(
                call.answer in {SolverAnswer.SAT, SolverAnswer.UNSAT}
                for call in verdict.queries):
            _ANSWERS.put(identity, _query_image(verdict))
        return decorated
    except (TypeError, ValueError, KeyError):
        # Instrumentation/cache publication has no authority to replace a solver
        # answer.  Return the original exact record without cache telemetry.
        return verdict


def _feasibility_image(call):
    return {name: value for name, value in call.as_dict().items()
            if name != "telemetry"}


def _feasibility_from_image(value):
    from .instance import Feasible
    from .query import FeasibilityCall

    if type(value) is not dict or set(value) != {"answer", "detail"}:
        raise TypeError("a cached feasibility answer has its exact semantic fields")
    return FeasibilityCall(Feasible(value["answer"]), value["detail"])


def cached_feasibility(binding, horizon, timeout_ms, invoke):
    """Run or reuse one exact domain-inhabitation request."""

    from .instance import Feasible
    from .query import FeasibilityCall

    if type(binding) is not AnalysisCacheBinding:
        raise TypeError("cached feasibility needs an exact AnalysisCacheBinding")
    if type(horizon) is not int or horizon < 1:
        raise ValueError("cached feasibility needs its positive semantic horizon")
    identity = binding.query_identity(QueryKind.FEASIBILITY)
    cached = _ANSWERS.get(identity)
    if cached is not None:
        try:
            call = _feasibility_from_image(cached)
            telemetry = _telemetry(
                binding, kind=QueryKind.FEASIBILITY, horizon=horizon, goals=(),
                outcome={
                    Feasible.YES: "sat", Feasible.NO: "unsat",
                    Feasible.UNRESOLVED: "unknown",
                }[call.answer], identity=identity,
                disposition=CacheDisposition.HIT, timeout_ms=timeout_ms)
            return FeasibilityCall(call.answer, call.detail, telemetry=telemetry)
        except (TypeError, ValueError, KeyError):
            _ANSWERS.discard(identity)

    call = invoke()
    if type(call) is not FeasibilityCall:
        return call
    try:
        telemetry = call.telemetry
        if not _matching_telemetry(
                telemetry, binding, horizon=horizon,
                kind=QueryKind.FEASIBILITY, goals=()):
            raise ValueError(
                "a feasibility call and its semantic cache binding disagree")
        outcome = {
            Feasible.YES: "sat", Feasible.NO: "unsat",
            Feasible.UNRESOLVED: "unknown",
        }[call.answer]
        telemetry = (
            _telemetry(
                binding, kind=QueryKind.FEASIBILITY, horizon=horizon, goals=(),
                outcome=outcome, identity=identity,
                disposition=CacheDisposition.MISS, timeout_ms=timeout_ms)
            if telemetry is None else
            telemetry.with_cache(identity, CacheDisposition.MISS))
        decorated = FeasibilityCall(call.answer, call.detail, telemetry=telemetry)
        if call.answer in {Feasible.YES, Feasible.NO}:
            _ANSWERS.put(identity, _feasibility_image(call))
        return decorated
    except (TypeError, ValueError, KeyError):
        return call
