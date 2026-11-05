import pytest

from behave.sim.core import closure
from behave.sim.core.audit import AuditedWitness
from behave.sim.core.audit_plan import AuditPlan
from behave.sim.core.catalogue import COVERAGE, CatalogueManifest, Goal
from behave.sim.core.domain import TraceDomain
from behave.sim.core.instance import (
    BoundedInstance,
    Feasible,
    Replay,
    StepOutput,
    Verdict,
)
from behave.sim.core.query import FeasibilityCall, QueryCall, SolverAnswer
from behave.sim.core.query_cache import (
    AnalysisRequestBudget,
    clear_analysis_answer_cache,
)
from behave.sim.core.query_telemetry import (
    AnalysisCacheBinding,
    CacheDisposition,
    QueryKind,
    QueryTelemetry,
    analysis_semantic_instance,
)
from behave.sim.core.refusal import Refused

GOAL = Goal(
    "cache.goal", COVERAGE, family="cache-test", provider="test",
    policy="cache-test/1")


def _binding(**overrides):
    values = {
        "semantic_instance": analysis_semantic_instance(
            artifact="artifact:a", task_contract="task:a", domain="domain:a",
            catalogue="catalogue:a", representation="representation:a",
            encoding="encoding:a"),
        "backend": "test-z3",
        "backend_version": "test-backend/1",
        "tool": "z3",
        "tool_version": "4.test",
    }
    values.update(overrides)
    return AnalysisCacheBinding(**values)


class _CachedProvider:
    def __init__(self, binding=None):
        self.binding = binding or _binding()
        self.query_calls = 0
        self.feasibility_calls = 0
        self.audit_request_calls = 0
        self.replay_calls = 0
        self.manifest = CatalogueManifest((GOAL,))

    def analysis_cache_binding(self):
        return self.binding

    def goals(self):
        return {GOAL.id: GOAL}

    def audit_plan(self):
        return AuditPlan()

    def query(self, goals, steps, timeout_ms=None):
        assert steps == 1
        self.query_calls += 1
        trace = [{"a": 1}]
        return Verdict(
            {GOAL.id: trace}, (), {},
            queries=(QueryCall(goals, SolverAnswer.SAT, witness=trace),))

    def feasible(self, steps, timeout_ms=None):
        assert steps == 1
        self.feasibility_calls += 1
        return FeasibilityCall(Feasible.YES, [{"a": 0}])

    def audit_request(self, trace, domain):
        self.audit_request_calls += 1
        return AuditedWitness(
            domain=domain, states=({}, {}), inputs=trace, outputs=((),),
            hit_sets=({GOAL.id},))

    def replay(self, trace):
        self.replay_calls += 1
        return Replay(
            {GOAL.id}, outputs=(StepOutput(),), state={}, steps=len(trace))


def _bound(provider, request_budget=None):
    domain = TraceDomain(
        1, contract={"a": (0, 1)}, frontend="test", artifact="artifact:a",
        task_contract="task:a")
    return BoundedInstance(
        provider, domain, analysis_cache=provider.binding,
        request_budget=request_budget)


def test_cold_and_warm_queries_have_identical_semantics_and_reaudit_sat():
    clear_analysis_answer_cache()
    provider = _CachedProvider()
    bound = _bound(provider)

    cold = closure.close_over(
        bound, catalogue_manifest=provider.manifest, audit_plan=AuditPlan(),
        timeout_ms=1)
    warm = closure.close_over(
        bound, catalogue_manifest=provider.manifest, audit_plan=AuditPlan(),
        timeout_ms=999_999)

    assert cold.summary() == warm.summary()
    assert provider.query_calls == 1
    # The cached SAT model remains a decoded request.  Both the cold and warm path
    # audit it and independently replay it before it can settle the goal.
    assert provider.audit_request_calls == 2
    assert provider.replay_calls == 2
    assert cold.queries[0].telemetry.cache is CacheDisposition.MISS
    assert warm.queries[0].telemetry.cache is CacheDisposition.HIT
    assert cold.queries[0].solver_calls == 1
    assert warm.queries[0].solver_calls == 0
    assert cold.queries[0].telemetry.cache_identity \
        == warm.queries[0].telemetry.cache_identity
    # Physical work is invocation telemetry, not part of the logical query identity.
    assert cold.queries[0].query_id == warm.queries[0].query_id


def test_feasibility_cache_ignores_timeout_but_retains_a_logical_call_record():
    clear_analysis_answer_cache()
    provider = _CachedProvider()
    bound = _bound(provider)

    cold = bound.feasible(timeout_ms=3)
    warm = bound.feasible(timeout_ms=30_000)

    assert cold.answer is warm.answer is Feasible.YES
    assert cold.detail == warm.detail == [{"a": 0}]
    assert provider.feasibility_calls == 1
    assert cold.telemetry.cache is CacheDisposition.MISS
    assert warm.telemetry.cache is CacheDisposition.HIT
    assert cold.telemetry.cache_identity == warm.telemetry.cache_identity


def test_warm_answer_consumes_the_same_finite_request_budget_as_cold():
    clear_analysis_answer_cache()
    provider = _CachedProvider()
    budget = AnalysisRequestBudget(2)
    bound = _bound(provider, request_budget=budget)

    cold = bound.query((GOAL.id,), timeout_ms=1)
    warm = bound.query((GOAL.id,), timeout_ms=30_000)

    assert provider.query_calls == 1
    assert cold.queries[0].telemetry.cache is CacheDisposition.MISS
    assert warm.queries[0].telemetry.cache is CacheDisposition.HIT
    assert budget.used == 2
    with pytest.raises(AttributeError, match="immutable limit"):
        budget.max_requests = 3
    with pytest.raises(
            Refused, match="analysis-logical-request-budget-exhausted"):
        bound.query((GOAL.id,), timeout_ms=30_000)
    assert provider.query_calls == 1


