"""Parallel search keeps source sessions private and evidence coordinator-owned."""
from contextlib import contextmanager
from threading import Barrier, Event, Lock, get_ident
from time import monotonic, sleep

import pytest

from behave.core import runtime_defaults as defaults
from behave.sim import api
from behave.sim.cpu_admission import reserve_cpus
from behave.sim.engines.probing import Outcome, ProbeResult, Scope
from behave.sim.generation import ProbeSelection, generate_independently
from behave.sim.generation_feedback import ReplayFeedback
from behave.sim.search_policy import SearchPolicy, check_search
from behave.sim.search_parallel import search_workers
from .test_api_path import contract
from .test_generation_feedback import ObservedProvider
from .test_generation_scheduler import _canonical, _request, _replace, _spec, _stimulus


class ParallelProvider(ObservedProvider):
    def __init__(self, *, outcome=Outcome.LOCAL_UNSAT, barrier=None, fail=False):
        super().__init__({}, outcome=outcome)
        self.barrier, self.fail = barrier, fail
        self.lanes, self.calls, self.feedback_threads = [], [], []
        self.lock = Lock()
        self.entered = Event()

    def observe_many(self, seeds):
        self.feedback_threads.append(get_ident())
        manifest = self.catalogue_manifest()
        return tuple(ReplayFeedback(seed.fingerprint, manifest.digest(), seed.fingerprint,
                     ("g1", "g2") if dict(seed.queues)["in"][0][0] == 7 else ("g1",))
                     for seed in seeds)

    def fork_search_worker(self, index):
        parent = self
        class Lane(ObservedProvider):
            def __init__(self):
                super().__init__({}, outcome=parent.outcome)
                self.seed = None
                self.closed = False

            @contextmanager
            def search_scope(self, *, probe_budget):
                try:
                    yield
                finally:
                    self.closed = True

            def select(self, seed, *, targets=None):
                self.seed = seed
                feedback = parent.observe_many((seed,))[0]
                return ProbeSelection(
                    (_request("one", targets=tuple(targets)),), self.catalogue_manifest().digest(),
                    replay=feedback)

            def probe(self, seed, request):
                assert seed is self.seed
                with reserve_cpus(1):
                    with parent.lock:
                        parent.calls.append((index, seed.fingerprint, request.targets))
                    if parent.barrier is not None:
                        parent.barrier.wait(10)
                    if parent.fail:
                        if index == 0:
                            assert parent.entered.wait(10)
                            raise ValueError("deliberate worker error")
                        parent.entered.set()
                        while True:
                            check_search()
                            sleep(0.005)
                    return ProbeResult(parent.outcome, Scope.EXACT_LOCAL_HIT,
                        goal=request.targets[0] if parent.outcome is Outcome.SAT else None,
                        candidate=_replace(seed, 0, 7) if parent.outcome is Outcome.SAT else None)

        lane = Lane()
        self.lanes.append(lane)
        return lane


def run(provider, *, seconds=10, values=(0, 1), debug=False):
    seeds = _canonical(*(_stimulus([value]) for value in values))
    return generate_independently(seeds, (provider,), contract=contract(),
        generation_spec=_spec(seeds[0]), search_policy=SearchPolicy(
            time_budget_s=seconds, debug=debug))


def test_parallel_seeds_have_private_sessions_and_shared_cpu_receipts(monkeypatch):
    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", 2)
    provider = ParallelProvider(barrier=Barrier(2))
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        result = run(provider)
        assert cpu.snapshot()["peak_reserved"] == 2
        assert cpu.snapshot()["reserved"] == 0
        assert not cpu.snapshot()["quarantined"]
    telemetry = result.searches[0].as_dict()["telemetry"]
    assert telemetry["probes"] == 2 and telemetry["outcomes"][Outcome.LOCAL_UNSAT.value] == 2
    assert telemetry["parallel"]["peak_in_flight"] == 2
    assert len(provider.lanes) == 2 and all(x.closed for x in provider.lanes)
    assert len({seed for _, seed, _ in provider.calls}) == 2
    assert all(targets == ("g2",) for _, _, targets in provider.calls)


