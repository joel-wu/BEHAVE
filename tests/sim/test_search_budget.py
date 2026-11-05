"""Time is a search resource, never score or reachability evidence."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from behave.isolation.preparation_control import (
    PreparationCancelled,
    check_preparation,
    preparation_scope,
)
from behave.sim import api
from behave.sim import search_policy as budget_module
from behave.sim.deferred_generation import deferred_provider
from behave.sim.generation import generate_independently
from behave.sim.search_policy import (
    SearchBudget,
    SearchPolicy,
    SearchStopped,
    active_search,
    remaining_timeout,
    remaining_solver_ms,
    search_query_scope,
    search_scope,
)
from tests.support.device_boundary import prepare_device_command

from .test_api_path import Doubling, _ref, contract, realization
from .test_generation_scheduler import _Provider, _replace, _request, _spec, _stimulus


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(budget_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    return clock


class TimedProvider(_Provider):
    def __init__(self, clock, name, *, select_s=0, probe_s=0):
        super().__init__({((0,),): [_request("one"), _request("two", rank=1)]},
                         {"one": lambda seed: _replace(seed, 0, 1),
                          "two": lambda seed: _replace(seed, 0, 2)})
        self.name, self.clock = name, clock
        self.select_s, self.probe_s = select_s, probe_s
        self.closed = 0
        self.received_timeout = []

    @contextmanager
    def search_scope(self, *, probe_budget):
        try:
            yield
        finally:
            self.closed += 1

    def select(self, seed, *, targets=None):
        self.received_timeout.append(remaining_timeout(1000))
        self.clock.now += self.select_s
        return super().select(seed)

    def probe(self, seed, request):
        self.received_timeout.append(remaining_timeout(1000))
        self.clock.now += self.probe_s
        return super().probe(seed, request)


def run(providers, policy):
    seed = _stimulus([0])
    return generate_independently((seed,), providers, contract=contract(),
                                 generation_spec=_spec(seed), search_policy=policy)


def reports(result):
    return [search.as_dict()["telemetry"]["search_budget"] for search in result.searches]


@pytest.mark.parametrize("options", [
    {"time_budget_s": -1}, {"time_budget_s": True}, {"time_budget_s": float("inf")},
    {"time_budget_s": float("nan")}, {"max_probes": True}, {"max_probes": -1},
    {"time_budget_s": None, "max_probes": None},
    {"debug": 1},
])
def test_policy_rejects_unbounded_or_invalid_options(options):
    with pytest.raises(ValueError):
        SearchPolicy(**options)


def test_public_policy_round_trip_is_strict():
    policy = api.SearchPolicy(time_budget_s=12.5, max_probes=3, debug=True)
    assert api.SearchPolicy.from_dict(policy.as_dict()).as_dict() == policy.as_dict()
    with pytest.raises(ValueError):
        api.SearchPolicy.from_dict({**policy.as_dict(), "allocation": "per-direction"})


def test_query_budget_follows_pending_work_and_does_not_renew_stage(clock):
    lease = SearchBudget(SearchPolicy(time_budget_s=120)).direction(1)
    with search_scope(lease):
        clock.now = 20  # Includes preparation and time spent waiting for a worker.
        with search_query_scope(4):
            assert remaining_solver_ms(None) == 25_000
            assert remaining_solver_ms(7_000) == 7_000
            assert remaining_solver_ms(90_000) == 25_000
        clock.now = 80
        with search_query_scope(2):
            assert remaining_solver_ms(None) == 20_000
        assert remaining_solver_ms(None) == 40_000
        clock.now = 119.9999
        assert remaining_solver_ms(None) == 1
        clock.now = 120
        with pytest.raises(SearchStopped):
            remaining_solver_ms(None)


def test_standalone_query_needs_an_explicit_cap():
    with pytest.raises(ValueError, match="finite owning stage"):
        remaining_solver_ms(None)
    assert remaining_solver_ms(42_000) == 42_000
    for invalid in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            remaining_solver_ms(invalid)


def test_resumed_query_uses_measured_construction_cost_but_never_renews_deadline(clock):
    lease = SearchBudget(SearchPolicy(time_budget_s=120)).direction(1)
    with search_scope(lease):
        clock.now = 20
        with search_query_scope(100, construction_seconds=20):
            assert remaining_solver_ms(None) == 21_000
            assert remaining_solver_ms(7_000) == 7_000
            clock.now = 110
            assert remaining_solver_ms(None) == 10_000
            clock.now = 120
            with pytest.raises(SearchStopped):
                remaining_solver_ms(None)
    for invalid in (-1, True, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            with search_query_scope(2, construction_seconds=invalid):
                pass


def test_scheduler_retains_cost_only_for_the_same_unfinished_question(clock, monkeypatch):
    from behave.sim import generation
    from behave.sim.generation_protocol import Outcome, ProbeResult, Scope

    monkeypatch.setattr(generation, "time", budget_module.time)
    provider = TimedProvider(clock, "one")
    caps, seen = [], []

    def probe(seed, request):
        caps.append(remaining_solver_ms(None))
        seen.append(request.occurrence)
        if len(seen) <= 2:
            clock.now += 20 if len(seen) == 1 else 10
            # Provider-reported timing does not control resource admission.
            return ProbeResult(Outcome.CONSTRUCTING, Scope.EXACT_LOCAL_HIT, solve_ms=1e9)
        return ProbeResult(Outcome.UNKNOWN, Scope.EXACT_LOCAL_HIT)

    provider.probe = probe
    result = run([provider], SearchPolicy(time_budget_s=120))
    assert seen == ["one", "one", "one", "two"]
    assert caps == [60_000, 70_000, 75_000, 90_000]
    answers = result.searches[0].as_dict()["telemetry"]["answers"]
    assert [row["construction_seconds"] for row in answers] == [0, 20, 30, 0]


def test_scheduler_passes_remaining_query_count_into_executing_worker(clock):
    provider = TimedProvider(clock, "one")
    caps = []
    original = provider.probe

    def probe(seed, request):
        caps.append(remaining_solver_ms(None))
        return original(seed, request)

    provider.probe = probe
    run([provider], SearchPolicy(time_budget_s=120))
    assert caps[:2] == [60_000, 120_000]


def test_narrowing_targets_preserves_the_unfinished_construction_cost(clock, monkeypatch):
    from behave.sim import generation
    from behave.sim.generation_protocol import Outcome, ProbeResult, Scope

    monkeypatch.setattr(generation, "time", budget_module.time)
    provider = TimedProvider(clock, "one")
    provider.selections = {((0,),): [
        _request("one", targets=("g1", "g2")), _request("two", targets=("g2",), rank=1)]}
    caps, targets = [], []
    provider.query_targets = lambda goals, seed: tuple(
        goal for goal in goals if not targets or goal == "g2")

    def probe(seed, request):
        caps.append(remaining_solver_ms(None))
        targets.append(request.targets)
        if len(targets) == 1:
            clock.now += 20
            return ProbeResult(Outcome.CONSTRUCTING, Scope.EXACT_LOCAL_HIT)
        return ProbeResult(Outcome.UNKNOWN, Scope.EXACT_LOCAL_HIT)

    provider.probe = probe
    result = run([provider], SearchPolicy(time_budget_s=120))
    assert targets == [("g1", "g2"), ("g2",), ("g2",)]
    assert caps == [60_000, 70_000, 100_000]
    answers = result.searches[0].as_dict()["telemetry"]["answers"]
    assert [row["construction_seconds"] for row in answers] == [0, 20, 0]


def test_default_six_hundred_seconds_is_shared_not_per_direction(clock):
    policy = api.SearchPolicy()
    assert policy.time_budget_s == api.DEFAULT_SEARCH_SECONDS == 600.0
    budget = SearchBudget(policy)
    first = budget.direction(2)
    assert first.deadline == 300
    # The first direction finishes early. The next inherits remaining time,
    # not a fresh 600-second allowance.
    clock.now = 100
    second = budget.direction(1)
    assert second.deadline == budget.deadline == 600
    assert second.deadline - second.started == 500


def test_deadline_preserves_accepted_candidate_but_discards_late_reply(clock):
    provider = TimedProvider(clock, "one", select_s=1, probe_s=2)
    # The completed first question yields to its new seed's one-second Select
    # before the parent resumes. Leave time to start, but not finish, reply 2.
    result = run([provider], SearchPolicy(time_budget_s=5))
    assert [dict(s.queues)["in"] for s in result.stimuli] == [((0,),), ((1,),)]
    report = reports(result)[0]
    assert report["stop_reason"] == "time-budget-exhausted"
    assert (report["probes_started"], report["probes_completed"], report["probes_cancelled"]) == (2, 1, 1)
    assert provider.received_timeout == [5, 4, 2, 1]
    assert provider.closed == 1 and active_search() is None


def test_directions_share_time_and_unused_share_moves_forward(clock):
    first = TimedProvider(clock, "a", select_s=1)
    first.selections = {}
    second = TimedProvider(clock, "b", select_s=2, probe_s=3)
    result = run([first, second], SearchPolicy(time_budget_s=10))
    a, b = reports(result)
    assert a["allocated_s"] == 5 and b["allocated_s"] == 9
    assert b["total_elapsed_s"] == 11  # Deliberate noncooperative test provider.
    assert first.received_timeout == [5]
    assert second.received_timeout == [9, 7, 4, 2]
    assert all(provider.closed == 1 for provider in (first, second))


def test_unavailable_direction_reports_scope_and_releases_its_time(clock):
    from behave.sim.generation import UnavailableGenerationProvider

    unavailable = UnavailableGenerationProvider("missing", "test/1",
        api.Refusal("no-representation", "the direction cannot bind its exact product"))
    remaining = TimedProvider(clock, "available", select_s=1)
    remaining.selections = {}
    seed = _stimulus([0])
    task = api.admit(contract(), realization(), reference=_ref())
    generated = api.generate_stimuli(task, (seed,), (unavailable, remaining),
        frontend_catalogue=api.CatalogueManifest(()), generation_spec=_spec(seed),
        search_policy=SearchPolicy(time_budget_s=10))
    summary = generated.search_summary
    assert summary["stop_reason"] == "provider-unavailable"
    directions = {row["provider"]: row for row in summary["directions"]}
    assert directions["missing"]["stop_reason"] == "provider-unavailable"
    assert directions["available"]["stop_reason"] == "queue-empty"
    assert directions["available"]["allocated_s"] == 10
    assert summary["probes_started"] == summary["probes_completed"] == 0
    assert remaining.closed == 1 and active_search() is None
    assert generated.stimuli == generated.baseline


def test_probe_cap_is_total_not_per_direction(clock):
    first, second = TimedProvider(clock, "a"), TimedProvider(clock, "b")
    result = run([first, second], SearchPolicy(time_budget_s=None, max_probes=1))
    assert len(first.probes) == 1 and second.probes == []
    assert sum(row["probes_started"] for row in reports(result)) == 1
    assert all(row["stop_reason"] == "probe-budget-exhausted" for row in reports(result))


@pytest.mark.parametrize("policy, reason", [
    (SearchPolicy(time_budget_s=4), "time-budget-exhausted"),
    (SearchPolicy(time_budget_s=20, max_probes=1), "probe-budget-exhausted"),
    (SearchPolicy(time_budget_s=20), "queue-empty"),
])
def test_stopped_search_retains_provider_cost_after_cleanup(clock, policy, reason):
    provider = TimedProvider(clock, "one", select_s=1, probe_s=2)
    provider.execution_cost = lambda: {"probes": len(provider.probes),
                                       "closed": provider.closed}
    result = run([provider], policy)
    telemetry = result.searches[0].as_dict()["telemetry"]
    assert telemetry["termination"] == reason
    assert telemetry["execution_cost"] == {"probes": len(provider.probes), "closed": 1}
    assert telemetry["context_cache"] == provider.cache_telemetry().as_dict()


def test_debug_failure_retains_diagnostics_and_closes_worker(clock):
    provider = TimedProvider(clock, "one")

    def fail(_seed):
        raise RuntimeError("test selection failed")

    provider.select = fail
    provider.execution_cost = lambda: {"closed": provider.closed}
    with pytest.raises(api.Refused) as caught:
        run([provider], SearchPolicy(time_budget_s=20, debug=True))
    refusal = caught.value.refusal.as_dict()
    assert refusal["kind"] == "generation-debug-stop"
    telemetry = refusal["detail"]["search"]["telemetry"]
    assert telemetry["termination"] == "search-failed"
    assert telemetry["execution_cost"] == {"closed": 1}


@pytest.mark.parametrize("count", [0, 1, 2])
def test_legacy_probe_count_remains_per_direction_without_a_time_lease(clock, count):
    first, second = (TimedProvider(clock, name, select_s=10000, probe_s=10000)
                     for name in ("a", "b"))
    seed = _stimulus([0])
    result = generate_independently((seed,), (first, second), contract=contract(),
        generation_spec=_spec(seed), probe_budget=count)
    assert [len(p.probes) for p in (first, second)] == [count, count]
    assert all(timeout == 1000 for p in (first, second) for timeout in p.received_timeout)
    assert all(p.closed == 1 for p in (first, second))
    assert seed in result.stimuli
    assert active_search() is None


def test_count_only_scope_preserves_caller_preparation_cancellation():
    def stop():
        raise PreparationCancelled()

    with preparation_scope(stop):
        with pytest.raises(PreparationCancelled), search_scope(None):
            assert active_search() is None
            assert remaining_timeout(1000) == 1000
            check_preparation()
        assert active_search() is None
        with pytest.raises(PreparationCancelled):
            check_preparation()


def test_timed_scope_checks_preparation_and_restores_context(clock):
    lease = SearchBudget(SearchPolicy(time_budget_s=1)).direction(1)
    with pytest.raises(SearchStopped), search_scope(lease):
        assert active_search() is lease
        check_preparation()
        clock.now = 2
        check_preparation()
    assert active_search() is None
    check_preparation()


def test_remaining_timeout_never_grants_time_past_the_deadline(clock):
    lease = SearchBudget(SearchPolicy(time_budget_s=1)).direction(1)
    with search_scope(lease):
        clock.now = 1 - 1e-7
        assert remaining_timeout(10) == lease.deadline - clock.now
        clock.now = lease.deadline
        with pytest.raises(SearchStopped, match="time-budget-exhausted"):
            remaining_timeout(10)


@pytest.mark.parametrize("seconds", [0, 2])
def test_preparation_is_inside_budget_and_zero_budget_never_calls_factory(clock, seconds):
    made = []

    def prepare(*, execution, baseline):
        made.append(remaining_timeout(300))
        clock.now += 3
        return TimedProvider(clock, "a")

    recipe = deferred_provider("a", TimedProvider.policy, {"source": "frozen"}, prepare)
    result = run([recipe], SearchPolicy(time_budget_s=seconds))
    assert result.stimuli == result.baseline
    assert made == ([] if seconds == 0 else [2])
    assert reports(result)[0]["stop_reason"] == "time-budget-exhausted"
    assert recipe.prepared_image()["name"] == "a"


def test_search_does_not_eagerly_replay_all_seeds(clock):
    provider = TimedProvider(clock, "a", select_s=3)

    def bulk(_seeds):
        raise AssertionError("a timed search must not prepare unused seeds")

    provider.prepare_seeds = bulk
    result = run([provider], SearchPolicy(time_budget_s=2))
    assert result.stimuli == result.baseline and not provider.probes
    assert reports(result)[0]["phases_s"]["select"] == 3


def test_timeout_control_flow_cannot_be_misreported_as_candidate_fault(clock):
    lease = SearchBudget(SearchPolicy(time_budget_s=1)).direction(1)
    with search_scope(lease):
        clock.now = 1
        with pytest.raises(SearchStopped):
            try:
                remaining_timeout(100)
            except Exception:  # noqa: BLE001 - exercise ordinary fault containment
                pytest.fail("budget cancellation must bypass ordinary fault wrappers")


@pytest.mark.parametrize("broken_score", [False, True])
def test_public_command_finishes_scoring_after_search_timeout(clock, tmp_path, monkeypatch, broken_score):
    from behave.sim import evaluation as evaluation_service

    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    seed = _stimulus([0])
    provider = TimedProvider(clock, "a", probe_s=3)
    request = prepare_device_command(
        task, Doubling(), run_directory=tmp_path / "result",
        baseline=api.build_baseline(contract(), _spec(seed),
            policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1)),
        providers=(provider,), generation_spec=_spec(seed),
        search_policy=SearchPolicy(time_budget_s=2), reference=reference, run_analysis=False)
    original = evaluation_service.evaluate_generated
    called = []

    def evaluate(*args, **kwargs):
        assert active_search() is None and provider.closed == 1
        called.append(True)
        if broken_score:
            raise RuntimeError("scoring did not finish")
        return original(*args, **kwargs)

    monkeypatch.setattr(evaluation_service, "evaluate_generated", evaluate)
    result = api.handle_command(request)
    assert called == [True]
    assert result.generated.stimuli == result.generated.baseline
    assert result.generated.searches[0].as_dict()["telemetry"]["termination"] == "time-budget-exhausted"
    if broken_score:
        assert result.status is not api.CommandStatus.EVALUATED
    else:
        assert result.status is api.CommandStatus.EVALUATED and result.evaluation.passed


@pytest.mark.parametrize("debug", (False, True))
def test_public_search_cpu_wait_expires_without_losing_baseline_or_poisoning_pool(
        tmp_path, monkeypatch, debug):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from behave.sim import evaluation
    from behave.sim.cpu_admission import reserve_cpus

    from .test_command_handler import _BaselineOnly
    from .test_command_handler import _request as command_request

    occupied, release = threading.Event(), threading.Event()
    calls, holders = [], []
    original = evaluation.run

    def replay(*args, **kwargs):
        calls.append("baseline")
        assert active_search() is None
        return original(*args, **kwargs)

    monkeypatch.setattr(evaluation, "run", replay)
    with api.CpuPool(1) as pool, ThreadPoolExecutor(max_workers=1) as executor:
        def occupy():
            with api.cpu_scope(pool.endpoint), reserve_cpus(1):
                occupied.set()
                assert release.wait(10)

        class WaitingSearch(_BaselineOnly):
            def select(self, seed, *, targets=None):
                assert calls == ["baseline"]
                calls.append("select")
                holders.append(executor.submit(occupy))
                assert occupied.wait(10)
                with reserve_cpus(1):
                    pytest.fail("search must not launch work while another caller holds the CPU")

        try:
            request = command_request(tmp_path, provider=WaitingSearch(), probe_budget=None,
                search_policy=SearchPolicy(time_budget_s=0.25, debug=debug), run_analysis=False)
            with api.cpu_scope(pool.endpoint) as usage:
                result = api.handle_command(request)
            assert calls == ["baseline", "select"]
            assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
            assert result.decision.authoritative and result.decision.passed
            assert result.evaluation.complete
            assert result.evaluation.execution_cost["reuse"]["completed_runs"] == 1
            assert result.generated.stimuli == result.generated.baseline
            assert result.generated.search_summary["stop_reason"] == "time-budget-exhausted"
            assert result.generated.search_summary["probes_started"] == 0
            assert any(row["status"] == "time-budget-exhausted"
                       for row in usage.as_dict()["reservations"])
            assert not pool.snapshot()["quarantined"]
        finally:
            release.set()
            for holder in holders:
                holder.result(10)
        with api.cpu_scope(pool.endpoint), reserve_cpus(1):
            assert pool.snapshot()["reserved"] == 1


def test_budgeted_worker_uses_remaining_timeout_and_late_return_stops(clock, monkeypatch):
    from behave.sim import worker

    received = []
    def fake(_name, _payload, timeout_s, **_kwargs):
        received.append(timeout_s)
        clock.now += 2
        return {"late": True}

    with worker._testing_job_runner(fake):
        lease = SearchBudget(SearchPolicy(time_budget_s=1)).direction(1)
        with search_scope(lease), pytest.raises(SearchStopped):
            worker.run_job_strict("echo", {}, 300)
    assert received == [1]


def test_inflight_session_is_closed_by_sim_at_deadline(monkeypatch):
    import io
    import json
    import threading

    from behave.sim import job_session

    released = threading.Event()
    process = SimpleNamespace(stdin=io.BytesIO(), stderr=io.BytesIO(), closed=0)
    process.stdout = io.BytesIO((json.dumps({
        "version": "test/1", "id": 0, "ok": True, "wall_ms": 0, "result": {}}) + "\n").encode())

    def close():
        process.closed += 1
        released.set()

    class BlockedReply:
        def readline(self, _limit):
            assert released.wait(2), "Sim did not cancel the blocked transport"
            return b""

    process.close = close
    limits = []
    monkeypatch.setattr(job_session, "_freeze_inputs", lambda _n, p: (p, ()))
    monkeypatch.setattr(job_session, "_job_spec", lambda *a, **kw: limits.append(kw))
    monkeypatch.setattr(job_session, "start", lambda _spec: process)
    lease = SearchBudget(SearchPolicy(time_budget_s=0.05)).direction(1)
    with search_scope(lease):
        session = job_session.JobSession("echo", {}, module="test", version="test/1",
                                         timeout_s=60, wall_s=300, exchanges=None)
        process.stdout = BlockedReply()
        with pytest.raises(SearchStopped):
            session._exchange("probe", {}, 60)
        session.close()
    assert process.closed == 1 and released.is_set()
    assert 0 < limits[0]["timeout_s"] <= 0.05


def test_zero_total_probe_limit_does_not_select(clock):
    provider = TimedProvider(clock, "a", select_s=100)
    result = run([provider], SearchPolicy(max_probes=0))
    assert provider.received_timeout == [] and provider.probes == []
    assert reports(result)[0]["stop_reason"] == "probe-budget-exhausted"