def test_over_budget_cold_transcript_cannot_seed_a_warm_answer():
    clear_analysis_answer_cache()
    provider = _CachedProvider()
    goals = ("cache.g0", "cache.g1", "cache.g2")

    def multi_call(asked, steps, timeout_ms=None):
        assert tuple(asked) == goals and steps == 1
        provider.query_calls += 1
        first, second = ([{"a": 0}], [{"a": 1}])
        return Verdict(
            {goals[0]: first, goals[1]: second}, (goals[2],), {},
            queries=(
                QueryCall(goals, SolverAnswer.SAT, witness=first),
                QueryCall(goals[1:], SolverAnswer.SAT, witness=second),
                QueryCall((goals[2],), SolverAnswer.UNSAT),
            ))

    provider.query = multi_call
    too_small = _bound(provider, request_budget=AnalysisRequestBudget(2))
    with pytest.raises(
            Refused, match="analysis-logical-request-budget-exhausted"):
        too_small.query(goals)
    assert provider.query_calls == 1

    # A fresh run with enough allowance must call the provider.  A HIT here would mean
    # the refused cold run published before its complete transcript was charged.
    enough = _bound(provider, request_budget=AnalysisRequestBudget(3))
    got = enough.query(goals)
    assert provider.query_calls == 2
    assert all(
        call.telemetry.cache is CacheDisposition.MISS for call in got.queries)


@pytest.mark.parametrize("mismatch", [
    "semantic_instance", "backend", "horizon", "kind",
    "backend_version", "tool", "tool_version",
])
def test_incompatible_worker_telemetry_is_never_cached(mismatch):
    clear_analysis_answer_cache()
    provider = _CachedProvider()
    binding = provider.binding

    def query(_goals, steps, timeout_ms=None):
        provider.query_calls += 1
        provenance = {
            "backend_version": binding.backend_version,
            "tool": binding.tool,
            "tool_version": binding.tool_version,
        }
        values = {
            "backend": binding.backend,
            "kind": QueryKind.SINGLETON,
            "semantic_instance": binding.semantic_instance,
            "horizon": steps,
        }
        if mismatch in provenance:
            provenance[mismatch] += ":changed"
        elif mismatch == "horizon":
            values[mismatch] += 1
        elif mismatch == "kind":
            values[mismatch] = QueryKind.LOCAL_PROBE
        else:
            values[mismatch] += ":changed"
        trace = [{"a": 1}]
        telemetry = QueryTelemetry(
            **values, goals_asked=(GOAL.id,), outcome="sat",
            provenance=provenance)
        return Verdict(
            {GOAL.id: trace}, (), {},
            queries=(QueryCall(
                (GOAL.id,), SolverAnswer.SAT, witness=trace,
                telemetry=telemetry),))

    provider.query = query
    bound = _bound(provider)
    first = bound.query((GOAL.id,))
    second = bound.query((GOAL.id,))

    assert provider.query_calls == 2, mismatch
    assert first.queries[0].telemetry.cache is None
    assert second.queries[0].telemetry.cache is None


def test_every_backend_or_tool_component_invalidates_the_answer_key():
    baseline = _binding()
    changes = {
        "semantic_instance": baseline.semantic_instance + ":changed",
        "backend": "another-z3",
        "backend_version": "test-backend/2",
        "tool": "another-solver",
        "tool_version": "5.test",
    }
    baseline_key = baseline.query_identity(
        kind=QueryKind.SINGLETON, goals=(GOAL.id,))
    for name, value in changes.items():
        changed = _binding(**{name: value})
        assert changed.identity != baseline.identity, name
        assert changed.query_identity(
            QueryKind.SINGLETON, (GOAL.id,)) != baseline_key, name


def test_cache_binding_round_trip_rederives_every_digest():
    binding = _binding()
    assert AnalysisCacheBinding.from_dict(binding.as_dict()).as_dict() \
        == binding.as_dict()

    forged = binding.as_dict()
    forged["tool_digest"] += ":forged"
    with pytest.raises(ValueError, match="inconsistent digests"):
        AnalysisCacheBinding.from_dict(forged)


def test_every_bounded_semantic_component_invalidates_the_answer_key():
    components = {
        "artifact": "artifact:a",
        "task_contract": "task:a",
        "domain": "domain:a",
        "catalogue": "catalogue:a",
        "representation": "representation:a",
        "encoding": "encoding:a",
    }
    baseline = _binding(semantic_instance=analysis_semantic_instance(**components))
    baseline_key = baseline.query_identity(
        QueryKind.SINGLETON, (GOAL.id,))

    for name in components:
        changed = dict(components)
        changed[name] += ":changed"
        binding = _binding(
            semantic_instance=analysis_semantic_instance(**changed))
        assert binding.query_identity(
            QueryKind.SINGLETON, (GOAL.id,)) != baseline_key, name


def test_unknown_feasibility_is_execution_policy_and_is_not_cached():
    clear_analysis_answer_cache()
    provider = _CachedProvider()

    def unresolved(steps, timeout_ms=None):
        provider.feasibility_calls += 1
        return FeasibilityCall(Feasible.UNRESOLVED, "the budget expired")

    provider.feasible = unresolved
    bound = _bound(provider)
    first = bound.feasible(timeout_ms=1)
    second = bound.feasible(timeout_ms=2)

    assert first.answer is second.answer is Feasible.UNRESOLVED
    assert provider.feasibility_calls == 2
    assert first.telemetry.cache is second.telemetry.cache is CacheDisposition.MISS