def test_parallel_sat_is_deduplicated_and_only_native_feedback_adds_hit(monkeypatch):
    from behave.sim.generation_feedback import SearchCoverage
    coordinator = get_ident()
    observe = SearchCoverage.observe
    def observe_on_coordinator(self, *args):
        assert get_ident() == coordinator
        return observe(self, *args)
    monkeypatch.setattr(SearchCoverage, "observe", observe_on_coordinator)
    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", 2)
    provider = ParallelProvider(outcome=Outcome.SAT, barrier=Barrier(2))
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        result = run(provider)
    search = result.searches[0]
    assert len(search.generated) == 1
    image = search.as_dict()["telemetry"]
    assert image["termination"] == "goals-covered"
    assert image["coverage"]["remaining"] == []
    assert all(lane.closed for lane in provider.lanes)


def test_parallel_query_chains_keep_one_seed_and_owner_until_extension_finishes(monkeypatch):
    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", 2)
    provider = ParallelProvider()
    original_fork = provider.fork_search_worker
    rendezvous, order = Barrier(2), {0: [], 1: []}

    def fork(index):
        lane = original_fork(index)
        original_select, original_probe = lane.select, lane.probe

        def select(seed, *, targets=None):
            selected = original_select(seed, targets=targets)
            requests = tuple(_request(name, targets=("g2",), rank=rank)
                             for rank, name in enumerate(("short", "other", "long")))
            return ProbeSelection(requests, selected.catalogue_digest, replay=selected.replay,
                                  query_chains=((requests[0].identity, requests[2].identity),))

        def probe(seed, request):
            order[index].append((seed.fingerprint, request.occurrence))
            if request.occurrence == "short":
                rendezvous.wait(10)
            return original_probe(seed, request)

        lane.select, lane.probe = select, probe
        return lane

    monkeypatch.setattr(provider, "fork_search_worker", fork)
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        result = run(provider)
        assert cpu.snapshot()["reserved"] == 0
    for calls in order.values():
        assert [name for _seed, name in calls] == ["short", "long", "other"]
        assert len({seed for seed, _name in calls}) == 1
    assert result.searches[0].as_dict()["telemetry"]["outcomes"]["local-unsat"] == 6


@pytest.mark.parametrize('context', (False, True))
def test_parallel_goal_sharing_preserves_deferred_targets_and_construction(monkeypatch, context):
    from behave.sim.query_plan import QueryPlan, QueryTemplate

    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", 2)
    provider = ParallelProvider()
    provider.explores_contexts = context
    rendezvous, calls = Barrier(2), {0: [], 1: []}
    manifest = provider.catalogue_manifest()
    provider.observe_many = lambda seeds: tuple(ReplayFeedback(
        seed.fingerprint, manifest.digest(), seed.fingerprint, ()) for seed in seeds)
    fork = provider.fork_search_worker

    def worker(index):
        lane = fork(index)
        original_select = lane.select

        def select(seed, *, targets=None):
            selected = original_select(seed, targets=targets)
            if not context:
                return selected
            request, = selected.requests
            plan = QueryPlan((QueryTemplate(request.targets, request.occurrence,
                request.mutable_fields, request.rank, catalogue_digest=selected.catalogue_digest),),
                selected.catalogue_digest, explorations=(request.rank,))
            return ProbeSelection(plan, selected.catalogue_digest, replay=selected.replay)

        def probe(seed, request):
            calls[index].append(request.targets)
            scope = Scope.STATE_PREPARATION if context else Scope.EXACT_LOCAL_HIT
            if len(calls[index]) == 1:
                rendezvous.wait(10)
                return ProbeResult(Outcome.CONSTRUCTING, scope)
            return ProbeResult(Outcome.UNKNOWN if context else Outcome.LOCAL_UNSAT, scope)
        lane.select, lane.probe = select, probe
        return lane

    provider.fork_search_worker = worker
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        result = run(provider)
    first = [rows[0] for rows in calls.values()]
    assert set(first) == ({("g1", "g2")} if context else {("g1",), ("g2",)})
    for rows in calls.values():
        assert rows[0] == rows[1]  # Resume the same formula, not a new partition.
        assert set().union(*map(set, rows)) == {"g1", "g2"}
    telemetry = result.searches[0].as_dict()["telemetry"]
    assert telemetry["parallel_target_splits"] == (0 if context else 2)
    assert telemetry["coverage"]["remaining"] == ["g1", "g2"]
    assert telemetry["termination"] == "queue-empty"
    assert not result.searches[0].generated


