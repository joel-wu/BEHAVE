"""Baseline-first coverage scheduling, independently of solver/backend choices."""

from types import SimpleNamespace

import pytest

from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.generation import ProbeRequest, ProbeSelection, generate, generate_independently
from behave.sim.generation_feedback import ReplayFeedback, SearchCoverage, selection_targets

from .test_api_path import contract
from .test_generation_scheduler import (
    _canonical,
    _frontend_catalogue,
    _Provider,
    _replace,
    _request,
    _spec,
    _stimulus,
)


class ObservedProvider(_Provider):
    replay_guided = True

    def __init__(self, hits, *, answer=7, outcome=probing.Outcome.SAT):
        super().__init__({}, {})
        self.hits, self.answer, self.outcome = hits, answer, outcome
        self.events = []

    def select(self, seed, *, targets=None):
        value = dict(seed.queues)["in"][0][0]
        self.events.append(("replay", value))
        manifest = self.catalogue_manifest()
        feedback = ReplayFeedback(seed.fingerprint, manifest.digest(), seed.fingerprint,
                                  self.hits.get(value, ()))
        return ProbeSelection([_request("all", targets=("g1", "g2"))],
                              manifest.digest(), replay=feedback)

    def probe(self, seed, request):
        self.events.append(("probe", request.targets))
        self.probes.append(request)
        return probing.ProbeResult(self.outcome, probing.Scope.EXACT_LOCAL_HIT,
            goal=request.targets[0] if self.outcome is probing.Outcome.SAT else None,
            candidate=_replace(seed, 0, self.answer) if self.outcome is probing.Outcome.SAT else None)


def run(provider, values=(0, 1), *, budget=4):
    baseline = _canonical(*(_stimulus([value]) for value in values))
    return generate(baseline, provider, contract=contract(),
                    generation_spec=_spec(baseline[0]), probe_budget=budget)


def test_whole_baseline_union_covers_targets_without_any_probe():
    provider = ObservedProvider({0: ("g1",), 1: ("g2",)})
    result = run(provider)
    assert len(provider.events) == 2 and not provider.probes
    assert not result.generated
    assert result.telemetry["termination"] == "goals-covered"
    assert result.as_dict()["telemetry"]["coverage"]["remaining"] == []


