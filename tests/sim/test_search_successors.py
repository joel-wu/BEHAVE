"""Native progress schedules descendants without losing or mixing parent work."""
from collections import deque
from contextlib import contextmanager

import pytest

from behave.sim import api
from behave.sim.engines.probing import Outcome, ProbeResult, Scope
from behave.sim.generation import ProbeSelection, generate
from behave.sim.search_parallel import ExploreSuccessor, QueryComplete, run_seed_worklist
from behave.sim.search_policy import SearchBudget, SearchPolicy, search_scope
from .test_api_path import contract
from .test_generation_feedback import ObservedProvider
from .test_generation_scheduler import _replace, _request, _spec, _stimulus


class Sessions:
    def __init__(self):
        self.closed = []

    def fork_search_worker(self, index):
        parent = self

        class Lane:
            @contextmanager
            def search_scope(self, *, probe_budget):
                try:
                    yield
                finally:
                    parent.closed.append(index)

        return Lane()


@pytest.mark.parametrize("workers", (1, 2))
def test_query_boundaries_admit_other_seeds_without_splitting_construction(workers):
    queue, events, owners, closed = deque(range(6)), [], {}, []
    provider, telemetry = Sessions(), {}

    def explore(seed, lane):
        owners[seed] = lane
        events.append((seed, "selected"))
        try:
            for question in range(3):
                if question:
                    yield QueryComplete()
                    assert owners[seed] is lane
                # A logical question can take multiple resumable exchanges.
                for part in range(2):
                    answer = yield ("probe", lambda: (seed, question, part))
                    assert answer == (seed, question, part)
                    events.append((seed, question, part))
        finally:
            closed.append(seed)

    with search_scope(SearchBudget(SearchPolicy(time_budget_s=30)).direction(1)):
        run_seed_worklist(queue, provider, explore, lambda: True,
                          workers=workers, telemetry=telemetry)
    assert sorted(closed) == list(range(6)) and len(closed) == len(owners)
    last_selection = max(i for i, event in enumerate(events) if event[1] == "selected")
    first_tail = min(i for i, event in enumerate(events) if event[1] == 1)
    assert first_tail < last_selection  # A pending input pool cannot starve old tails.
    # Other lanes can progress, but this lane never switches seed between the
    # two construction pieces. Resuming a seed must not transfer its ownership.
    for lane in {id(owner) for owner in owners.values()}:
        probes = [e for e in events if len(e) == 3 and id(owners[e[0]]) == lane]
        for first, second in zip(probes[::2], probes[1::2], strict=True):
            assert first[:2] == second[:2] and (first[2], second[2]) == (0, 1)
    assert telemetry["worklist"]["query_handoffs"] > 0
    assert telemetry["worklist"]["suspended_at_stop"] == 0
    assert sorted(provider.closed) == ([] if workers == 1 else [0, 1])


def test_common_generation_rotates_only_after_completed_questions():
    class Provider(ObservedProvider):
        def __init__(self):
            super().__init__({})
            self.pieces, self.questions = {}, []

        def select(self, seed, *, targets=None):
            selected = super().select(seed, targets=targets)
            return ProbeSelection(
                (_request("first", targets=("g1",)),
                 _request("tail", targets=("g2",), rank=1)),
                selected.catalogue_digest, replay=selected.replay)

        def probe(self, seed, request):
            value = dict(seed.queues)["in"][0][0]
            key = value, request.occurrence
            self.questions.append(key)
            self.pieces[key] = self.pieces.get(key, 0) + 1
            return ProbeResult(Outcome.CONSTRUCTING if self.pieces[key] == 1
                               else Outcome.LOCAL_UNSAT, Scope.EXACT_LOCAL_HIT)

    provider = Provider()
    seeds = tuple(_stimulus([value]) for value in range(3))
    result = generate(seeds, provider, contract=contract(),
                      generation_spec=_spec(seeds[0]), probe_budget=12)
    # Alternate fresh seeds and retained tails. Every construction finishes
    # uninterrupted; target-family balancing still applies at fresh selection.
    order = [(0, "first"), (1, "tail"), (0, "tail"),
             (2, "first"), (1, "first"), (2, "tail")]
    assert provider.questions == [item for item in order for _piece in range(2)]
    assert result.telemetry["outcomes"]["constructing"] == 6
    assert result.telemetry["outcomes"]["local-unsat"] == 6
    assert result.telemetry["worklist"]["query_handoffs"] == 3
    assert result.telemetry["goal_family_starts"] == 6