def test_parallel_seeds_distribute_independent_families_before_repeating_the_first(monkeypatch):
    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", 2)
    provider = ParallelProvider()
    manifest = provider.catalogue_manifest()
    provider.observe_many = lambda seeds: tuple(ReplayFeedback(
        seed.fingerprint, manifest.digest(), seed.fingerprint, ()) for seed in seeds)
    fork, rendezvous, calls = provider.fork_search_worker, Barrier(2), {0: [], 1: []}

    def worker(index):
        lane = fork(index)

        def select(seed, *, targets=None):
            requests = tuple(_request(name, targets=(goal,), rank=rank)
                             for rank, (name, goal) in enumerate((
                                 ("a-short", "g1"), ("b-short", "g2"),
                                 ("a-long", "g1"), ("b-long", "g2"))))
            return ProbeSelection(requests, manifest.digest(),
                replay=provider.observe_many((seed,))[0],
                query_chains=((requests[0].identity, requests[2].identity),
                              (requests[1].identity, requests[3].identity)))

        def probe(seed, request):
            calls[index].append((request.occurrence, request.targets))
            if len(calls[index]) == 1:
                rendezvous.wait(10)
                return ProbeResult(Outcome.CONSTRUCTING, Scope.EXACT_LOCAL_HIT)
            return ProbeResult(Outcome.LOCAL_UNSAT, Scope.EXACT_LOCAL_HIT)

        lane.select, lane.probe = select, probe
        return lane

    provider.fork_search_worker = worker
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        result = run(provider)
        assert cpu.snapshot()["reserved"] == 0
    assert {rows[0][1] for rows in calls.values()} == {("g1",), ("g2",)}
    for rows in calls.values():
        assert rows[0] == rows[1]  # Resume construction without switching families.
        assert rows[2][0].endswith("long") and rows[2][1] == rows[0][1]
        assert {name for name, _targets in rows} == {"a-short", "a-long", "b-short", "b-long"}
    telemetry = result.searches[0].as_dict()["telemetry"]
    assert telemetry["goal_family_starts"] == 4
    assert telemetry["coverage"]["remaining"] == ["g1", "g2"]
    assert telemetry["termination"] == "queue-empty"
    assert not result.searches[0].generated


def test_target_partition_uses_only_available_lanes_and_never_drops_a_goal():
    from behave.sim.search_parallel import partition_targets

    request = _request("all", targets=tuple(f"g{i}" for i in range(9)))
    active, parts = {}, []
    for lane in range(4):
        chosen, remainder = partition_targets(request, active, 4)
        assert set(chosen.targets).isdisjoint(set().union(*active.values()))
        assert set(chosen.targets) | set(remainder.targets) == set(request.targets)
        active[lane] = set(chosen.targets)
        parts.append(chosen)
    assert set().union(*(set(part.targets) for part in parts)) == set(request.targets)
    # No idle wait when other seeds are already trying all remaining targets.
    chosen, remainder = partition_targets(request, active, 4)
    assert chosen is request and remainder is None
    chosen, remainder = partition_targets(request, {}, 1)
    assert chosen is request and remainder is None


def test_query_work_shares_by_chain_and_retains_split_remainders():
    from behave.sim.search_parallel import QueryWork

    requests = tuple(_request(str(index), rank=index) for index in range(4))
    chains = ((requests[0].identity, requests[2].identity, requests[3].identity),)
    work = QueryWork(requests, chains)
    first = work.popleft()
    assert first == (0, requests[0]) and work.shares(0) == 2
    assert work.continues(0)
    work.appendleft(first)  # A construction yield or deferred target subset.
    assert work.shares(0) == 2 and work.popleft() == first
    assert work.popleft()[0] == 2
    assert work.popleft()[0] == 3 and not work.continues(3)
    assert work.shares(3) == 2
    assert work.popleft()[0] == 1 and not work


def test_query_chain_metadata_never_grants_equivalence():
    requests = tuple(_request(str(index), rank=index) for index in range(3))
    digest = requests[0].catalogue_digest
    ids = tuple(request.identity for request in requests)
    selected = ProbeSelection(requests, digest, query_chains=(ids,))
    assert selected.query_chains == (ids,) and not selected.equivalent_requests
    for chains in (((ids[0], "unknown"),), ((ids[0], ids[0]),),
                   ((ids[0], ids[1]), (ids[1], ids[2]))):
        with pytest.raises(ValueError):
            ProbeSelection(requests, digest, query_chains=chains)