def test_nested_generations_own_separate_acceptance_and_feedback(monkeypatch):
    from behave.sim import generation

    owners = []
    initialize = generation._SearchRun.__init__

    def retain_owner(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        owners.append(self)

    monkeypatch.setattr(generation._SearchRun, '__init__', retain_owner)
    nested_results = []

    class NestedProvider(ObservedProvider):
        def probe(self, seed, request):
            inner = ObservedProvider({0: ('g2',), 11: ('g1',)}, answer=11)
            nested_results.append(run(inner, values=(0,)))
            return super().probe(seed, request)

    result = run(NestedProvider({0: ('g1',), 7: ('g2',)}), values=(0,))
    assert len(nested_results) == 1 and len(owners) == 2
    outer, inner = owners
    for name in ('queue', 'seen', 'fingerprints', 'generated', 'provenance',
                 'coverage', 'telemetry', 'target_set_ids', 'candidate_answers',
                 'preselected', 'in_flight_targets', 'family_visits',
                 'successor_targets', 'context_visits', 'lane_extensions'):
        assert getattr(outer, name) is not getattr(inner, name)
    for owner in owners:
        assert not {'local_unsat', 'pending_requests', 'construction_request',
                    'construction_seconds', 'started_families'} & vars(owner).keys()
    assert [dict(seed.queues)['in'][0][0] for seed in result.generated] == [7]
    assert [dict(seed.queues)['in'][0][0] for seed in nested_results[0].generated] == [11]
    assert [request.targets for request in outer.provider.probes] == [('g2',)]
    assert [request.targets for request in inner.provider.probes] == [('g1',)]
    assert result.telemetry['candidates_retained'] == 1
    assert nested_results[0].telemetry['candidates_retained'] == 1


def test_reused_provider_does_not_reuse_a_previous_run_ledger():
    provider = ObservedProvider({0: ('g1',), 7: ('g2',)})
    first = run(provider, values=(0,))
    saved = first.as_dict()
    provider.hits = {0: ('g1', 'g2')}
    second = run(provider, values=(0,))
    assert second.generated == () and second.telemetry['probes'] == 0
    assert second.telemetry['candidates_retained'] == 0
    assert second.as_dict()['telemetry']['coverage']['hits'] == ['g1', 'g2']
    assert first.as_dict() == saved


def test_stopping_at_candidate_admission_preserves_answer_but_no_partial_candidate(monkeypatch):
    from behave.sim import generation
    from behave.sim.search_policy import SearchStopped

    admit = generation._SearchRun.admit_candidate
    checkpoint = generation.check_execution
    admitting = False

    def interrupt_admission():
        if admitting:
            raise SearchStopped('test-admission-stop')
        checkpoint()

    def candidate_boundary(self, *args):
        nonlocal admitting
        admitting = True
        try:
            return admit(self, *args)
        finally:
            admitting = False

    monkeypatch.setattr(generation, 'check_execution', interrupt_admission)
    monkeypatch.setattr(generation._SearchRun, 'admit_candidate', candidate_boundary)
    result = run(ObservedProvider({0: ('g1',), 7: ('g2',)}), values=(0,))
    assert result.telemetry['termination'] == 'test-admission-stop'
    assert result.telemetry['outcomes']['sat'] == 1
    assert result.generated == () and not result.provenance
    assert result.telemetry['candidates_retained'] == 0
    assert result.as_dict()['telemetry']['coverage']['hits'] == ['g1']
    assert 'stimulus' not in result.as_dict()['telemetry']['answers'][0]


def test_context_proposals_continue_after_all_public_goals_are_hit():
    from behave.sim.query_plan import QueryPlan, QueryTemplate

    class ContextProvider(ObservedProvider):
        explores_contexts = True

        def select(self, seed, *, targets=None):
            selected = super().select(seed, targets=targets)
            assert set(targets) == {'g1', 'g2'}
            request = selected.requests[0]
            template = QueryTemplate(request.targets, request.occurrence,
                request.mutable_fields, request.rank, catalogue_digest=request.catalogue_digest)
            plan = QueryPlan((template,), selected.catalogue_digest, explorations=(request.rank,))
            return ProbeSelection(plan, selected.catalogue_digest, replay=selected.replay)

        def probe(self, seed, request):
            self.probes.append(request)
            return probing.ProbeResult(probing.Outcome.PROPOSED, probing.Scope.STATE_PREPARATION,
                                       candidate=_replace(seed, 0, 7))

    provider = ContextProvider({0: ('g1', 'g2'), 7: ('g1', 'g2')})
    result = run(provider, values=(0,), budget=4)
    assert len(result.generated) == 1 and len(provider.probes) == 2
    assert result.as_dict()['telemetry']['coverage']['hits'] == ['g1', 'g2']
    assert result.telemetry['termination'] == 'queue-empty'
    assert ('replay', 7) in provider.events


def test_covered_questions_cannot_push_live_state_setup_behind_context_work():
    from behave.sim.query_plan import QueryPlan, QueryTemplate

    class ContextProvider(ObservedProvider):
        explores_contexts = True

        def select(self, seed, *, targets=None):
            selected = super().select(seed, targets=targets)
            fields = selected.requests[0].mutable_fields
            templates = tuple(QueryTemplate((goal,), name, fields, rank,
                catalogue_digest=selected.catalogue_digest) for rank, (name, goal) in enumerate((
                    ('covered-0', 'g1'), ('covered-1', 'g1'), ('live', 'g2'), ('context', 'g1'))))
            return ProbeSelection(QueryPlan(templates, selected.catalogue_digest, explorations=(3,)),
                                  selected.catalogue_digest, replay=selected.replay)

        def probe(self, seed, request):
            self.probes.append(request.occurrence)
            return probing.ProbeResult(probing.Outcome.UNKNOWN, probing.Scope.STATE_PREPARATION) \
                if request.occurrence == 'context' else probing.ProbeResult(
                    probing.Outcome.LOCAL_UNSAT, probing.Scope.EXACT_LOCAL_HIT)

    provider = ContextProvider({0: ('g1',)})
    result = run(provider, values=(0,), budget=4)
    assert provider.probes == ['live', 'context']
    assert result.telemetry['requests_covered'] == 2
    assert result.as_dict()['telemetry']['coverage']['remaining'] == ['g2']


def test_continuous_baseline_hits_retire_goals_without_becoming_query_seeds():
    from behave.sim.replay_suite import build_replay_suite

    from .test_replay_suite import _requests

    baseline = _requests(tail=0)
    suite = build_replay_suite(contract(), baseline)

    class RecordedProvider(ObservedProvider):
        runtime_guidance = True

        def observe_many(self, seeds):
            assert seeds == suite.stimuli
            manifest = self.catalogue_manifest()
            return tuple(ReplayFeedback(
                seed.fingerprint, manifest.digest(), seed.fingerprint,
                ("g2",) if seed in suite.continuous else ("g1",)) for seed in seeds)

        def select(self, *args, **kwargs):
            pytest.fail("both goals were already hit by mandatory baseline runs")

    provider = RecordedProvider({})
    result = generate(baseline, provider, contract=contract(), generation_spec=None,
                      probe_budget=4, _execution=SimpleNamespace(has_failure_witness=False))
    assert result.baseline == baseline and not result.generated and not provider.probes
    coverage = result.as_dict()["telemetry"]["baseline_coverage"]
    assert coverage["hits"] == ["g1", "g2"] and coverage["remaining"] == []
    assert result.telemetry["termination"] == "goals-covered"


@pytest.mark.parametrize("gain", [2, 3])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("debug", [False, True])
def test_candidate_scoring_stops_all_directions_only_after_actual_replay(
        monkeypatch, gain, legacy, debug):
    from contextlib import contextmanager

    from behave.sim.deferred_generation import deferred_provider
    from behave.sim.search_policy import SearchPolicy, active_search

    from .test_execution_reuse import _execution

    execution, calls = _execution(monkeypatch, gain=gain)
    baseline = (_stimulus([0]),)
    execution.evaluate(baseline)
    closed, preparations = [], []

    class RecordedProvider(ObservedProvider):
        def observe_many(self, seeds):
            execution.traces(seeds)
            return tuple(ReplayFeedback(s.fingerprint, self.catalogue_manifest().digest(),
                                        s.fingerprint, ("g1",)) for s in seeds)

        def select(self, seed, *, targets=None):
            assert not execution.has_failure_witness, "do not Select after a scored counterexample"
            return super().select(seed, targets=targets)

        def probe(self, seed, request):
            answer = super().probe(seed, request)
            assert not execution.has_failure_witness, "SAT alone must not set the stop"
            return answer

        @contextmanager
        def search_scope(self, *, probe_budget):
            try:
                yield
            finally:
                closed.append(True)

    first = RecordedProvider({0: ("g1",), 7: ("g1",)})
    second = ObservedProvider({0: ("g1", "g2")})
    second.name = "second"

    def prepare(**_kwargs):
        preparations.append(True)
        assert gain == 2, "remaining directions must not prepare after candidate failure"
        return second

    deferred = deferred_provider(second.name, second.policy, {}, prepare)
    portfolio = generate_independently(baseline, (first, deferred), contract=contract(),
        generation_spec=_spec(baseline[0]), _execution=execution,
        **({"probe_budget": 4} if legacy else {
            "search_policy": SearchPolicy(time_budget_s=60, max_probes=4, debug=debug)}))
    a, b = portfolio.searches
    assert len(a.generated) == 1
    assert len(calls) == 2  # Baseline and the new input, no duplicated final replay.
    assert closed == [True] and active_search() is None
    assert a.as_dict()["telemetry"]["coverage"]["remaining"] == ["g2"]
    assert a.provenance  # The failing input was admitted before the actual replay.
    if gain == 3:
        assert a.telemetry["termination"] == b.telemetry["termination"] == "candidate-failed"
        assert a.telemetry["probes"] == 1 and b.telemetry["probes"] == 0
        assert not preparations
    else:
        assert preparations == [True] and b.telemetry["termination"] == "goals-covered"
        assert len(first.probes) > 1


def test_observed_catalogue_is_not_narrowed_to_the_solver_capability():
    manifest = ObservedProvider({}).catalogue_manifest()
    seed = _stimulus([0])
    coverage = SearchCoverage(manifest, ("g2",))
    coverage.observe(seed, ReplayFeedback(seed.fingerprint, manifest.digest(),
                                         seed.fingerprint, ("g1",)))
    image = coverage.as_dict()
    assert image["version"] == "generation-coverage/2"
    assert image["declared"] == ["g1", "g2"]
    assert image["hits"] == ["g1"]
    assert image["targetable"] == image["remaining"] == image["unhit"] == ["g2"]
    assert image["not_targeted"] == ["g1"]
    assert coverage.restrict(_request("all", targets=("g1", "g2"))).targets == ("g2",)


def test_partial_feedback_does_not_reserialize_failure_logs():
    from behave.sim.core.catalogue import catalogue_subset
    from behave.sim.core.failure import ObservedGoals, RunFailure
    from behave.sim.core.refusal import Refusal
    from behave.sim.evaluation import ReplayExecution

    manifest = ObservedProvider({}).catalogue_manifest()
    seed = _stimulus([0])
    observed = ObservedGoals(seed.fingerprint, catalogue_subset(manifest, {"g1"}))

    class RetainedFailure:
        trace = None
        observations_eligible = True
        run_failure = RunFailure.candidate("replay", Refusal("test-stop", "stopped"), observed=observed)

        def as_dict(self):
            pytest.fail("feedback must not walk the retained logs or full native failure payload")

    execution = ReplayExecution(None, None, None, manifest)
    execution._assessments[seed.identity_bytes] = SimpleNamespace(result=RetainedFailure(), hits={"g1"})
    feedback, = execution.feedback((seed,), manifest)
    assert feedback.hits == ("g1",)
    assert feedback.stimulus == seed.fingerprint
    assert execution.feedback((seed,), manifest)[0].as_dict() == feedback.as_dict()


@pytest.mark.parametrize("budget", (0, 4))
def test_unavailable_queries_keep_completed_baseline_observations(budget):
    from behave.sim.core.refusal import Refusal
    from behave.sim.generation import UnavailableGenerationProvider

    manifest = ObservedProvider({}).catalogue_manifest()
    observed = []

    def read_completed(seeds, catalogue):
        observed.extend(seeds)
        assert catalogue is manifest
        return tuple(ReplayFeedback(seed.fingerprint, manifest.digest(), seed.fingerprint,
                                    ("g1",)) for seed in seeds)

    provider = UnavailableGenerationProvider("unavailable", "test/1",
        Refusal("query-not-implemented", "source query is not attached"),
        catalogue=manifest, observe=read_completed)
    result = run(provider, budget=budget)
    image = result.as_dict()["telemetry"]
    assert len(observed) == 2
    assert not result.generated and image["probes"] == 0
    assert image["termination"] == "provider-unavailable"
    baseline = image["baseline_coverage"]
    assert baseline["declared"] == baseline["not_targeted"] == ["g1", "g2"]
    assert baseline["hits"] == ["g1"] and baseline["unhit"] == ["g2"]
    assert baseline["targetable"] == baseline["remaining"] == []
    assert image["coverage"] == baseline


@pytest.mark.parametrize("covered", (False, True))
def test_recorded_baseline_feedback_precedes_and_can_eliminate_select(covered):
    class RecordedProvider(ObservedProvider):
        def observe_many(self, seeds):
            self.events.append(("observe", len(seeds)))
            manifest = self.catalogue_manifest()
            return tuple(ReplayFeedback(s.fingerprint, manifest.digest(), s.fingerprint,
                         ("g1", "g2") if covered else ("g1",)) for s in seeds)

        def select_many(self, _seeds):
            pytest.fail("baseline observations do not need bulk Select")

    provider = RecordedProvider({0: ("g1",), 1: (), 7: ("g2",)})
    result = run(provider)
    assert provider.events[0] == ("observe", 2)
    if covered:
        assert provider.events == [("observe", 2)] and not provider.probes
    else:
        assert provider.probes[0].targets == ("g2",)
    assert result.telemetry["termination"] == "goals-covered"


def test_terminal_candidate_observation_does_not_require_a_new_query_boundary():
    class RecordedProvider(ObservedProvider):
        def observe_many(self, seeds):
            values = [dict(seed.queues)["in"][0][0] for seed in seeds]
            self.events.append(("observe", tuple(values)))
            manifest = self.catalogue_manifest()
            return tuple(ReplayFeedback(seed.fingerprint, manifest.digest(), seed.fingerprint,
                                        self.hits[value]) for seed, value in zip(seeds, values))

        def select(self, seed, *, targets=None):
            assert dict(seed.queues)["in"][0][0] != 7, "a stopped run is not a normal query seed"
            return super().select(seed, targets=targets)

    provider = RecordedProvider({0: ("g1",), 1: (), 7: ("g2",)})
    result = run(provider)
    assert provider.events[-1] == ("observe", (7,))
    assert result.telemetry["coverage"]["hits"] == ("g1", "g2")
    assert result.telemetry["termination"] == "goals-covered"
    assert len(result.generated) == 1


@pytest.mark.parametrize("outcome", [probing.Outcome.SAT, probing.Outcome.LOCAL_UNSAT,
                                    probing.Outcome.UNKNOWN])
def test_residual_select_equals_full_select_then_filter(outcome):
    class RecordedProvider(ObservedProvider):
        def __init__(self, *, restrict_early):
            super().__init__({0: ("g1",), 1: (), 7: ("g2",)}, outcome=outcome)
            self.restrict_early = restrict_early
            self.offered_targets = []
            self.constructed_targets = []

        def observe_many(self, seeds):
            manifest = self.catalogue_manifest()
            return tuple(ReplayFeedback(
                seed.fingerprint, manifest.digest(), seed.fingerprint,
                self.hits.get(dict(seed.queues)["in"][0][0], ())) for seed in seeds)

        def select(self, seed, *, targets=None):
            self.offered_targets.append(frozenset(targets))
            # The full case is the previous construction path. Both cases still
            # pass through the production scheduler's late restriction.
            selected = super().select(seed, targets=targets)
            goals = selection_targets(self.targetable_goals,
                                      targets if self.restrict_early else None)
            self.constructed_targets.append(goals)
            return ProbeSelection(
                [_request("all", targets=goals)] if goals else (),
                selected.catalogue_digest, replay=selected.replay)

    full = RecordedProvider(restrict_early=False)
    narrow = RecordedProvider(restrict_early=True)
    before, after = run(full), run(narrow)
    assert before.baseline == after.baseline and before.generated == after.generated
    assert [r.as_dict() for r in full.probes] == [r.as_dict() for r in narrow.probes]
    assert full.events == narrow.events
    for name in ("coverage", "outcomes", "termination", "probes"):
        assert before.as_dict()["telemetry"][name] == after.as_dict()["telemetry"][name]
    assert narrow.offered_targets and all(t == {"g2"} for t in narrow.offered_targets)
    assert all(t == ("g2",) for t in narrow.constructed_targets)
    assert all(t == ("g1", "g2") for t in full.constructed_targets)
    assert narrow.targetable_goals == full.targetable_goals == ("g1", "g2")
    if outcome is not probing.Outcome.SAT:
        assert after.as_dict()["telemetry"]["coverage"]["remaining"] == ["g2"]


def test_target_restriction_preserves_provider_order_and_distinguishes_empty():
    available = ("z", "a", "middle")
    assert selection_targets(available) == available
    assert selection_targets(available, {"a", "z"}) == ("z", "a")
    assert selection_targets(available, ()) == ()
    with pytest.raises(ValueError, match="provider's capability"):
        selection_targets(available, ("foreign",))


def test_baseline_precedes_probe_and_candidate_replay_retires_target():
    provider = ObservedProvider({0: ("g1",), 1: (), 7: ("g2",)})
    result = run(provider)
    assert [event[0] for event in provider.events] == ["replay", "replay", "probe", "replay"]
    assert provider.probes[0].targets == ("g2",)
    assert result.telemetry["termination"] == "goals-covered"
    assert len(result.generated) == 1
    # No catalogue mutation, nor an assertion that the candidate passed scoring.
    assert result.as_dict()["telemetry"]["provider_targets"] == ["g1", "g2"]
    assert "passed" not in result.as_dict()["telemetry"]


def test_batch_selection_is_preserved_and_all_feedback_precedes_probe():
    class BatchProvider(ObservedProvider):
        def select_many(self, seeds):
            self.events.append(("batch", len(seeds)))
            return tuple(super(BatchProvider, self).select(seed) for seed in seeds)

    provider = BatchProvider({0: ("g1",), 1: (), 7: ("g2",)})
    result = run(provider)
    assert [event[0] for event in provider.events] == [
        "batch", "replay", "replay", "probe", "replay"]
    assert provider.probes[0].targets == ("g2",)
    assert result.telemetry["termination"] == "goals-covered"


def test_sat_claim_without_native_hit_does_not_retire_goal():
    provider = ObservedProvider({0: ("g1",), 7: ("g1",)})
    result = run(provider, (0,), budget=1)
    assert provider.events[-1] == ("replay", 7)
    assert result.as_dict()["telemetry"]["coverage"]["remaining"] == ["g2"]
    assert result.telemetry["termination"] == "probe-budget-exhausted"


@pytest.mark.parametrize("hits,status,named_hit", [
    (("g1", "g2"), "named-goal-hit", True),
    (("g2",), "other-requested-goal-hit", False),
    ((), "no-requested-goal-hit", False),
])
def test_sat_receipt_keeps_prediction_separate_from_own_native_replay(hits, status, named_hit):
    result = run(ObservedProvider({0: (), 7: hits}), (0,), budget=1)
    image = result.as_dict()["telemetry"]
    answer, = image["answers"]
    assert image["version"] == 16
    assert answer["outcome"] == "sat" and answer["goal"] == "g1"
    assert answer["stimulus"] == result.generated[0].fingerprint
    assert answer["native_replay"] == {
        "status": status, "record": result.generated[0].fingerprint,
        "hit_targets": list(hits), "named_goal_hit": named_hit}
    assert image["coverage"]["hits"] == list(hits)
    assert image["coverage"]["remaining"] == sorted({"g1", "g2"} - set(hits))


def test_unnamed_group_sat_is_confirmed_only_by_a_requested_native_hit():
    class GroupProvider(ObservedProvider):
        def probe(self, seed, request):
            result = super().probe(seed, request)
            result.goal = None
            return result

    result = run(GroupProvider({0: (), 7: ("g2",)}), (0,), budget=1)
    answer, = result.as_dict()["telemetry"]["answers"]
    assert answer["goal"] is None
    assert answer["native_replay"]["status"] == "requested-goal-hit"
    assert answer["native_replay"]["named_goal_hit"] is None
    assert answer["native_replay"]["hit_targets"] == ["g2"]


def test_duplicate_sat_uses_its_existing_replay_without_replaying_again():
    provider = ObservedProvider({0: ("g1",)}, answer=0)
    result = run(provider, (0,), budget=1)
    assert not result.generated
    assert provider.events == [("replay", 0), ("probe", ("g2",))]
    answer, = result.as_dict()["telemetry"]["answers"]
    assert answer["stimulus"] == result.baseline[0].fingerprint
    assert answer["native_replay"]["status"] == "no-requested-goal-hit"


def test_confirmation_never_borrows_another_inputs_or_directions_hits():
    provider = ObservedProvider({})
    manifest = provider.catalogue_manifest()
    left, right = (SearchCoverage(manifest, ("g1", "g2")) for _ in range(2))
    first, second = _stimulus([0]), _stimulus([7])
    left.observe(first, ReplayFeedback(first.fingerprint, manifest.digest(),
                                      first.fingerprint, ("g1",)))
    left.observe(second, ReplayFeedback(second.fingerprint, manifest.digest(),
                                       second.fingerprint, ("g2",)))
    assert left.remaining == set()
    assert left.confirmation(first.fingerprint, ("g1", "g2"), "g2") == {
        "status": "other-requested-goal-hit", "record": first.fingerprint,
        "hit_targets": ["g1"], "named_goal_hit": False}
    assert right.confirmation(first.fingerprint, ("g1", "g2"), "g2") == {
        "status": "not-observed", "record": None,
        "hit_targets": None, "named_goal_hit": None}


@pytest.mark.parametrize("observed", [False, True])
def test_candidate_replay_receipt_survives_next_selection_refusal(observed):
    from behave.sim.core.refusal import Refusal

    class RefusingSelection(ObservedProvider):
        def observe_many(self, seeds):
            manifest = self.catalogue_manifest()
            return tuple(ReplayFeedback(seed.fingerprint, manifest.digest(), seed.fingerprint,
                self.hits[dict(seed.queues)["in"][0][0]]) for seed in seeds) if observed else None

        def select(self, seed, *, targets=None):
            if dict(seed.queues)["in"][0][0] == 7:
                return Refusal("next-boundary-unavailable", "no further query boundary")
            return super().select(seed, targets=targets)

    result = run(RefusingSelection({0: (), 7: ("g2",)}), (0,), budget=1)
    answer, = result.as_dict()["telemetry"]["answers"]
    assert answer["outcome"] == "sat" and answer["goal"] == "g1"
    replay = answer["native_replay"]
    assert replay["status"] == ("other-requested-goal-hit" if observed else "not-observed")
    assert replay["hit_targets"] == (["g2"] if observed else None)


@pytest.mark.parametrize("outcome", [probing.Outcome.LOCAL_UNSAT, probing.Outcome.UNKNOWN])
def test_local_unsat_or_unknown_does_not_remove_goal(outcome):
    result = run(ObservedProvider({0: ("g1",)}, outcome=outcome), (0,))
    assert result.as_dict()["telemetry"]["coverage"]["remaining"] == ["g2"]
    assert result.telemetry["termination"] == "queue-empty"


def test_same_goal_names_in_two_directions_do_not_share_coverage():
    left = ObservedProvider({0: ("g1", "g2")})
    right = ObservedProvider({0: ("g1",)}, outcome=probing.Outcome.LOCAL_UNSAT)
    left.name, right.name = "reference", "candidate"
    seed = _stimulus([0])
    result = generate_independently((seed,), (left, right), contract=contract(),
                                   generation_spec=_spec(seed), probe_budget=2)
    assert not left.probes and right.probes[0].targets == ("g2",)
    assert {r.provider: r.telemetry["termination"] for r in result.searches} == {
        "reference": "goals-covered", "candidate": "queue-empty"}


def test_zero_budget_neither_replays_nor_probes():
    provider = ObservedProvider({})
    result = run(provider, budget=0)
    assert provider.events == [] and result.telemetry["probes"] == 0


def test_deadline_during_baseline_retains_pool_without_starting_probe(monkeypatch):
    from behave.sim import search_policy

    now = [0.0]
    monkeypatch.setattr(search_policy.time, "monotonic", lambda: now[0])

    class SlowReplay(ObservedProvider):
        def select(self, seed, *, targets=None):
            selected = super().select(seed)
            now[0] += 2
            return selected

    provider = SlowReplay({0: ("g1",), 1: ("g2",)})
    lease = search_policy.SearchBudget(
        search_policy.SearchPolicy(time_budget_s=1)).direction(1)
    with search_policy.search_scope(lease):
        result = run(provider)
    assert len(result.baseline) == 2 and not result.generated
    assert not provider.probes
    assert result.telemetry["termination"] == "time-budget-exhausted"
    assert result.as_dict()["telemetry"]["coverage"]["hits"] == []


@pytest.mark.parametrize("changed", ["stimulus", "catalogue", "unknown_goal", "missing"])
def test_ledger_refuses_unbound_or_undeclared_feedback(changed):
    seed = _stimulus([0])
    manifest = _frontend_catalogue()
    coverage = SearchCoverage(manifest, ("g1", "g2"))
    feedback = ReplayFeedback(
        "0" * 64 if changed == "stimulus" else seed.fingerprint,
        "0" * 64 if changed == "catalogue" else manifest.digest(),
        seed.fingerprint, ("not-in-catalogue",) if changed == "unknown_goal" else ("g1",))
    with pytest.raises(Refused, match="generation-replay-feedback-mismatch"):
        coverage.observe(seed, None if changed == "missing" else feedback)
    assert coverage.remaining == {"g1", "g2"}


def test_restricted_request_changes_only_nonempty_target_subset():
    request = _request("site", targets=("g1", "g2"))
    reduced = request.with_targets(("g2",))
    assert reduced.identity != request.identity and reduced.is_restriction_of(request)
    assert not request.is_restriction_of(reduced)
    for targets in ((), ("unregistered",)):
        with pytest.raises(ValueError):
            request.with_targets(targets)
    altered = ProbeRequest(("g2",), occurrence="other", mutable_fields=request.mutable_fields,
                           rank=request.rank, catalogue_digest=request.catalogue_digest)
    assert not altered.is_restriction_of(request)


def test_feedback_wire_image_is_canonical():
    seed = _stimulus([0])
    feedback = ReplayFeedback(seed.fingerprint, _frontend_catalogue().digest(),
                              seed.fingerprint, ("g2", "g1"))
    assert ReplayFeedback.from_dict(feedback.as_dict()).as_dict() == feedback.as_dict()
    image = feedback.as_dict()
    image["hits"].reverse()
    with pytest.raises(ValueError):
        ReplayFeedback.from_dict(image)


def test_behavior_feedback_keeps_resets_in_one_memory_preserving_audit_run():
    from behave.sim.core.audit import AuditCheck
    from behave.sim.engines.behavior.generation_job import _replay_feedback

    calls = []

    def audit(sequences, *, reset_indices, observe_step):
        calls.append((sequences, reset_indices))
        for step, hits in enumerate((("g1",), (), ("g2",))):
            observe_step(0, step, {"count": step % 2}, frozenset(hits), [])
        return AuditCheck.agreed()

    occurrences = (
        {"position": 0, "input": {"a": 1}},
        {"position": 1, "input": {"a": 2}},
        {"position": 0, "input": {"a": 3}},
    )
    observations = SimpleNamespace(observe=lambda previous, inputs, outputs: set())
    feedback, observed = _replay_feedback(
        SimpleNamespace(source_audit=audit),
        _frontend_catalogue(), _stimulus([0]), occurrences, observations)
    assert calls == [((({"a": 1}, {"a": 2}, {"a": 3}),), ((2,),))]
    assert feedback.hits == ("g1", "g2")
    assert [item["state"]["count"] for item in observed] == [0, 1, 0]
    for result in (AuditCheck.inconsistent(["changed hit"]),
                   AuditCheck.refused("source-unavailable", "no replay")):
        with pytest.raises(Refused, match="behavior-generation-feedback-unverified"):
            _replay_feedback(SimpleNamespace(source_audit=lambda _sequences, _result=result, **_kwargs: _result),
                             _frontend_catalogue(), _stimulus([0]), occurrences, observations)