def test_common_generation_keeps_a_query_chain_together_without_reusing_answers():
    class Provider(ObservedProvider):
        def __init__(self):
            super().__init__({})
            self.questions = []

        def select(self, seed, *, targets=None):
            selected = super().select(seed, targets=targets)
            requests = tuple(_request(name, targets=("g2",), rank=rank)
                             for rank, name in enumerate(("short", "other", "long")))
            return ProbeSelection(requests, selected.catalogue_digest, replay=selected.replay,
                                  query_chains=((requests[0].identity, requests[2].identity),))

        def probe(self, seed, request):
            self.questions.append((dict(seed.queues)["in"][0][0], request.occurrence))
            return ProbeResult(Outcome.LOCAL_UNSAT, Scope.EXACT_LOCAL_HIT)

    provider = Provider()
    seeds = tuple(_stimulus([value]) for value in range(2))
    result = generate(seeds, provider, contract=contract(),
                      generation_spec=_spec(seeds[0]), probe_budget=6)
    assert provider.questions == [(0, "short"), (0, "long"), (1, "short"),
                                  (1, "long"), (0, "other"), (1, "other")]
    assert result.telemetry["outcomes"]["local-unsat"] == 6
    assert tuple(result.telemetry["coverage"]["remaining"]) == ("g1", "g2")
    assert not result.generated


def test_reordered_seed_refusal_counts_pending_work_not_original_request_rank():
    class Provider(ObservedProvider):
        blocked = None

        def select(self, seed, *, targets=None):
            selected = super().select(seed, targets=targets)
            requests = tuple(_request(str(rank), targets=(goal,), rank=rank)
                             for rank, goal in enumerate(("g1", "g1", "g2")))
            return ProbeSelection(requests, selected.catalogue_digest, replay=selected.replay)

        def query_targets(self, targets, *, seed):
            if dict(seed.queues)["in"][0][0] == 1 and targets == ("g2",):
                self.blocked = seed.fingerprint
            return targets

        def seed_refusal(self, seed):
            if seed.fingerprint == self.blocked:
                return api.Refusal("seed-unavailable", "only this seed cannot continue")

    provider = Provider({}, outcome=Outcome.LOCAL_UNSAT)
    seeds = tuple(_stimulus([value]) for value in range(2))
    result = generate(seeds, provider, contract=contract(),
                      generation_spec=_spec(seeds[0]), probe_budget=12)
    assert provider.blocked == seeds[1].fingerprint
    # The last-ranked g2 request was first for seed 1. None of its three
    # questions ran, rather than only the old rank-based one-request suffix.
    assert result.telemetry["requests_deferred"] == 3
    assert len(provider.probes) == 3
    assert result.telemetry["coverage"]["remaining"] == ("g1", "g2")


@pytest.mark.parametrize("workers", (1, 2))
@pytest.mark.parametrize("stop", (False, True))
def test_descendants_precede_parent_tail_and_resume_same_lane(workers, stop):
    queue, events, closed, owners = deque(("root", "other")), [], [], {}
    provider, telemetry = Sessions(), {}

    def explore(seed, lane):
        owners[seed] = lane
        try:
            answer = yield ("select", lambda: seed)
            assert answer == seed
            events.append((seed, "selected"))
            child = {"root": "child", "child": "grandchild"}.get(seed)
            if child:
                queue.append(child)
                yield ExploreSuccessor(child)
                assert owners[seed] is lane
                events.append((seed, "resumed"))
            if seed == "grandchild" and stop:
                raise RuntimeError("stop with suspended parents")
            answer = yield ("probe", lambda: (seed, lane))
            assert answer == (seed, lane)
            events.append((seed, "done"))
        finally:
            closed.append(seed)

    budget = SearchBudget(SearchPolicy(time_budget_s=30))
    with search_scope(budget.direction(1)):
        if stop:
            with pytest.raises(RuntimeError, match="suspended parents"):
                run_seed_worklist(queue, provider, explore, lambda: True,
                                  workers=workers, telemetry=telemetry)
        else:
            run_seed_worklist(queue, provider, explore, lambda: True,
                              workers=workers, telemetry=telemetry)
    assert set(closed) == set(owners) and len(closed) == len(owners)
    assert owners["root"] is owners["child"] is owners["grandchild"]
    assert events.index(("grandchild", "selected")) < len(events)
    if not stop:
        assert events.index(("grandchild", "done")) < events.index(("root", "resumed"))
        assert events.index(("child", "selected")) < events.index(("root", "done"))
        assert set(owners) == {"root", "child", "grandchild", "other"}
        assert telemetry["worklist"]["seeds_resumed"] == 2
    assert telemetry["worklist"]["successors_selected"] == 2
    assert telemetry["worklist"]["peak_suspended_per_lane"] == 2
    assert sorted(provider.closed) == ([] if workers == 1 else [0, 1])