def test_worker_failure_cancels_sibling_and_preserves_original_diagnostic(monkeypatch):
    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", 2)
    provider = ParallelProvider(fail=True)
    start = monotonic()
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        result = run(provider, seconds=30)
        assert cpu.snapshot()["reserved"] == 0
        assert not cpu.snapshot()["quarantined"]
    telemetry = result.searches[0].as_dict()["telemetry"]
    assert telemetry["termination"] == "search-failed"
    assert "deliberate worker error" in str(telemetry["failure"])
    assert monotonic() - start < 5
    assert all(lane.closed for lane in provider.lanes)


def test_one_worker_preserves_serial_path_and_parent_permit(monkeypatch):
    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", 8)
    provider = ParallelProvider()
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        assert search_workers(provider) == 2
        with reserve_cpus(1):
            assert search_workers(provider) == 1
        assert search_workers(provider, legacy=True) == 1
    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", 1)
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        result = run(provider)
    assert not provider.lanes
    assert "parallel" not in result.searches[0].as_dict()["telemetry"]


def test_runtime_workers_cannot_replay_missing_seeds():
    from behave.sim.runtime_guidance import RecordedExecutions
    from .test_runtime_guidance import setup, runtime

    provider, seeds, replays, _contexts, _scopes = setup()
    reader = provider._executions.reader()
    assert reader.trace(seeds[0]) is provider._executions.trace(seeds[0])
    with pytest.raises(ValueError, match="coordinator-replayed"):
        reader.trace(runtime(2))
    assert len(replays) == 1
    provider._executions.prepare((runtime(2),))
    assert reader.trace(runtime(2)) is provider._executions.trace(runtime(2))


def test_runtime_worker_forks_do_not_share_mutable_context_state():
    from .test_runtime_guidance import setup
    provider, _seeds, replays, _contexts, _scopes = setup()
    left, right = provider.fork_search_worker(0), provider.fork_search_worker(1)
    assert left.name == right.name == provider.name
    assert left.catalogue_manifest() == provider.catalogue_manifest()
    assert left._contexts is not right._contexts
    assert left._selected is not right._selected
    assert len(replays) == 1

