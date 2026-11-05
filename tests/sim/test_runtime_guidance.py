"""Runtime latency is observed, never replaced by a parent's query schedule."""

from contextlib import contextmanager, nullcontext

import pytest

from behave.sim import api
from behave.sim.core.stimulus import AuditedTrace, Completion, SlotRecord, Stimulus
from behave.sim.generation import ProbeSelection, _candidate_refusal, generate
from behave.sim.runtime_guidance import RecordedExecutions, RuntimeGuidance, _PreparedGuidance

from .test_api_path import contract
from .test_generation_feedback import ObservedProvider
from .test_generation_scheduler import _replace, _request, _spec, _stimulus


def _capability():
    from behave.sim.core.goal_provider import BEHAVIOR_LOCAL_GUIDANCE_POLICY, LOCAL_GUIDANCE
    from behave.sim.goal_policy import evaluation_goal_policy

    from .test_generation_scheduler import _frontend_catalogue

    policy = evaluation_goal_policy(contract(), _frontend_catalogue(), artifact_kind="behavior")
    return policy.bind_implementation(LOCAL_GUIDANCE, BEHAVIOR_LOCAL_GUIDANCE_POLICY,
                                      policy.manifest)


def runtime(value=0):
    return Stimulus(queues={"in": [(value,)]}, replay_policy=api.ReplayPolicy(
        reset_hold_cycles=3, drain_slots=1, stall_every=0))


def setup(*, hits=None, outcome=None, mutate=None, context_failure=False, drain_of=None):
    replays, providers, scopes = [], [], []
    capability = _capability()

    def replay(seeds):
        result = []
        for seed in seeds:
            replays.append(seed)
            value = dict(seed.queues)["in"][0][0]
            view = _stimulus([value], drain=1 + value if drain_of is None else drain_of(value))
            result.append(AuditedTrace(view, records=[SlotRecord() for _ in view.slots],
                                       request=seed))
        return tuple(result)

    def factory(spec, execution, schedule):
        assert not scopes, "context preparation must release the previous worker first"
        class Provider(ObservedProvider):
            policy = capability.implementation

            def catalogue_manifest(self):
                return capability.source_catalogue

            def goal_capability_use(self):
                return capability

            @contextmanager
            def search_scope(self, *, probe_budget):
                assert not scopes, "different query domains must not hold nested CPU workers"
                scopes.append(spec.horizon)
                try:
                    yield
                finally:
                    scopes.pop()

            def select(self, seed, *, targets=None):
                self.received_targets.append(tuple(targets))
                assert len(seed.slots) == spec.horizon
                assert execution.traces((seed,))[0].stimulus == seed
                selected = super().select(seed)
                if context_failure:
                    return ProbeSelection(
                        (selected.requests[0], _request("second", targets=("g2",), rank=1)),
                        selected.catalogue_digest, replay=selected.replay)
                return selected

            def search_refusal(self):
                return getattr(self, "failure", None)

            def query_targets(self, targets, *, seed=None):
                return tuple(goal for goal in targets
                             if goal not in getattr(self, 'constant_false_goals', ()))

            def probe(self, seed, request):
                answer = super().probe(seed, request)
                if context_failure:
                    self.failure = api.Refusal("test-context-unavailable", "this local context failed")
                if mutate is not None:
                    answer.candidate = mutate(answer.candidate)
                return answer

        kwargs = {} if outcome is None else {"outcome": outcome}
        provider = Provider(hits or {0: ("g1",), 7: ("g2",)}, **kwargs)
        provider.received_targets = []
        providers.append(provider)
        return provider

    seeds = (runtime(),)
    provider = RuntimeGuidance(factory, RecordedExecutions(replay), seeds, contract(),
                               name=ObservedProvider.name, capability=capability)
    assert not providers  # No local context is needed to expose the declaration.
    return provider, seeds, replays, providers, scopes


def test_changed_payload_replays_runtime_and_may_take_more_slots():
    provider, seeds, replays, contexts, scopes = setup()
    # Explicit analysis domain is deliberately shorter than the new runtime run.
    spec = _spec(_stimulus([0]))
    result = generate(seeds, provider, contract=contract(), generation_spec=spec, probe_budget=1)
    assert result.telemetry["termination"] == "goals-covered", result.as_dict()
    assert len(result.generated) == 1
    assert result.generated[0].replay_policy == seeds[0].replay_policy
    assert not result.generated[0].slots
    assert len(replays) == 2  # One baseline and one new input; Select does not replay them.
    assert len(contexts) == 2 and not scopes
    assert result.generation_spec == spec
    domains = provider.execution_cost()["domains"]
    assert max(row["generation_spec"]["horizon"] for row in domains) > spec.horizon