@pytest.mark.parametrize("gain", (False, True))
@pytest.mark.parametrize("chained", (False, True))
def test_native_progress_and_query_fairness_both_preserve_parent_tail(gain, chained):
    class Provider(ObservedProvider):
        def select(self, seed, *, targets=None):
            value = dict(seed.queues)["in"][0][0]
            selected = super().select(seed, targets=targets)
            requests = ((_request("first", targets=("g1", "g2") if chained else ("g1",)),
                         _request("tail", targets=("g1", "g2") if chained else ("g2",), rank=1))
                        if value == 0 else (_request("child", targets=("g2",)),))
            chains = ((requests[0].identity, requests[1].identity),) if chained and value == 0 else ()
            return ProbeSelection(requests, selected.catalogue_digest,
                                  replay=selected.replay, query_chains=chains)

        def probe(self, seed, request):
            self.events.append(("question", request.occurrence))
            if request.occurrence == "first":
                return ProbeResult(Outcome.SAT, Scope.EXACT_LOCAL_HIT,
                                   goal="g1", candidate=_replace(seed, 0, 7))
            return ProbeResult(Outcome.LOCAL_UNSAT, Scope.EXACT_LOCAL_HIT)

    provider = Provider({7: ("g1",)} if gain else {})
    seed = _stimulus([0])
    result = generate((seed,), provider, contract=contract(),
                      generation_spec=_spec(seed), probe_budget=8)
    questions = [name for event, name in provider.events if event == "question"]
    assert questions == (["first", "tail", "child"] if chained and not gain
                         else ["first", "child", "tail"])
    telemetry = result.as_dict()["telemetry"]
    assert telemetry["worklist"]["successors_selected"] == int(gain)
    assert telemetry["worklist"]["seeds_resumed"] == int(gain or not chained)
    assert telemetry["worklist"]["query_handoffs"] == int(not gain and not chained)
    assert telemetry["termination"] == "queue-empty"
    assert telemetry["coverage"]["hits"] == (["g1"] if gain else [])


def test_last_legacy_probe_answer_is_consumed_before_budget_stop():
    provider = ObservedProvider({0: ("g1",), 7: ("g1", "g2")})
    seed = _stimulus([0])
    result = generate((seed,), provider, contract=contract(),
                      generation_spec=_spec(seed), probe_budget=1)
    assert len(result.generated) == 1
    telemetry = result.as_dict()["telemetry"]
    assert telemetry["outcomes"][Outcome.SAT.value] == 1
    assert telemetry["coverage"]["remaining"] == []
    assert telemetry["termination"] == "goals-covered"


def test_preparation_without_new_coverage_is_replayed_then_explored():
    class Provider(ObservedProvider):
        def probe(self, seed, request):
            value = dict(seed.queues)['in'][0][0]
            self.events.append(('query', value))
            if value == 0:
                return ProbeResult(Outcome.PROPOSED, Scope.STATE_PREPARATION,
                                   candidate=_replace(seed, 0, 7))
            return ProbeResult(Outcome.LOCAL_UNSAT, Scope.EXACT_LOCAL_HIT)

    provider = Provider({})
    seed = _stimulus([0])
    result = generate((seed,), provider, contract=contract(),
                      generation_spec=_spec(seed), probe_budget=4)
    assert ('query', 7) in provider.events
    assert provider.events.index(('replay', 7)) < provider.events.index(('query', 7))
    telemetry = result.as_dict()['telemetry']
    assert telemetry['coverage']['hits'] == []
    assert telemetry['outcomes']['proposed'] == 1 and telemetry['outcomes']['sat'] == 0
    assert telemetry['worklist']['successors_selected'] == 1