def test_two_questions_share_one_pool_without_nested_reservation(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", 2)
    rendezvous = Barrier(2)
    providers = [ParallelProvider(barrier=rendezvous) for _ in range(2)]
    with api.CpuPool(2) as cpu, ThreadPoolExecutor(max_workers=2) as callers:
        def check(provider):
            with api.cpu_scope(cpu.endpoint):
                return run(provider)
        results = list(callers.map(check, providers))
        assert cpu.snapshot()["peak_reserved"] == 2
        assert cpu.snapshot()["reserved"] == 0
        assert not cpu.snapshot()["quarantined"]
    assert all(result.searches[0].telemetry["probes"] == 2 for result in results)
    assert all(lane.closed for provider in providers for lane in provider.lanes)


def test_shared_deadline_cancels_all_lanes_without_renewing_budget(monkeypatch):
    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", 2)
    provider = ParallelProvider()
    fork = provider.fork_search_worker
    def blocking(index):
        lane = fork(index)
        def probe(seed, request):
            with reserve_cpus(1):
                while True:
                    check_search()
                    sleep(0.005)
        lane.probe = probe
        return lane
    provider.fork_search_worker = blocking
    start = monotonic()
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        result = run(provider, seconds=0.2)
        assert cpu.snapshot()["reserved"] == 0
        assert not cpu.snapshot()["quarantined"]
    telemetry = result.searches[0].as_dict()["telemetry"]
    assert telemetry["termination"] == "time-budget-exhausted"
    assert telemetry["search_budget"]["probes_cancelled"] == 2
    assert not result.searches[0].generated
    assert all(lane.closed for lane in provider.lanes)
    assert monotonic() - start < 5


def test_session_cancellation_interrupts_exchange_wait_not_full_timeout(monkeypatch):
    import io
    import json
    from types import SimpleNamespace
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    from behave.sim import job_session
    from behave.sim.search_policy import SearchBudget, search_scope, SearchStopped
    from .test_cpu_admission_sessions import _open

    waiting, finish = Event(), Event()
    class Output:
        def __init__(self):
            self.opened = False
        def readline(self, *_):
            if not self.opened:
                self.opened = True
                return json.dumps({"version": "test-session/1", "id": 0,
                    "ok": True, "wall_ms": 0, "result": "prepared"}).encode() + b"\n"
            waiting.set()
            assert finish.wait(10)
            return b""

    child = SimpleNamespace(stdin=io.BytesIO(), stderr=io.BytesIO(),
                            stdout=Output(), close=finish.set)
    monkeypatch.setattr(job_session, "_freeze_inputs", lambda n, p: (p, ()))
    monkeypatch.setattr(job_session, "_job_spec", lambda *a, **k: None)
    monkeypatch.setattr(job_session, "start", lambda spec: child)
    lease = api.SearchPolicy(time_budget_s=30)
    lease = SearchBudget(lease).direction(1)
    with api.CpuPool(1) as cpu, api.cpu_scope(cpu.endpoint), search_scope(lease):
        session = _open()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(copy_context().run, session._exchange, "probe", {}, 20)
            assert waiting.wait(5)
            lease.cancel("test-stop")
            with pytest.raises(SearchStopped, match="test-stop"):
                future.result(2)
        assert finish.is_set()
        assert cpu.snapshot()["reserved"] == 0
        assert not cpu.snapshot()["quarantined"]


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
@pytest.mark.parametrize("wrong", (False, True))
def test_public_parallel_search_finds_native_confirmed_rare_branch(
        tmp_path, monkeypatch, kind, wrong, stateful=False):
    from .test_settled_protocol import contract as settled_contract
    from behave.sim.deferred_generation import DeferredGenerationProvider

    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", 2)
    source = """class M:
    def reset(self):
        pass
    def process(self, in_a):
        if in_a == 37:
            out_y = 52
        else:
            out_y = (in_a * 2) & 255
        return {"out_y": out_y}
"""
    if stateful:
        source = source.replace("        pass", "        self.n = 0")
        source = source.replace("        if in_a == 37:",
            "        self.n = (self.n + in_a) & 255\n        if self.n == 37:")
        source = source.replace("(in_a * 2)", "(self.n * 2)")
    reference = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": source.encode()}, config={"class": "M"})
    c = settled_contract()
    spec = api.GenerationSpec(3, 3, 0, 0)
    prepared = []
    original_bind = DeferredGenerationProvider.bind
    def bind(recipe, provider):
        original_bind(recipe, provider)
        prepared.append(provider)
    monkeypatch.setattr(DeferredGenerationProvider, "bind", bind)
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        if kind == "behavior":
            candidate = source.replace("out_y = 52", "out_y = 0") if wrong else source
            path = tmp_path / "behavior.py"
            path.write_text(candidate)
            task = api.admit(c, api.realization_of_behavior_source(
                candidate, c, file=path.name), reference=reference)
            handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=path.name), spec)
        else:
            candidate = """module M(input wire [7:0] in_a, output reg [7:0] out_y);
                always_comb begin
                    if (in_a == 8'd37) out_y = SPECIAL;
                    else out_y = in_a << 1;
                end
            endmodule""".replace("SPECIAL", "8'd0" if wrong else "8'd52")
            path = tmp_path / "rtl.sv"
            path.write_text(candidate)
            task = api.admit(c, api.realization_of_rtl_source(
                candidate, "parallel rare branch", top="M", contract=c), reference=reference)
            handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"), spec)
        result = api.handle_command(handle.command_request(
            run_directory=tmp_path / "invocation", reference=reference,
            baseline=api.BaselinePolicy(case_limit=4, max_queue_depth=3),
            providers=handle.generation_providers(reference, workdir=tmp_path / "search"),
            search_policy=api.SearchPolicy(time_budget_s=60)))
        assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
        assert result.decision.authoritative and result.decision.passed is not wrong
        assert result.generated.searches
        searches = [row.as_dict() for row in result.generated.searches]
        assert any(row["generated"] for row in searches)
        assert any(row["telemetry"].get("parallel", {}).get("peak_in_flight") == 2
                   for row in searches)
        assert not any(row["telemetry"].get("failure") for row in searches), searches
        assert cpu.snapshot()["reserved"] == 0 and not cpu.snapshot()["quarantined"]
    for provider in prepared:
        for worker in getattr(provider, "_workers", ()):
            assert worker._active is None


@pytest.mark.isolation
@pytest.mark.parametrize("wrong", (False, True))
def test_public_parallel_stateful_behavior_keeps_seed_histories(
        tmp_path, monkeypatch, wrong):
    test_public_parallel_search_finds_native_confirmed_rare_branch(
        tmp_path, monkeypatch, "behavior", wrong, stateful=True)