def test_preparation_uses_all_baseline_schedules_once_across_workers():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace

    old, _, _, _, _ = setup()
    seeds = (runtime(0), runtime(1))  # Same input bound, different recorded horizons.
    provider = RuntimeGuidance(old._factory, old._executions, seeds, contract(),
                               name=old.name, capability=old._capability)
    workers = [provider.fork_search_worker(i) for i in range(2)]
    calls, received = [], []
    barrier = Barrier(2)
    def collect(views):
        calls.append(views)
        return {'writers': [{'value': 3}]}
    def use(material):
        received.append(material)
        material['writers'][0]['value'] += 1
    def prepare(worker):
        barrier.wait()
        worker._prepare_material(_PreparedGuidance.bind(api.GenerationSpec(5, 1, 1, 0, 0),
                                 SimpleNamespace(collect_preparation=collect, use_preparation=use)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(prepare, workers))
    assert len(calls) == 1 and len(calls[0]) == 2
    assert len({len(view.slots) for view in calls[0]}) == 2
    assert [row['writers'][0]['value'] for row in received] == [4, 4]
    assert received[0] is not received[1]


def test_failed_material_preparation_releases_waiters_without_repeating_work():
    from types import SimpleNamespace

    provider, *_ = setup()
    calls = []
    def collect(_seeds):
        calls.append(True)
        raise ValueError('source observation failed')
    fake = SimpleNamespace(collect_preparation=collect,
                           use_preparation=lambda _: pytest.fail('no partial import'))
    for worker in (provider, provider.fork_search_worker(0)):
        with pytest.raises(ValueError, match='source observation failed'):
            worker._prepare_material(_PreparedGuidance.bind(api.GenerationSpec(5, 1, 1, 0, 0), fake))
    assert calls == [True]


@pytest.mark.parametrize('share', [False, True])
def test_replayed_writer_material_survives_other_lanes_and_domain_restarts(monkeypatch, share):
    """Discovery is input-only; native feedback still comes from each Select."""
    owner, seeds, replays, providers, _ = setup(hits={0: ('g1',), 1: ('g1',), 7: ('g2',)})
    if not share:
        monkeypatch.setattr(RuntimeGuidance, '_exchange_preparation', lambda *_a, **_k: None)
    factory = owner._factory

    def material(values):
        return dict(version='test-material', binding='same-source',
                    writers=[{'value': value} for value in sorted(values)], commands=[])

    def with_material(*args):
        provider = factory(*args)
        select, scope = provider.select, provider.search_scope
        provider.writers, provider.new_writers = set(), set()

        @contextmanager
        def isolated_scope(**kwargs):
            with scope(**kwargs):
                provider.writers.clear()  # A newly isolated source worker.
                provider.new_writers.clear()
                yield

        def use(image):
            provider.writers.update(row['value'] for row in image['writers'])

        def observe(seed, **kwargs):
            answer = select(seed, **kwargs)
            value = dict(seed.queues)['in'][0][0]
            if value not in provider.writers:
                provider.writers.add(value)
                provider.new_writers.add(value)
            return answer

        def updates():
            image = material(provider.new_writers)
            provider.new_writers.clear()
            return image

        provider.search_scope = isolated_scope
        provider.collect_preparation = lambda _seeds: material({0})
        provider.use_preparation = use
        provider.preparation_updates = updates
        provider.select = observe
        return provider

    owner._factory = with_material
    other = owner.fork_search_worker(0)
    owner._executions.prepare((runtime(1),))
    with owner.search_scope(probe_budget=10), other.search_scope(probe_budget=10):
        first = owner.select(runtime(7))
        assert first.replay.hits == ('g2',)
        owner._close_scope()
        second = other.select(runtime(1))
        assert second.replay.hits == ('g1',)  # Imported inputs cannot import hits.
        assert providers[-1].writers == ({0, 1, 7} if share else {0, 1})
        other.select(seeds[0])  # Different recorded horizon, new source worker.
        assert providers[-1].writers == ({0, 1, 7} if share else {0})
        before = len(replays)
        other.select(runtime(1))  # Restore a prior domain without replaying it.
        assert len(replays) == before
        assert providers[-2].writers == ({0, 1, 7} if share else {0, 1})


def test_waiting_for_material_uses_the_owning_search_deadline():
    from concurrent.futures import Future
    from types import SimpleNamespace
    from behave.sim.search_policy import SearchBudget, SearchStopped, search_scope

    provider, *_ = setup()
    provider._preparation[0][1] = Future()  # Another worker owns this collection.
    fake = SimpleNamespace(collect_preparation=lambda _: pytest.fail('duplicate collection'),
                           use_preparation=lambda _: pytest.fail('no incomplete import'))
    with pytest.raises(SearchStopped, match='time-budget-exhausted'):
        with search_scope(SearchBudget(api.SearchPolicy(time_budget_s=0.01)).direction(1)):
            provider._prepare_material(_PreparedGuidance.bind(api.GenerationSpec(5, 1, 1, 0, 0), fake))


def test_runtime_selection_preserves_query_chains(monkeypatch):
    provider, seeds, _replays, _contexts, _scopes = setup()
    factory = provider._factory

    def chained(*args):
        delegate = factory(*args)
        original = delegate.select

        def select(seed, *, targets=None):
            selected = original(seed, targets=targets)
            requests = tuple(_request(str(index), targets=("g2",), rank=index)
                             for index in range(3))
            return ProbeSelection(requests, selected.catalogue_digest, replay=selected.replay,
                                  query_chains=(tuple(r.identity for r in requests),))

        monkeypatch.setattr(delegate, "select", select)
        return delegate

    monkeypatch.setattr(provider, "_factory", chained)
    with provider.search_scope(probe_budget=3):
        selected = provider.select(seeds[0], targets=("g2",))
    assert selected.query_chains == (tuple(r.identity for r in selected.requests),)


@pytest.mark.parametrize("order", [("g1", "g2"), ("g2", "g1")])
def test_domain_resume_restores_each_selected_target_group(monkeypatch, order):
    from behave.sim.engines.probing import Outcome

    provider, seeds, _replays, contexts, scopes = setup(outcome=Outcome.LOCAL_UNSAT)
    original_factory = provider._factory

    def factory(*args):
        delegate = original_factory(*args)
        original_select, original_probe = delegate.select, delegate.probe
        selected = {}

        def select(seed, *, targets=None):
            observed = original_select(seed, targets=targets)
            requests = tuple(_request(goal, targets=(goal,)) for goal in targets)
            selected[seed.identity_bytes] = {row.occurrence for row in requests}
            return ProbeSelection(requests, observed.catalogue_digest,
                                  replay=observed.replay)

        def probe(seed, request):
            assert request.occurrence in selected[seed.identity_bytes]
            return original_probe(seed, request)

        monkeypatch.setattr(delegate, "select", select)
        monkeypatch.setattr(delegate, "probe", probe)
        return delegate

    provider._factory = factory
    seed, = seeds
    with provider.search_scope(probe_budget=3):
        requests = {row.targets[0]: row for row in provider.select(seed).requests}
        provider.select(runtime(1))  # New latency domain releases the old scope.
        for goal in order:
            assert provider.probe(seed, requests[goal]).outcome is Outcome.LOCAL_UNSAT
        assert provider.probe(seed, requests[order[0]]).outcome is Outcome.LOCAL_UNSAT
        assert contexts[0].received_targets == [("g1", "g2"), (order[0],), ("g1", "g2")]
    assert not scopes


def test_recorded_schedule_reuses_complete_assessment_feedback():
    from behave.sim.generation_feedback import ReplayFeedback

    seed = runtime()
    view = _stimulus([0], drain=1)
    trace = AuditedTrace(view, records=[SlotRecord() for _ in view.slots], request=seed)
    calls = []
    manifest = _capability().source_catalogue

    def observed(seeds, catalogue):
        calls.append((seeds, catalogue))
        return (ReplayFeedback(seed.fingerprint, catalogue.digest(), trace.fingerprint,
                               ("g1", "g2")),)

    records = RecordedExecutions(lambda seeds: (trace,), feedback=observed)
    records.prepare((seed,))
    got, = records.feedback((view,), manifest)
    assert calls == [((seed,), manifest)]
    assert got.stimulus == view.fingerprint
    assert got.record == trace.fingerprint
    assert got.hits == ("g1", "g2")  # Not the empty raw SlotRecord hits above.


def test_unavailable_source_is_not_an_empty_covered_catalogue():
    from behave.sim.generation import UnavailableGenerationProvider

    seed = runtime()
    view = _stimulus([0])
    records = RecordedExecutions(lambda _seeds: (AuditedTrace(
        view, records=[SlotRecord() for _ in view.slots], request=seed),))

    prepared = []

    def unavailable(*_args):
        prepared.append(True)
        return UnavailableGenerationProvider("source", "test/1",
            api.Refusal("layout-unavailable", "no source capability"))

    provider = RuntimeGuidance(unavailable, records, (seed,), contract(),
                               name="source", capability=_capability())
    assert not prepared
    result = generate((seed,), provider, contract=contract(),
                      generation_spec=_spec(view), probe_budget=4)
    assert prepared == [True]
    assert result.telemetry["probes"] == 0
    assert result.telemetry["termination"] != "goals-covered"
    assert result.as_dict()["telemetry"]["coverage"]["remaining"] == ["g1", "g2"]
    assert dict(result.telemetry["refusals"]) == {"layout-unavailable": 1}


def test_solver_claim_without_runtime_source_hit_does_not_cover_goal():
    provider, seeds, *_ = setup(hits={0: ("g1",), 7: ("g1",)})
    result = generate(seeds, provider, contract=contract(),
                      generation_spec=_spec(_stimulus([0])), probe_budget=1)
    assert result.as_dict()["telemetry"]["coverage"]["remaining"] == ["g2"]


def test_runtime_wrapper_passes_residual_targets_to_recorded_domain():
    provider, seeds, _replays, contexts, _scopes = setup()
    with provider.search_scope(probe_budget=0):
        provider.select(seeds[0], targets=("g2",))
        assert contexts[0].received_targets == [("g2",)]
        assert provider.targetable_goals == contexts[0].targetable_goals == ("g1", "g2")
        feedback = provider.select(seeds[0], targets=()).replay
        assert contexts[0].received_targets[-1] == ()
        assert feedback.hits == ("g1",)  # Observation is not filtered by the target set.


def test_static_query_facts_do_not_prepare_or_leak_between_recorded_domains():
    provider, seeds, replays, contexts, scopes = setup()
    assert provider.query_targets(("g1", "g2"), seed=seeds[0]) == ("g1", "g2")
    assert contexts == [] and scopes == []
    with provider.search_scope(probe_budget=0):
        provider.select(seeds[0], targets=("g2",))
        contexts[0].constant_false_goals = frozenset({'g2'})
        assert provider.query_targets(("g1", "g2"), seed=seeds[0]) == ("g1",)
        provider.select(runtime(1), targets=("g2",))
        # The first context's fact cannot prune a goal in the second domain,
        # nor may reading the first context switch back/allocate a worker.
        assert provider.query_targets(("g1", "g2"), seed=runtime(1)) == ("g1", "g2")
        active = tuple(scopes)
        assert provider.query_targets(("g1", "g2"), seed=seeds[0]) == ("g1",)
        assert tuple(scopes) == active
        assert len(contexts) == 2 and len(replays) == 2


def test_select_checks_and_probes_bind_the_current_recorded_domain_once(monkeypatch):
    from behave.sim import runtime_guidance
    from behave.sim.generation_protocol import Outcome

    query_domain = runtime_guidance._query_domain
    bound = []

    def record(view, *args):
        bound.append(view)
        return query_domain(view, *args)

    monkeypatch.setattr(runtime_guidance, "_query_domain", record)
    provider, seeds, replays, contexts, scopes = setup(outcome=Outcome.LOCAL_UNSAT)
    assert len(bound) == 1  # The initial recorded baseline already binds it.
    with provider.search_scope(probe_budget=9):
        for seed in (seeds[0], runtime(1), seeds[0]):
            for _ in range(3):
                selection = provider.select(seed, targets=("g2",))
                request = selection.requests[0].with_targets(("g2",))
                assert provider.seed_refusal(seed) is None
                assert provider.request_refusal(seed, request) is None
                assert provider.probe(seed, request).outcome is Outcome.LOCAL_UNSAT
    assert [dict(view.queues)["in"][0][0] for view in bound] == [0, 1, 0]
    assert len(replays) == 2 and len(contexts) == 2 and not scopes
    assert sum(len(context.probes) for context in contexts) == 9


def test_prepared_context_keeps_ports_but_reads_live_failure_without_rebuilding():
    provider, seeds, replays, contexts, scopes = setup()
    seed, = seeds
    with provider.search_scope(probe_budget=3):
        selected = provider.select(seed, targets=('g2',))
        request = selected.requests[0]
        context, = provider._contexts.values()
        assert context.provider is contexts[0]
        assert context.spec.horizon == len(provider._executions.trace(seed).stimulus.slots)
        failure = api.Refusal('late-context-failure', 'only this recorded domain')
        contexts[0].failure = failure
        assert provider.seed_refusal(seed) is failure
        assert provider.request_refusal(seed, request) is failure
        contexts[0].failure = None
        assert provider.seed_refusal(seed) is None
        assert provider.request_refusal(seed, request) is None
        assert len(replays) == len(contexts) == len(provider._contexts) == 1
    assert not scopes


@pytest.mark.parametrize("outcome", ["sat", "local-unsat", "unknown", "unsupported"])
def test_bound_domain_keeps_search_results_and_replay_order_unchanged(monkeypatch, outcome):
    from behave.sim.generation_protocol import Outcome
    from behave.sim.runtime_guidance import _query_domain

    images = []
    for reuse in (False, True):
        provider, seeds, replays, _contexts, scopes = setup(outcome=Outcome(outcome))
        if not reuse:
            monkeypatch.setattr(provider, "_domain", lambda view, p=provider:
                                _query_domain(view, p._contract, p._fixed_spec, view.clock_schedule))
        result = generate((*seeds, runtime(1)), provider, contract=contract(),
                          generation_spec=None, probe_budget=4)
        assert not scopes
        images.append((result.as_dict(), provider.execution_cost(),
                       [seed.as_dict() for seed in replays]))
    assert images[0] == images[1]


@pytest.mark.parametrize("fixed", [False, True])
def test_current_domain_binding_preserves_clock_memory_and_fixed_bounds(fixed):
    from behave.sim.core.memory import MemoryEnvironment, MemoryRun
    from behave.sim.runtime_guidance import _query_domain
    from tests.support.atomic_behavior import _contract, _schedule

    task = _contract()
    schedule = _schedule(n_slots=4)
    seed = Stimulus(queues={"a_in": [(0,)], "b_in": [(0,)]},
        slots=[slot.event for slot in schedule.slots], clock_schedule=schedule,
        memory_environment=MemoryEnvironment([MemoryRun("ram", initial={0: 1})]))
    trace = AuditedTrace(seed, records=[SlotRecord() for _ in seed.slots])
    spec = api.GenerationSpec(4, 8, 0, 0) if fixed else None
    provider = RuntimeGuidance(None, RecordedExecutions(lambda seeds: (trace,)),
        (seed,), task, name="source", capability=_capability(), fixed_spec=spec,
        clock_schedule=schedule)
    initial_key, initial_spec, initial_schedule = provider._domain(seed)
    assert initial_schedule is schedule
    assert initial_spec.queue_bound == (8 if fixed else 1)
    for changed in (None, "clock_schedule", "memory_environment"):
        image = seed.as_dict()
        if changed == "clock_schedule":
            for timing in image["clock_schedule"]["timings"]:
                timing["phase"] += 10
            for slot in image["clock_schedule"]["slots"]:
                slot["time"] += 10
        elif changed == "memory_environment":
            image["memory_environment"] = MemoryEnvironment([
                MemoryRun("ram", initial={0: 2})]).as_dict()
        view = Stimulus.from_dict(image)
        assert view is not seed
        expected_key, expected_spec, expected_schedule = _query_domain(
            view, task, spec, schedule if fixed else view.clock_schedule)
        actual = provider._domain(view)
        assert actual is provider._domain(view)
        assert actual[0] == expected_key
        assert actual[1].as_dict() == expected_spec.as_dict()
        assert actual[2] is expected_schedule
        assert (actual[0] == initial_key) is (changed is None)
        if fixed:
            assert actual[1] is spec
    assert provider._domain(seed)[0] == initial_key


def test_lazy_context_cannot_silently_shrink_the_declared_target_set():
    provider, seeds, *_ = setup()
    original = provider._factory

    def changed(*args):
        query = original(*args)
        query.targetable_goals = ("g1",)
        return query

    provider._factory = changed
    result = generate(seeds, provider, contract=contract(),
                      generation_spec=_spec(_stimulus([0])), probe_budget=4)
    assert provider.targetable_goals == ("g1", "g2")
    assert result.telemetry["probes"] == 0
    assert "generation-runtime-catalogue-changed" in dict(result.telemetry["refusals"])
    assert result.telemetry["termination"] != "goals-covered"


def test_bad_fixed_candidate_cannot_be_laundered_back_into_runtime_request():
    provider, seeds, *_ = setup(mutate=lambda seed: _replace(seed, 0, 7, slots=seed.slots[:-1]))
    result = generate(seeds, provider, contract=contract(),
                      generation_spec=_spec(_stimulus([0])), probe_budget=1)
    assert not result.generated
    assert result.as_dict()["telemetry"]["outcomes"]["unsupported"] == 1


@pytest.mark.parametrize('invalid', [False, True])
def test_preparation_proposal_preserves_runtime_policy_and_candidate_checks(invalid):
    from behave.sim.generation_protocol import Outcome, ProbeResult, Scope

    provider, seeds, *_ = setup(mutate=(lambda seed: _replace(
        seed, 0, 7, slots=seed.slots[:-1])) if invalid else None)
    factory = provider._factory
    def preparation(*args):
        delegate = factory(*args)
        original = delegate.probe
        def propose(seed, request):
            result = original(seed, request)
            return ProbeResult(Outcome.PROPOSED, Scope.STATE_PREPARATION,
                               candidate=result.candidate)
        delegate.probe = propose
        return delegate
    provider._factory = preparation
    result = generate(seeds, provider, contract=contract(),
                      generation_spec=_spec(_stimulus([0])), probe_budget=1)
    if invalid:
        assert not result.generated
        assert result.telemetry['outcomes']['unsupported'] == 1
    else:
        assert len(result.generated) == 1
        assert result.generated[0].replay_policy == seeds[0].replay_policy
        assert result.telemetry['outcomes']['sat'] == 0
        assert result.telemetry['outcomes']['proposed'] == 1


def test_failed_context_is_not_probed_repeatedly():
    from behave.sim.generation_protocol import Outcome

    provider, seeds, *_ = setup(outcome=Outcome.UNSUPPORTED, context_failure=True)
    result = generate(seeds, provider, contract=contract(),
                      generation_spec=_spec(_stimulus([0])), probe_budget=10)
    assert result.telemetry["probes"] == 1
    assert result.telemetry["requests_deferred"] == 1
    assert result.telemetry["termination"] == "queue-empty"
    assert result.as_dict()["telemetry"]["coverage"]["remaining"] == ["g2"]


def test_failed_context_does_not_disable_another_recorded_domain():
    from behave.sim.generation_protocol import Outcome

    provider, seeds, _replays, contexts, scopes = setup(
        outcome=Outcome.UNSUPPORTED, context_failure=True)
    result = generate((*seeds, runtime(1)), provider, contract=contract(),
                      generation_spec=_spec(_stimulus([0])), probe_budget=10)
    assert result.telemetry["probes"] == 2
    assert len(contexts) == 2 and all(len(item.probes) == 1 for item in contexts)
    assert not scopes
    assert result.telemetry["termination"] == "queue-empty"
    assert result.as_dict()["telemetry"]["coverage"]["remaining"] == ["g2"]


@pytest.mark.parametrize("phase", ["select", "probe"])
@pytest.mark.parametrize("debug", [False, True])
def test_timed_search_scopes_worker_failure_to_its_recorded_domain(phase, debug):
    from behave.sim.generation_feedback import ReplayFeedback
    from behave.sim.generation_protocol import Outcome
    from behave.sim.search_policy import SearchBudget, SearchPolicy, search_scope

    provider, seeds, _replays, contexts, scopes = setup(
        outcome=Outcome.UNKNOWN, drain_of=lambda value: 1 + value % 2)
    factory = provider._factory
    failure = api.Refusal("worker-unavailable", "this recorded domain lost its worker")

    def failed_first_domain(*args):
        context = factory(*args)
        if len(contexts) == 1:
            original = getattr(context, phase)

            def fail(*call_args, **kwargs):
                original(*call_args, **kwargs)
                context.failure = failure
                raise api.Refused(failure)

            setattr(context, phase, fail)
        return context

    provider._factory = failed_first_domain
    provider._observe = lambda batch, manifest: tuple(ReplayFeedback(
        seed.fingerprint, manifest.digest(), seed.fingerprint,
        ("g1",) if dict(seed.queues)["in"][0][0] == 0 else ()) for seed in batch)
    baseline = tuple(sorted((*seeds, runtime(1), runtime(2)),
                            key=lambda seed: seed.identity_bytes))
    budget = SearchBudget(SearchPolicy(time_budget_s=60, max_probes=10, debug=debug))
    expected = (pytest.raises(api.Refused, match="generation-debug-stop") if debug
                else nullcontext())
    with search_scope(budget.direction(1)), expected:
        result = generate(baseline, provider, contract=contract(), generation_spec=None,
                          probe_budget=None)
    assert not scopes
    assert provider.seed_refusal(seeds[0]) is failure  # Also after scope cleanup.
    if debug:
        assert len(contexts) == 1
    else:
        assert result.baseline == baseline and not result.generated
        row = result.as_dict()["telemetry"]
        assert len(contexts) == 2  # The failed domain did not restart for its next seed.
        assert len(contexts[1].probes) == 1
        assert row["outcomes"]["unknown"] == 1
        assert row["termination"] == "queue-empty"
        assert row["coverage"]["remaining"] == ["g2"]


def test_runtime_candidate_must_preserve_policy():
    seed = runtime()
    altered = Stimulus(queues=seed.queues, replay_policy=api.ReplayPolicy(drain_slots=0))
    refusal = _candidate_refusal(seed, altered, _request("one"), contract(), _spec(_stimulus([0])))
    assert refusal.kind == "generation-candidate-mutates-nonselected-field"


@pytest.mark.parametrize("changed", [None, "clock_schedule", "memory_environment"])
def test_candidate_wire_roundtrip_preserves_record_values_not_python_identity(changed):
    from behave.sim.core.memory import MemoryEnvironment, MemoryInterface, MemoryPort, MemoryRun
    from tests.support.atomic_behavior import _contract, _schedule

    base = _contract()
    task = api.TaskContract(base.endpoints, base.clocks, memories=(
        MemoryInterface("ram", 2, 4, "a", [MemoryPort("rw")]),))
    schedule = _schedule((2, 2), n_slots=2)
    seed = Stimulus(queues={"a_in": [(0,), (0,)], "b_in": [(0,), (0,)]},
                    slots=[slot.event for slot in schedule.slots], clock_schedule=schedule,
                    memory_environment=MemoryEnvironment([
                        MemoryRun("ram", initial={0: 1}, ready={None: [1, 1]})]))
    image = seed.as_dict()
    image["queues"][0][1][0][0] = 3
    if changed == "clock_schedule":
        for timing in image["clock_schedule"]["timings"]:
            timing["phase"] += 10
        for slot in image["clock_schedule"]["slots"]:
            slot["time"] += 10
    elif changed == "memory_environment":
        image["memory_environment"] = MemoryEnvironment([
            MemoryRun("ram", initial={0: 2}, ready={None: [1, 1]})]).as_dict()
    candidate = Stimulus.from_dict(image)
    request = api.ProbeRequest(["g1"], occurrence="slot0",
        mutable_fields=[api.StimulusField("a_in", 0, 0, 0)],
        catalogue_digest=_capability().source_catalogue.digest())
    refused = _candidate_refusal(seed, candidate, request, task, None)
    if changed is None:
        assert refused is None, refused.as_dict()
        assert candidate.clock_schedule is not seed.clock_schedule
        assert candidate.memory_environment is not seed.memory_environment
    else:
        assert refused.kind == "generation-candidate-mutates-nonselected-field"
        assert dict(refused.detail)["component"] == changed


@pytest.mark.parametrize("completion", [
    Completion.INCOMPLETE, Completion.PADDED, Completion.CHECKPOINT,
])
def test_incomplete_execution_is_not_a_local_query_seed(completion):
    seed = runtime()
    view = _stimulus([0])
    rows = () if completion is Completion.INCOMPLETE else tuple(SlotRecord() for _ in view.slots)
    trace = AuditedTrace(view, records=rows, completion=completion, request=seed)
    records = RecordedExecutions(lambda seeds: (trace,))
    refusal = records.trace(seed)
    assert refusal.kind == "generation-runtime-trace-incomplete"
    assert dict(refusal.detail)["completion"] == completion.value


def test_policy_form_does_not_inherit_fixed_analysis_slots():
    from behave.sim.baseline import _materialize_baseline

    policy = api.BaselinePolicy(case_limit=3, max_queue_depth=100)
    expected = api.build_baseline(contract(), policy=policy)
    actual = _materialize_baseline(contract(), _spec(_stimulus([0])), policy)
    assert actual.as_dict() == expected.as_dict()
    assert all(seed.depth("in") == 100 and not seed.slots for seed in actual.stimuli)


def test_selection_must_bind_request_and_actual_source_position():
    from behave.sim.generation import _selection

    from .test_generation_scheduler import _frontend_catalogue

    seed = runtime()
    manifest = _frontend_catalogue()
    for view, request, kind in (
            (_stimulus([1]), _request("one"), "generation-selection-runtime-binding-mismatch"),
            (_stimulus([0]), _request("one", source_position=500),
             "generation-request-source-map-outside-schedule")):
        telemetry = {"refusals": {}, "refusal_why": {}, "refusal_detail": {},
                     "selection_refusals": 0, "request_refusals": 0,
                     "catalogue_confirmations": 0, "requests_selected": 0}
        selected = ProbeSelection((request,), manifest.digest(), recorded_seed=view)
        assert not _selection(seed, selected, {"g1", "g2"}, telemetry, manifest.digest())
        assert kind in telemetry["refusals"]


def test_multiclock_runtime_baseline_does_not_inherit_the_analysis_domain():
    from behave.sim.baseline import _materialize_baseline

    from .test_generation_baseline import _multiclock_contract, _multiclock_realization

    task = _multiclock_contract()
    realization = _multiclock_realization()
    spec = api.GenerationSpec(12, 2, 1, 2)
    policy = api.BaselinePolicy(case_limit=2, max_queue_depth=2)
    actual = _materialize_baseline(task, spec, policy, realization=realization)
    expected = api.build_baseline(task, policy=policy, realization=realization)
    assert actual.as_dict() == expected.as_dict()
    assert actual.generation_spec is None
    assert all(seed.replay_policy is not None and not seed.slots for seed in actual.stimuli)


def test_lazy_multiclock_context_keeps_declared_domain_and_schedule():
    from behave.sim.generation import UnavailableGenerationProvider

    from .test_generation_baseline import _multiclock_contract, _multiclock_realization

    task = _multiclock_contract()
    spec = api.GenerationSpec(12, 8, 1, 2, stall_every=3)
    baseline = api.build_baseline(task, spec, realization=_multiclock_realization(),
                                  policy=api.BaselinePolicy(case_limit=1, max_queue_depth=2))
    seed, = baseline.stimuli
    trace = AuditedTrace(seed, records=[SlotRecord() for _ in seed.slots])
    constructed = []

    def factory(actual_spec, execution, schedule):
        constructed.append((actual_spec, schedule))
        assert execution.traces((seed,)) == (trace,)
        return UnavailableGenerationProvider("source", "test/1",
            api.Refusal("test-query-unavailable", "injected query preparation failure"))

    provider = RuntimeGuidance(
        factory, RecordedExecutions(lambda seeds: (trace,)), (seed,), task,
        name="source", capability=_capability(), fixed_spec=spec,
        clock_schedule=seed.clock_schedule)
    assert constructed == []
    assert provider.select(seed).kind == "test-query-unavailable"
    assert provider.select(seed).kind == "test-query-unavailable"
    assert len(constructed) == 1
    actual_spec, actual_schedule = constructed[0]
    assert actual_spec is spec  # Do not shrink queue_bound=8 to the observed depth=2.
    assert actual_schedule is seed.clock_schedule
    assert provider.execution_cost()["domains"][0]["generation_spec"] == spec.as_dict()


def test_implicit_clock_actions_bind_distinct_recorded_query_domains():
    from behave.sim.core.stimulus import EventSlot
    from behave.sim.generation import UnavailableGenerationProvider
    from behave.sim.rtl.exact_product import global_schedule_for_stimuli

    from .test_sampled_protocol import _contract

    task = _contract(input_valid=True, output_valid=True)
    views = tuple(Stimulus(queues={"in": [(value,)]}, slots=(
        EventSlot(reset=True, controls={"rst": 1}),
        EventSlot(offers=("in",), controls={"rst": 0}),
        EventSlot(reset=bool(value), controls={"rst": value}))) for value in (0, 1))
    traces = {view: AuditedTrace(view, records=[SlotRecord() for _ in view.slots])
              for view in views}
    constructed = []

    def factory(spec, execution, schedule):
        constructed.append(schedule)
        return UnavailableGenerationProvider("source", "test/1",
            api.Refusal("test-query-unavailable", "injected preparation failure"))

    provider = RuntimeGuidance(factory,
        RecordedExecutions(lambda seeds: tuple(traces[seed] for seed in seeds)),
        views, task, name="source", capability=_capability(),
        recorded_schedule=lambda seed: global_schedule_for_stimuli(task, (seed,)))
    assert not constructed
    for seed in (*views, views[0]):
        assert provider.select(seed).kind == "test-query-unavailable"
    assert len(constructed) == 2  # Same length/prefix, different reset action.
    assert constructed[0].slots[-1].event.resets == ()
    assert constructed[1].slots[-1].event.resets == ("clk",)
    assert all(tuple(slot.event.offers for slot in schedule.slots)
               == tuple(slot.offers for slot in view.slots)
               for schedule, view in zip(constructed, views, strict=True))