@pytest.mark.parametrize('workers', (1, 2))
@pytest.mark.parametrize('stop', (False, True))
def test_preparation_handoff_is_fair_and_keeps_unfinished_construction_owned(workers, stop):
    queue, events, owners, closed = deque((0, 100)), [], {}, []
    provider, telemetry = Sessions(), {}

    def explore(seed, lane):
        owners[seed] = lane
        events.append((seed, 'selected'))
        try:
            yield ('probe', lambda: None)
            if seed < 6:
                queue.append(seed + 1)
                yield ExploreSuccessor(seed + 1, progress=False)
                if stop and seed == 0:
                    raise RuntimeError('stop after preparation handoff')
            # Once a full query starts its pieces keep their lane. Preparation
            # children may run before it starts, not during construction.
            for piece in range(2):
                yield ('probe', lambda: None)
                events.append((seed, piece))
            yield QueryComplete()
        finally:
            closed.append(seed)

    with search_scope(SearchBudget(SearchPolicy(time_budget_s=30)).direction(1)):
        if stop:
            with pytest.raises(RuntimeError, match='preparation handoff'):
                run_seed_worklist(queue, provider, explore, lambda: True,
                                  workers=workers, telemetry=telemetry)
        else:
            run_seed_worklist(queue, provider, explore, lambda: True,
                              workers=workers, telemetry=telemetry)
    assert set(closed) == set(owners) and len(closed) == len(owners)
    if not stop:
        assert set(owners) == {*range(7), 100}
        for seed in range(6):
            assert owners[seed] is owners[seed + 1]
        assert events.index((1, 'selected')) < events.index((0, 0))
        # Recursively prepared children cannot indefinitely displace old work.
        assert events.index((0, 1)) < events.index((2, 'selected'))
        assert telemetry['worklist']['preparations_deferred'] == 6
        assert telemetry['worklist']['successors_selected'] == 6
        assert telemetry['worklist']['suspended_at_stop'] == 0
    for lane in {id(owner) for owner in owners.values()}:
        pieces = [event for event in events if event[1] in (0, 1)
                  and id(owners[event[0]]) == lane]
        assert all(first[0] == second[0] and (first[1], second[1]) == (0, 1)
                   for first, second in zip(pieces[::2], pieces[1::2], strict=True))
    assert sorted(provider.closed) == ([] if workers == 1 else [0, 1])


def test_common_preparation_explores_child_before_starting_full_parent_query():
    class Provider(ObservedProvider):
        def select(self, seed, *, targets=None):
            selected = super().select(seed, targets=targets)
            value = dict(seed.queues)['in'][0][0]
            requests = ((_request('native', targets=('g1',)),
                         _request('full', targets=('g1',), rank=1)) if value == 0 else
                        (_request('child', targets=('g1',)),))
            chains = ((requests[0].identity, requests[1].identity),) if value == 0 else ()
            return ProbeSelection(requests, selected.catalogue_digest, replay=selected.replay,
                                  query_chains=chains)

        def probe(self, seed, request):
            self.events.append(('question', request.occurrence))
            if request.occurrence == 'native':
                return ProbeResult(Outcome.PROPOSED, Scope.STATE_PREPARATION,
                                   candidate=_replace(seed, 0, 7))
            return ProbeResult(Outcome.LOCAL_UNSAT, Scope.EXACT_LOCAL_HIT)

    provider, seed = Provider({}), _stimulus([0])
    result = generate((seed,), provider, contract=contract(),
                      generation_spec=_spec(seed), probe_budget=6)
    assert [name for event, name in provider.events if event == 'question'] == ['native', 'child', 'full']
    assert provider.events.index(('replay', 7)) < provider.events.index(('question', 'child'))
    assert result.telemetry['worklist']['preparations_deferred'] == 1
    assert result.telemetry['coverage']['hits'] == ()


@pytest.mark.parametrize('same_family', (False, True))
@pytest.mark.parametrize('exploratory_setup', (False, True))
def test_preparation_resume_runs_next_context_instead_of_yielding_twice(same_family, exploratory_setup):
    from behave.sim.query_plan import QueryPlan, QueryTemplate

    class Provider(ObservedProvider):
        explores_contexts = True

        def select(self, seed, *, targets=None):
            selected = super().select(seed, targets=targets)
            requests = (_request('prepare', targets=('g1',)),
                        _request('context', targets=('g1' if same_family else 'g2',), rank=1))
            plan = QueryPlan(tuple(QueryTemplate(r.targets, r.occurrence, r.mutable_fields,
                r.rank, catalogue_digest=selected.catalogue_digest) for r in requests),
                selected.catalogue_digest, explorations=(0, 1) if exploratory_setup else (1,),
                preparations=(0,) if exploratory_setup else ())
            return ProbeSelection(plan, selected.catalogue_digest, replay=selected.replay)

        def probe(self, seed, request):
            value = dict(seed.queues)['in'][0][0]
            self.events.append(('question', (value, request.occurrence)))
            if request.occurrence == 'prepare':
                return ProbeResult(Outcome.PROPOSED, Scope.STATE_PREPARATION,
                                   candidate=_replace(seed, 0, value + 1))
            return ProbeResult(Outcome.UNKNOWN, Scope.STATE_PREPARATION)

    provider, seed = Provider({value: ('g2',) for value in range(8)}), _stimulus([0])
    result = generate((seed,), provider, contract=contract(),
                      generation_spec=_spec(seed), probe_budget=3)
    questions = [value for event, value in provider.events if event == 'question']
    assert questions == [(0, 'prepare'),
                         (1, 'context' if same_family else 'prepare'), (0, 'context')]
    assert provider.events.index(('replay', 1)) < provider.events.index(('question', questions[1]))
    assert result.telemetry['coverage']['hits'] == ('g2',)
    assert result.telemetry['outcomes']['unknown'] == (2 if same_family else 1)


@pytest.mark.parametrize('gain', (False, True))
def test_native_successor_continues_its_unfinished_family_before_unrelated_work(gain):
    from behave.sim.core.catalogue import COVERAGE, CatalogueManifest, Goal
    from behave.sim.generation import ProbeRequest, StimulusField
    from behave.sim.generation_feedback import ReplayFeedback

    manifest = CatalogueManifest(tuple(
        Goal(name, COVERAGE, family='decision', provider='python-structural',
             event=name, policy='test/v1') for name in ('g1', 'g2', 'g3')))

    class Provider(ObservedProvider):
        targetable_goals = ('g1', 'g2', 'g3')

        def catalogue_manifest(self):
            return manifest

        def select(self, seed, *, targets=None):
            value = dict(seed.queues)['in'][0][0]
            self.events.append(('replay', value))
            choices = ((('prepare', ('g1', 'g2')), ('unrelated', ('g3',))) if value == 0
                       else (('continue', ('g2',)), ('unrelated', ('g3',))))
            requests = tuple(ProbeRequest(goals, occurrence=name, rank=rank,
                mutable_fields=(StimulusField('in', 0, 0, 3),),
                catalogue_digest=manifest.digest())
                for rank, (name, goals) in enumerate(choices))
            replay = ReplayFeedback(seed.fingerprint, manifest.digest(), seed.fingerprint,
                                    ('g1',) if value == 7 and gain else ())
            return ProbeSelection(requests, manifest.digest(), replay=replay)

        def probe(self, seed, request):
            value = dict(seed.queues)['in'][0][0]
            self.events.append(('question', (value, request.occurrence)))
            if request.occurrence == 'prepare':
                return ProbeResult(Outcome.SAT if gain else Outcome.PROPOSED,
                    Scope.EXACT_LOCAL_HIT if gain else Scope.STATE_PREPARATION,
                    goal='g1' if gain else None, candidate=_replace(seed, 0, 7))
            return ProbeResult(Outcome.LOCAL_UNSAT, Scope.EXACT_LOCAL_HIT)

    provider, seed = Provider({}), _stimulus([0])
    result = generate((seed,), provider, contract=contract(),
                      generation_spec=_spec(seed), probe_budget=8)
    questions = [value for event, value in provider.events if event == 'question']
    assert questions[:2] == [(0, 'prepare'), (7, 'continue')]
    assert set(questions) == {(0, 'prepare'), (7, 'continue'),
                              (0, 'unrelated'), (7, 'unrelated')}
    assert provider.events.index(('replay', 7)) < provider.events.index(('question', (7, 'continue')))
    assert result.as_dict()['telemetry']['coverage']['hits'] == (['g1'] if gain else [])


@pytest.mark.parametrize('budget', [1, 2, 3])
def test_construction_slices_resume_and_each_counts_against_shared_probe_cap(budget):
    class Building(ObservedProvider):
        def probe(self, seed, request):
            self.probes.append(request)
            if len(self.probes) < 3:
                return ProbeResult(Outcome.CONSTRUCTING, Scope.RTL_LOCAL_HIT)
            return ProbeResult(Outcome.SAT, Scope.RTL_LOCAL_HIT,
                               goal='g1', candidate=_replace(seed, 0, 7))
    provider = Building({7: ('g1', 'g2')})
    seed = _stimulus([0])
    result = generate((seed,), provider, contract=contract(),
                      generation_spec=_spec(seed), probe_budget=budget)
    telemetry = result.as_dict()['telemetry']
    assert len(provider.probes) == budget
    assert len({request.identity for request in provider.probes}) == 1
    assert telemetry['outcomes']['constructing'] == min(budget, 2)
    assert len(result.generated) == int(budget == 3)
    assert telemetry['coverage']['hits'] == (['g1', 'g2'] if budget == 3 else [])


def test_solver_unknown_advances_instead_of_resuming():
    provider = ObservedProvider({}, outcome=Outcome.UNKNOWN)
    seed = _stimulus([0])
    result = generate((seed,), provider, contract=contract(),
                      generation_spec=_spec(seed), probe_budget=5)
    assert len(provider.probes) == 1 and not result.generated
    assert result.telemetry['outcomes']['unknown'] == 1


def test_search_policy_binds_worklist_order():
    image = api.SearchPolicy().as_dict()
    assert image["worklist"] == "context-frontier+goal-family+native-progress+preparation-handoff+query-chain/17"
    assert api.SearchPolicy.from_dict(image).as_dict() == image
    with pytest.raises(ValueError, match="policy record"):
        api.SearchPolicy.from_dict({**image, "worklist": "FIFO"})


@pytest.mark.isolation
@pytest.mark.parametrize("workers", (1, 2))
def test_public_stateful_search_natively_finds_deep_error(
        tmp_path, monkeypatch, workers):
    import json
    from behave.core import runtime_defaults as defaults
    from .test_settled_protocol import contract as settled_contract

    monkeypatch.setattr(defaults, "SIM_SEARCH_WORKERS", workers)
    source = '''class M:
    def reset(self):
        self.unlocked = False
    def process(self, in_a):
        out_y = (in_a * 2) & 255
        if self.unlocked:
            if in_a == 83:
                out_y = 52
        if in_a == 37:
            self.unlocked = True
        return {"out_y": out_y}
'''
    c = settled_contract()
    policy = api.BaselinePolicy(case_limit=4, max_queue_depth=4)
    baseline = api.build_baseline(c, policy=policy)
    assert all(row[0] not in (37, 83) for seed in baseline.stimuli
               for row in dict(seed.queues)["in"])
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source.encode()}, config={"class": "M"})
    candidate = source.replace("out_y = 52", "out_y = 0")
    path = tmp_path / "behavior.py"
    path.write_text(candidate)
    with api.CpuPool(workers) as cpu, api.cpu_scope(cpu.endpoint):
        task = api.admit(c, api.realization_of_behavior_source(
            candidate, c, file=path.name), reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=path.name),
                                      baseline.generation_spec)
        result = api.handle_command(handle.command_request(
            run_directory=tmp_path / "invocation", reference=reference, baseline=policy,
            providers=handle.generation_providers(reference, workdir=tmp_path / "search")[:1],
            search_policy=api.SearchPolicy(time_budget_s=60)))
        (tmp_path / "control_result.json").write_text(json.dumps(result.as_dict(), indent=2))
        assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
        assert result.decision.authoritative and not result.decision.passed
        search, = result.generated.searches
        telemetry = search.as_dict()["telemetry"]
        assert not telemetry.get("failure"), telemetry
        # A joint window can now find the full sequence directly. Requiring a
        # second-generation seed would demand redundant queries after the real
        # counterexample is already found. The worklist tests above separately
        # require productive successors to run and suspended parents to resume.
        # Proposals deliberately name no proven goal. Require the replay's
        # actual requested-goal hits, not a solver claim of the same event.
        confirmations = [answer.get("native_replay", {}) for answer in telemetry["answers"]]
        assert any(replay.get("status") in ("requested-goal-hit", "named-goal-hit",
                                          "other-requested-goal-hit")
                   and replay.get("hit_targets") and replay.get("record")
                   for replay in confirmations)
        assert any(37 in values[:values.index(83)] for seed in search.generated
                   if 83 in (values := [row[0] for row in dict(seed.queues)["in"]]))
        assert result.decision.as_dict()["evidence"]["mismatch_count"] > 0
        assert cpu.snapshot()["reserved"] == 0 and not cpu.snapshot()["quarantined"]
