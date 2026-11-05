"""Production-shaped acceptance for Appendix A.3's common stimulus worklist."""

from __future__ import annotations

import json

import pytest

from behave.sim import api
from behave.sim.core.catalogue import (
    COVERAGE,
    CatalogueManifest,
    Goal,
    OccurrenceKey,
)
from behave.sim.core.monitor import Monitor
from behave.sim.core.outcome import Refused
from behave.sim.core.stimulus import AuditedTrace, EventSlot, SlotRecord, Stimulus
from behave.sim.engines import probing
from behave.sim.generation import (
    GenerationResult,
    ProbeRequest,
    ProbeSelection,
    StimulusField,
    combine_generation,
    generate,
    generate_independently,
    stimulus_bytes,
)
from behave.sim.generation_cache import GenerationCacheTelemetry
from behave.sim.generation_protocol import GenerationExtensions
from behave.sim.stimulus_set import (
    GenerationSpec,
    schedule_provenance_after_generation,
)
from tests.support.device_boundary import evaluate_generated_device

from .test_api_path import contract, realization


def test_optional_ports_bind_without_preparing_or_reading_execution_state():
    from dataclasses import FrozenInstanceError, fields
    from types import SimpleNamespace

    calls = []
    def unexpected(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('binding must not execute a port')

    provider = SimpleNamespace(**{port.name: unexpected for port in fields(GenerationExtensions)
                                 if port.default is None})
    extensions = GenerationExtensions.bind(provider)
    assert not calls
    assert extensions.search_scope is unexpected
    assert not extensions.replay_guided and extensions.parallel_search
    with pytest.raises(FrozenInstanceError):
        extensions.runtime_guidance = True
    assert GenerationExtensions.bind(object()).observe_many is None


@pytest.mark.parametrize('flag', ['replay_guided', 'runtime_guidance',
                                 'explores_contexts', 'parallel_search'])
def test_optional_capability_flags_are_exact_booleans(flag):
    from types import SimpleNamespace

    with pytest.raises(TypeError, match=flag):
        GenerationExtensions.bind(SimpleNamespace(**{flag: 1}))
    with pytest.raises(TypeError, match='native replay'):
        GenerationExtensions(explores_contexts=True)


def test_invalid_optional_port_is_not_silently_treated_as_no_capability():
    from types import SimpleNamespace

    with pytest.raises(TypeError, match='observe_many'):
        GenerationExtensions.bind(SimpleNamespace(observe_many=[]))


def test_orchestrators_use_the_explicit_ports_not_ad_hoc_capability_lookups():
    import ast
    from dataclasses import fields
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / 'src/behave/sim'
    ports = {field.name for field in fields(GenerationExtensions)}
    for name in ('generation.py', 'search_parallel.py', 'runtime_guidance.py'):
        for node in ast.walk(ast.parse((root / name).read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ('getattr', 'hasattr') and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)):
                assert node.args[1].value not in ports, (name, node.lineno, node.args[1].value)


def test_bound_refusal_readers_remain_live_and_keep_their_scope():
    class Provider:
        direction = None
        request = None
        def search_refusal(self):
            return self.direction
        def request_refusal(self, seed, request):
            assert seed == 'seed' and request == 'request'
            return self.request

    provider = Provider()
    ports = GenerationExtensions.bind(provider)
    assert ports.direction_refusal() is None
    assert ports.request_failure('seed', 'request') is None
    local = api.Refusal('local-context', 'only this request')
    provider.request = local
    assert ports.request_failure('seed', 'request') is local
    assert ports.direction_refusal() is None
    provider.direction = api.Refusal('direction', 'whole direction')
    assert ports.direction_refusal() is provider.direction
    provider.direction = None
    assert ports.direction_refusal() is None
    provider.request = 'not typed evidence'
    with pytest.raises(TypeError, match='exact Refusal'):
        ports.request_failure('seed', 'request')


def _frontend_catalogue():
    goals = (
        Goal(
            "g1", COVERAGE, family="decision", provider="python-structural",
            event="T", policy="test/v1"),
        Goal(
            "g2", COVERAGE, family="decision", provider="python-structural",
            event="F", policy="test/v1"),
    )
    return CatalogueManifest(
        goals,
        occurrences=((OccurrenceKey(
            "python-structural", ("process", 0), "true"), "g1"),))


def _stimulus(values, *, source="baseline", drain=1):
    return api.reset_then(
        contract(), [(value,) for value in values],
        controls={"out_ready": 1}, drain=drain, source=source)


def _spec(stimulus, queue_bound=4, drain=1):
    return GenerationSpec(
        len(stimulus.slots), queue_bound,
        contract().clocks[0].reset_hold_cycles, drain)


def _canonical(*stimuli):
    return tuple(sorted(stimuli, key=stimulus_bytes))


def _replace(seed, transaction, value, *, slots=None, source=None):
    queues = {name: [list(row) for row in rows] for name, rows in seed.queues}
    queues["in"][transaction][0] = value
    return Stimulus(
        queues={name: [tuple(row) for row in rows]
                for name, rows in queues.items()},
        slots=seed.slots if slots is None else slots,
        memory_policy=dict(seed.memory_policy),
        initial_state=dict(seed.initial_state),
        source=seed.source if source is None else source)


class _Provider:
    name = "fake/common"
    policy = "fake-common/v1:explicit-rank"
    targetable_goals = ("g1", "g2")

    def __init__(self, selections, answers):
        self.selections = selections
        self.answers = answers
        self.probes = []

    def select(self, seed, *, targets=None):
        selected = self.selections.get(tuple(dict(seed.queues)["in"]), ())
        if isinstance(selected, api.Refusal):
            return selected
        if type(selected) not in (tuple, list):
            return selected
        return ProbeSelection(selected, self.catalogue_manifest().digest())

    @staticmethod
    def catalogue_manifest():
        return _frontend_catalogue()

    @staticmethod
    def cache_telemetry():
        return GenerationCacheTelemetry()

    def probe(self, seed, request):
        self.probes.append((seed.fingerprint, request.identity))
        answer = self.answers[request.occurrence]
        if isinstance(answer, probing.Outcome):
            return probing.ProbeResult(
                answer, probing.Scope.EXACT_LOCAL_HIT,
                goal=request.targets[0] if request.targets else None)
        return probing.ProbeResult(
            probing.Outcome.SAT, probing.Scope.EXACT_LOCAL_HIT,
            goal=request.targets[0], candidate=answer(seed))


def test_scheduler_binds_optional_ports_once_not_once_per_query():
    from collections import Counter
    from dataclasses import fields

    names = {field.name for field in fields(GenerationExtensions)}
    class Provider(_Provider):
        def __getattribute__(self, name):
            if name in names:
                object.__getattribute__(self, 'reads')[name] += 1
            return super().__getattribute__(name)

    provider = Provider({((0,),): [_request('one')], ((1,),): [_request('one')]},
                        {'one': probing.Outcome.LOCAL_UNSAT})
    provider.reads = Counter()
    baseline = _canonical(_stimulus([0]), _stimulus([1]))
    result = generate(baseline, provider, contract=contract(),
                      generation_spec=_spec(baseline[0]), probe_budget=2)
    assert result.telemetry['probes'] == 2
    assert provider.reads == Counter({name: 1 for name in names})


def test_bound_diagnostics_read_final_state_after_session_cleanup():
    from contextlib import contextmanager

    class Provider(_Provider):
        opened = False
        closed = False
        @contextmanager
        def search_scope(self, *, probe_budget):
            self.opened = True
            try:
                yield
            finally:
                self.opened, self.closed = False, True
        def select(self, seed, *, targets=None):
            assert self.opened
            return super().select(seed, targets=targets)
        def execution_cost(self):
            assert self.closed and not self.opened
            return {'closed': self.closed, 'probes': len(self.probes)}

    provider = Provider({((0,),): [_request('one')]}, {'one': probing.Outcome.LOCAL_UNSAT})
    baseline = (_stimulus([0]),)
    result = generate(baseline, provider, contract=contract(),
                      generation_spec=_spec(baseline[0]), probe_budget=1)
    assert result.as_dict()['telemetry']['execution_cost'] == {'closed': True, 'probes': 1}


def _request(name, *, targets=("g1",), transaction=0, source_position=None,
             rank=0):
    if source_position is None:
        source_position = 3 + transaction
    return ProbeRequest(
        targets, occurrence=name,
        mutable_fields=[StimulusField(
            "in", transaction, 0, source_position)],
        rank=rank, catalogue_digest=_frontend_catalogue().digest())


def _telemetry(result):
    return dict(result.telemetry.items())


@pytest.mark.parametrize("static_fact", [False, True])
def test_late_static_facts_narrow_preselected_work_but_local_unsat_does_not(static_fact):
    seeds = _canonical(_stimulus([0]), _stimulus([1]))
    requests = (_request("discover", targets=("g1", "g2")),
                _request("false-only", rank=1),
                _request("mixed", targets=("g1", "g2"), rank=2))

    class Provider(_Provider):
        def query_targets(self, targets, *, seed):
            excluded = {"g1"} if static_fact and self.probes else set()
            return tuple(goal for goal in targets if goal not in excluded)

        def probe(self, seed, request):
            seen.append((seed.fingerprint, request.targets))
            return super().probe(seed, request)

    seen = []
    provider = Provider(
        {tuple(dict(seed.queues)["in"]): requests for seed in seeds},
        {request.occurrence: probing.Outcome.LOCAL_UNSAT for request in requests})
    result = generate(seeds, provider, contract=contract(),
                      generation_spec=_spec(seeds[0]), probe_budget=20)
    telemetry = result.as_dict()["telemetry"]
    assert telemetry["termination"] == "queue-empty"
    assert len(seen) == (4 if static_fact else 6)
    assert telemetry["requests_constant_false"] == (2 if static_fact else 0)
    assert telemetry["request_targets_constant_false"] == (5 if static_fact else 0)
    if static_fact:
        assert seen[0][1] == ("g1", "g2")
        assert all(targets == ("g2",) for _, targets in seen[1:])
    assert provider.targetable_goals == ("g1", "g2")
    assert set(provider.catalogue_manifest()) == {"g1", "g2"}
    assert result.generated == ()


def test_baseline_only_scheduler_does_not_require_an_analysis_domain():
    baseline = _canonical(_stimulus([0]))
    provider = _Provider({}, {})
    result = generate(baseline, provider, contract=contract(),
                      generation_spec=None, probe_budget=0)
    assert result.baseline == baseline and result.generated == ()
    assert result.generation_spec is None
    assert result.as_dict()["generation_spec"] is None
    assert not provider.probes


@pytest.mark.parametrize("value", [1, 256])
def test_local_search_without_analysis_domain_still_validates_candidate_inputs(value):
    baseline = _canonical(_stimulus([0]))
    provider = _Provider({((0,),): [_request("change")]},
                         {"change": lambda seed: _replace(seed, 0, value)})
    result = generate(baseline, provider, contract=contract(),
                      generation_spec=None, probe_budget=1)
    assert len(provider.probes) == 1
    if value == 1:
        assert len(result.generated) == 1
        assert dict(result.generated[0].queues)["in"] == ((1,),)
    else:
        assert result.generated == ()
        assert dict(result.telemetry["refusals"])["generation-candidate-outside-domain"] == 1


@pytest.mark.parametrize("domain", [False, {}, 1])
def test_optional_analysis_domain_does_not_accept_untyped_values(domain):
    with pytest.raises(Refused, match="generation-spec-not-frozen"):
        generate(_canonical(_stimulus([0])), _Provider({}, {}),
                 contract=contract(), generation_spec=domain, probe_budget=0)


@pytest.mark.parametrize("scope", ["request", "direction"])
def test_refusal_scope_stops_only_an_unavailable_direction(scope):
    from contextlib import contextmanager

    seeds = _canonical(_stimulus([0]), _stimulus([1]))
    refusal = api.Refusal("same-code", "scope comes from the provider, not this code")

    class Provider(_Provider):
        def __init__(self):
            super().__init__({}, {})
            self.unavailable = None
            self.selected = 0
            self.closed = False

        @contextmanager
        def search_scope(self, *, probe_budget):
            try:
                yield
            finally:
                self.closed = True

        def search_refusal(self):
            return self.unavailable

        def select(self, seed, *, targets=None):
            assert self.unavailable is None  # No admission after a fatal refusal.
            self.selected += 1
            return ProbeSelection([_request(str(i), rank=i) for i in range(3)],
                                  self.catalogue_manifest().digest())

        def probe(self, seed, request):
            self.probes.append(request)
            if len(self.probes) == 1:
                # Retained progress must survive a later context failure.
                return probing.ProbeResult(probing.Outcome.SAT,
                    probing.Scope.EXACT_LOCAL_HIT, goal="g1",
                    candidate=_replace(seed, 0, 2))
            if scope == "direction":
                self.unavailable = refusal
            raise Refused(refusal)

    provider = Provider()
    other = _Provider({}, {})
    other.name = "other/independent"
    portfolio = generate_independently(seeds, (provider, other), contract=contract(),
                                      generation_spec=_spec(seeds[0]), probe_budget=20)
    result, independent = portfolio.searches
    row = result.as_dict()["telemetry"]
    assert result.baseline == seeds and len(result.generated) == 1
    assert independent.baseline == seeds and independent.telemetry["termination"] == "queue-empty"
    assert provider.closed
    if scope == "direction":
        assert len(provider.probes) == 2 and provider.selected == 2
        assert row["termination"] == "provider-unavailable"
        assert row["provider_refusal"] == refusal.as_dict()
        assert row["requests_deferred"] == 2 and row["queue_remaining"] == 1
        assert row["worklist"]["suspended_at_stop"] == 1
    else:
        assert len(provider.probes) == 9 and provider.selected == 3
        assert row["termination"] == "queue-empty" and row["provider_refusal"] is None
    assert row["outcomes"]["sat"] == 1
    assert sum(row["outcomes"].values()) == 1  # Refusal is never an UNSAT answer.


def test_known_unavailable_direction_skips_seed_replay_and_reports_scope():
    from behave.sim.generation import UnavailableGenerationProvider

    seeds = _canonical(_stimulus([0]), _stimulus([1]))
    refusal = api.Refusal("layout-unavailable", "no shared representation")
    provider = UnavailableGenerationProvider("unavailable", "test/1", refusal,
                                             catalogue=_frontend_catalogue())
    result = generate(seeds, provider, contract=contract(),
                      generation_spec=_spec(seeds[0]), probe_budget=20)
    row = result.as_dict()["telemetry"]
    assert result.baseline == seeds and not result.generated
    assert row["catalogue_digest"] == _frontend_catalogue().digest()
    assert row["seeds_selected"] == row["probes"] == 0
    assert row["termination"] == "provider-unavailable" and row["queue_remaining"] == 2
    assert row["refusals"] == {refusal.kind: 1}


def test_initial_select_batch_preserves_the_canonical_fifo():
    left, right = _canonical(_stimulus([0]), _stimulus([1]))

    class Batched(_Provider):
        def __init__(self):
            super().__init__({}, {})
            self.batches = []

        def select_many(self, seeds):
            self.batches.append(tuple(seed.fingerprint for seed in seeds))
            digest = self.catalogue_manifest().digest()
            return tuple(ProbeSelection((), digest) for _seed in seeds)

        def select(self, _seed, *, targets=None):
            raise AssertionError("the baseline should use its one batch answer")

    provider = Batched()
    result = generate(
        (left, right), provider, contract=contract(),
        generation_spec=_spec(left), probe_budget=1)

    assert provider.batches == [(left.fingerprint, right.fingerprint)]
    assert result.baseline == (left, right)
    assert _telemetry(result)["seeds_selected"] == 2
    assert _telemetry(result)["catalogue_confirmations"] == 2


def test_prepared_replay_does_not_eagerly_select_unused_baseline_members():
    baseline = _canonical(_stimulus([0]), _stimulus([1]), _stimulus([2]))
    request = _request("a")
    choices = {tuple(dict(seed.queues)["in"]): [request] for seed in baseline}

    class Prepared(_Provider):
        def __init__(self):
            super().__init__(choices, {"a": probing.Outcome.LOCAL_UNSAT})
            self.prepared = []
            self.selected = []

        def prepare_seeds(self, seeds):
            self.prepared.append(tuple(seeds))

        def select_many(self, seeds):
            raise AssertionError("budgeted selection must stay lazy")

        def select(self, seed, *, targets=None):
            self.selected.append(seed)
            return super().select(seed)

    lazy = Prepared()
    args = {"contract": contract(), "generation_spec": _spec(baseline[0]), "probe_budget": 1}
    actual = generate(baseline, lazy, **args)
    ordinary = generate(baseline, _Provider(choices, {"a": probing.Outcome.LOCAL_UNSAT}), **args)
    assert lazy.prepared == [baseline]
    assert lazy.selected == [baseline[0]]
    assert actual.baseline == ordinary.baseline
    assert actual.generated == ordinary.generated
    assert _telemetry(actual)["answers"] == _telemetry(ordinary)["answers"]


def test_provider_sequence_order_cannot_change_the_budgeted_canonical_union():
    """Policy rank plus canonical identity, never incidental sequence order."""
    baseline = _stimulus([0])
    # Same explicit rank: the scheduler's canonical request identity breaks the tie.
    a = _request("a")
    b = _request("b")
    answers = {"a": lambda seed: _replace(seed, 0, 1),
               "b": lambda seed: _replace(seed, 0, 2)}
    left = generate(
        (baseline,), _Provider({((0,),): [b, a]}, answers),
        contract=contract(), generation_spec=_spec(baseline), probe_budget=1)
    right = generate(
        (baseline,), _Provider({((0,),): [a, b]}, answers),
        contract=contract(), generation_spec=_spec(baseline), probe_budget=1)

    assert [stimulus_bytes(s) for s in left.generated] == \
        [stimulus_bytes(s) for s in right.generated]
    assert [stimulus_bytes(s) for s in left.canonical_union] == \
        [stimulus_bytes(s) for s in right.canonical_union]
    assert _telemetry(left)["version"] == _telemetry(right)["version"] == 14
    assert _telemetry(left)["probes"] == _telemetry(right)["probes"] == 1
    assert _telemetry(left)["provider_policy"] == "fake-common/v1:explicit-rank"
    assert _telemetry(left)["provider_targets"] == ("g1", "g2")
    assert left.as_dict()["telemetry"]["context_cache"] == {
        "version": "generation-cache-telemetry/1",
        "counts": {"hit": 0, "miss": 0, "bypass": 0, "error": 0},
        "events": [],
    }


def test_invalid_optional_cache_telemetry_does_not_discard_the_pool():
    class WrongCacheTelemetry(_Provider):
        @staticmethod
        def cache_telemetry():
            return {"events": []}

    baseline = _stimulus([0])
    provider = WrongCacheTelemetry({((0,),): ()}, {})

    result = generate((baseline,), provider, contract=contract(),
                      generation_spec=_spec(baseline), probe_budget=0)
    assert result.canonical_union == (baseline,)
    assert result.telemetry["termination"] == "search-failed"
    assert result.telemetry["diagnostic_failure"]["kind"] == \
        "generation-provider-cache-telemetry-invalid"
    assert "context_cache" not in result.telemetry


def test_unordered_select_result_is_refused_instead_of_inventing_policy_order():
    baseline = _stimulus([0])
    request = _request("unordered")
    provider = _Provider(
        {((0,),): {request}},
        {"unordered": lambda seed: _replace(seed, 0, 1)})

    result = generate(
        (baseline,), provider, contract=contract(),
        generation_spec=_spec(baseline), probe_budget=1)

    telemetry = _telemetry(result)
    assert dict(telemetry["refusals"]) == {
        "generation-select-result-not-typed": 1}
    assert telemetry["selection_refusals"] == 1
    assert telemetry["probes"] == 0
    assert provider.probes == []
    assert result.generated == ()


def test_inconsistent_request_identity_is_refused_before_probe():
    baseline = _stimulus([0])
    request = _request("tampered-request")
    object.__setattr__(request, "_identity", "0" * 64)
    provider = _Provider(
        {((0,),): (request,)},
        {"tampered-request": lambda seed: _replace(seed, 0, 1)})

    result = generate(
        (baseline,), provider, contract=contract(),
        generation_spec=_spec(baseline), probe_budget=1)

    telemetry = _telemetry(result)
    assert dict(telemetry["refusals"]) == {
        "generation-request-identity-inconsistent": 1}
    assert telemetry["request_refusals"] == 1
    assert telemetry["probes"] == 0
    assert provider.probes == []


def test_budget_exhaustion_is_recorded_when_requests_remain_on_the_popped_seed():
    baseline = _stimulus([0])
    requests = (_request("first", rank=0), _request("deferred", rank=1))
    provider = _Provider(
        {((0,),): requests},
        {"first": probing.Outcome.UNKNOWN,
         "deferred": probing.Outcome.UNKNOWN})

    result = generate(
        (baseline,), provider, contract=contract(),
        generation_spec=_spec(baseline), probe_budget=1)

    telemetry = _telemetry(result)
    assert telemetry["queue_remaining"] == 0
    assert telemetry["requests_deferred"] == 1
    assert telemetry["termination"] == "probe-budget-exhausted"
    assert len(provider.probes) == 1


def test_every_unseen_sat_is_queued_and_one_request_may_target_many_goals():
    """Retention is independent of replay hits; the scheduler reports no credit."""
    baseline = _stimulus([0])
    request = _request("both", targets=("g2", "g1"))
    provider = _Provider(
        {((0,),): (request,), ((7,),): ()},
        {"both": lambda seed: _replace(seed, 0, 7)})
    result = generate(
        (baseline,), provider, contract=contract(),
        generation_spec=_spec(baseline), probe_budget=4)

    assert [dict(stimulus.queues)["in"] for stimulus in result.generated] == \
        [((7,),)]
    assert _telemetry(result)["seeds_selected"] == 2, \
        "the unseen candidate was pushed and later selected as a seed"
    row = dict(result.provenance[result.generated[0].fingerprint][0])
    assert tuple(row["targets"]) == ("g1", "g2")
    assert "hits" not in _telemetry(result) and "per_goal" not in _telemetry(result)


def test_local_unsat_unknown_and_unsupported_never_become_unreachable():
    baseline = _stimulus([0])
    requests = tuple(_request(outcome.value, rank=index)
                     for index, outcome in enumerate((
                         probing.Outcome.LOCAL_UNSAT,
                         probing.Outcome.UNKNOWN,
                         probing.Outcome.UNSUPPORTED)))
    provider = _Provider(
        {((0,),): requests},
        {outcome.value: outcome for outcome in (
            probing.Outcome.LOCAL_UNSAT,
            probing.Outcome.UNKNOWN,
            probing.Outcome.UNSUPPORTED)})
    result = generate(
        (baseline,), provider, contract=contract(),
        generation_spec=_spec(baseline), probe_budget=3)

    telemetry = _telemetry(result)
    outcomes = dict(telemetry["outcomes"])
    assert outcomes == {"constructing": 0, "sat": 0, "proposed": 0, "local-unsat": 1,
                        "unknown": 1, "unsupported": 1}
    answers = tuple(dict(row) for row in telemetry["answers"])
    assert [row["outcome"] for row in answers] == [
        "local-unsat", "unknown", "unsupported"]
    assert all(row["target_count"] == 1 for row in answers)
    assert all(tuple(telemetry["target_sets"][row["target_set"]]) == ("g1",)
               for row in answers)
    assert all(row["request"] and row["seed"] for row in answers)
    assert result.generated == ()
    assert not hasattr(probing.ProbeResult, "as_goal_status")
    assert {scope.value for scope in probing.Scope} == {
        "exact-local-hit", "path-prefix", "rtl-local-hit", "state-preparation"}


@pytest.mark.parametrize("goal_count", (1, 2, 3, 7, 48))
def test_query_evidence_retains_every_target_once_across_probe_answers(goal_count):
    goals = tuple(sorted("g" + str(index) for index in range(goal_count)))
    manifest = CatalogueManifest(tuple(Goal(
        goal, COVERAGE, family="decision", provider="python-structural",
        event="T", policy="test/v1") for goal in goals))

    class Provider(_Provider):
        targetable_goals = goals

        @staticmethod
        def catalogue_manifest():
            return manifest

    baseline = _stimulus([0])
    outcomes = (probing.Outcome.LOCAL_UNSAT, probing.Outcome.UNKNOWN,
                probing.Outcome.UNSUPPORTED)
    requests = tuple(ProbeRequest(
        goals, occurrence=outcome.value, rank=index,
        mutable_fields=[StimulusField("in", 0, 0, 3)],
        catalogue_digest=manifest.digest())
        for index, outcome in enumerate(outcomes))
    result = generate(
        (baseline,), Provider({((0,),): requests},
                             {outcome.value: outcome for outcome in outcomes}),
        contract=contract(), generation_spec=_spec(baseline), probe_budget=3)
    telemetry = json.loads(json.dumps(result.as_dict()))["telemetry"]
    assert telemetry["target_sets"] == [list(goals)]
    assert len(telemetry["answers"]) == 3
    for answer in telemetry["answers"]:
        assert answer["target_count"] == len(goals)
        assert telemetry["target_sets"][answer["target_set"]] == list(goals)
        assert "target_sample" not in answer


def test_generation_telemetry_preserves_empty_and_pair_shaped_sequences():
    telemetry = {
        "provider_policy": "test/v1", "answers": [], "target_sets": [],
        "refusals": {}, "pairs": [["a", "b"], ["a", "c"]],
        "nested": {"empty": [], "map": {}},
    }
    result = GenerationResult("test", (), (), {}, telemetry,
                              contract=contract().digest, generation_spec=None)
    assert result.as_dict()["telemetry"] == telemetry
    telemetry["pairs"][0][0] = "changed"
    detached = result.as_dict()["telemetry"]
    assert detached["pairs"][0] == ["a", "b"]
    detached["nested"]["empty"].append("changed")
    assert result.as_dict()["telemetry"]["nested"]["empty"] == []


def test_duplicate_candidate_merges_provenance_without_a_second_queue_member():
    baseline = _stimulus([0])
    requests = (_request("first", rank=0), _request("second", rank=1))
    provider = _Provider(
        {((0,),): requests, ((9,),): ()},
        {name: (lambda seed: _replace(seed, 0, 9))
         for name in ("first", "second")})
    result = generate(
        (baseline,), provider, contract=contract(),
        generation_spec=_spec(baseline), probe_budget=4)

    assert len(result.generated) == 1
    assert len(result.provenance[result.generated[0].fingerprint]) == 2
    telemetry = _telemetry(result)
    assert telemetry["candidates_retained"] == 1
    assert telemetry["candidates_duplicate"] == 1


def test_invalid_requests_and_candidates_are_typed_counted_refusals():
    seed = _stimulus([0, 1])
    # Later queue transaction mapped to an earlier source position: no Probe call.
    broken_map = ProbeRequest(
        ("g1",), occurrence="broken-map",
        mutable_fields=[StimulusField("in", 0, 0, 4),
                        StimulusField("in", 1, 0, 3)],
        catalogue_digest=_frontend_catalogue().digest())
    nonselected = _request("nonselected", transaction=0, rank=1)
    outside = _request("outside", transaction=0, rank=2)
    provenance_mutation = _request("source", transaction=0, rank=3)

    provider = _Provider(
        {((0,), (1,)): (outside, nonselected, provenance_mutation, broken_map)},
        {"nonselected": lambda stimulus: _replace(stimulus, 1, 8),
         # A shorter schedule is outside the frozen generation domain.
         "outside": lambda stimulus: _replace(
             stimulus, 0, 9, slots=stimulus.slots[:-1]),
         "source": lambda stimulus: _replace(
             stimulus, 0, 9, source="provider-authored")})
    result = generate(
        (seed,), provider, contract=contract(),
        generation_spec=_spec(seed, queue_bound=2), probe_budget=4)

    telemetry = _telemetry(result)
    refusals = dict(telemetry["refusals"])
    assert refusals["generation-request-source-map-nonmonotone"] == 1
    assert refusals["generation-candidate-outside-domain"] == 1
    assert refusals["generation-candidate-mutates-nonselected-field"] == 2
    assert telemetry["request_refusals"] == 1
    assert telemetry["candidate_refusals"] == 3
    assert len(provider.probes) == 3, "the malformed request never consumed Probe"
    assert result.generated == ()


def test_malformed_source_positions_are_refused_before_probe():
    seed = _stimulus([0])
    outside = ProbeRequest(
        ("g1",), occurrence="outside-source-position",
        mutable_fields=[StimulusField("in", 0, 0, len(seed.slots))], rank=0,
        catalogue_digest=_frontend_catalogue().digest())
    no_offer = ProbeRequest(
        ("g1",), occurrence="source-position-without-offer",
        mutable_fields=[StimulusField("in", 0, 0, len(seed.slots) - 1)], rank=1,
        catalogue_digest=_frontend_catalogue().digest())
    wrong_type = ProbeRequest(
        ("g1",), occurrence="source-position-wrong-type",
        mutable_fields=[StimulusField("in", "zero", 0, 3),
                        StimulusField("in", 0, 0, 3)], rank=2,
        catalogue_digest=_frontend_catalogue().digest())
    provider = _Provider(
        {((0,),): (outside, no_offer, wrong_type)}, {})

    result = generate(
        (seed,), provider, contract=contract(),
        generation_spec=_spec(seed), probe_budget=3)

    telemetry = _telemetry(result)
    assert dict(telemetry["refusals"]) == {
        "generation-request-source-map-invalid": 1,
        "generation-request-source-map-outside-schedule": 1,
        "generation-request-source-map-without-offer": 1,
    }
    assert telemetry["request_refusals"] == 3
    assert telemetry["probes"] == 0
    assert provider.probes == []


def test_same_baseline_initializes_wholly_independent_searches_and_budgets():
    first, second = _canonical(_stimulus([0]), _stimulus([3]))
    baseline = (first, second)
    empty = {tuple(dict(seed.queues)["in"]): () for seed in baseline}
    left = generate(
        baseline, _Provider(empty, {}), contract=contract(),
        generation_spec=_spec(first), probe_budget=0)
    right = generate(
        baseline, _Provider(empty, {}), contract=contract(),
        generation_spec=_spec(first), probe_budget=8)

    lt, rt = _telemetry(left), _telemetry(right)
    assert [stimulus_bytes(s) for s in left.baseline] == \
        [stimulus_bytes(s) for s in right.baseline]
    assert lt["baseline_digest"] == rt["baseline_digest"]
    assert lt["queue_remaining"] == 2 and rt["queue_remaining"] == 0
    assert lt["termination"] == "probe-budget-exhausted"
    assert rt["termination"] == "queue-empty"


def test_independent_searches_form_one_canonical_paper_stimulus_set():
    baseline = _stimulus([0])
    left = _Provider(
        {((0,),): (_request("left"),), ((1,),): ()},
        {"left": lambda seed: _replace(seed, 0, 1)})
    left.name, left.policy = "behavior", "behavior/v1"
    right = _Provider(
        {((0,),): (_request("right"),), ((2,),): ()},
        {"right": lambda seed: _replace(seed, 0, 2)})
    right.name, right.policy = "rtl", "rtl/v1"

    got = generate_independently(
        (baseline,), (left, right), contract=contract(),
        generation_spec=_spec(baseline), probe_budget=2)

    assert [dict(stimulus.queues)["in"] for stimulus in got.stimuli] == \
        [((0,),), ((1,),), ((2,),)]
    assert len(left.probes) == len(right.probes) == 1
    assert [search.provider for search in got.searches] == ["behavior", "rtl"]


def test_provider_order_cannot_change_xi_score_raw_records_or_secondary_pool():
    """Exercise the whole immutable handoff, not only the scheduler's set union."""

    from .test_api_path import Doubling, _ref

    baseline = _stimulus([0])

    def providers():
        left = _Provider(
            {((0,),): (_request("left"),), ((1,),): ()},
            {"left": lambda seed: _replace(seed, 0, 1)})
        left.name, left.policy = "behavior", "behavior/v1"
        right = _Provider(
            {((0,),): (_request("right"),), ((2,),): ()},
            {"right": lambda seed: _replace(seed, 0, 2)})
        right.name, right.policy = "rtl", "rtl/v1"
        return left, right

    task = api.admit(contract(), realization(), reference=_ref())
    left_provider, right_provider = providers()
    left = api.generate_stimuli(
        task, (baseline,), (left_provider, right_provider),
        frontend_catalogue=_frontend_catalogue(),
        generation_spec=_spec(baseline), probe_budget=2)
    left_evaluation = evaluate_generated_device(
        left, Doubling(), reference=_ref())
    left_pool = api.minimize_witnesses(left_evaluation)

    left_provider, right_provider = providers()
    right = api.generate_stimuli(
        task, (baseline,), (right_provider, left_provider),
        frontend_catalogue=_frontend_catalogue(),
        generation_spec=_spec(baseline), probe_budget=2)
    right_evaluation = evaluate_generated_device(
        right, Doubling(), reference=_ref())
    right_pool = api.minimize_witnesses(right_evaluation)

    assert left.as_dict() == right.as_dict()
    from .test_parallel_public_replay import _semantic

    assert _semantic(left_evaluation) == _semantic(right_evaluation)
    assert [result.trace.as_dict() for result in left_evaluation.results] == \
        [result.trace.as_dict() for result in right_evaluation.results]
    left_image, right_image = left_pool.as_dict(), right_pool.as_dict()
    assert left_image.pop("evaluation") == left_evaluation.fingerprint
    assert right_image.pop("evaluation") == right_evaluation.fingerprint
    assert left_image == right_image


def test_public_generation_path_uses_the_admitted_task_and_canonical_union():
    baseline = _stimulus([0])
    provider = _Provider(
        {((0,),): (_request("public"),), ((4,),): ()},
        {"public": lambda seed: _replace(seed, 0, 4)})
    provider.name, provider.policy = "behavior", "behavior/v1"
    task = api.admit(contract(), realization())

    got = api.generate_stimuli(
        task, (baseline,), (provider,), frontend_catalogue=_frontend_catalogue(),
        generation_spec=_spec(baseline),
        probe_budget=2)

    assert type(got) is api.GeneratedStimulusSet
    assert got.task is task
    assert got.portfolio.contract == task.contract.digest
    expected = api.evaluation_manifest(task, _frontend_catalogue())
    assert got.catalogue.digest() == expected.digest()
    assert got.catalogue.occurrences == _frontend_catalogue().occurrences
    assert [dict(stimulus.queues)["in"] for stimulus in got.stimuli] == \
        [((0,),), ((4,),)]
    with pytest.raises(api.Refused, match="generation-task-not-admitted"):
        api.generate_stimuli(
            task.contract, (baseline,), (provider,),
            frontend_catalogue=_frontend_catalogue(),
            generation_spec=_spec(baseline), probe_budget=2)


def test_generated_set_is_the_only_public_generation_to_evaluation_handoff():
    from behave.sim.core.frozen import freeze

    from .test_api_path import Doubling, _ref

    baseline = _stimulus([2])
    provider = _Provider({((2,),): ()}, {})
    provider.name, provider.policy = "behavior", "behavior/v1"
    task = api.admit(contract(), realization(), reference=_ref())
    generated = api.generate_stimuli(
        task, (baseline,), (provider,), frontend_catalogue=_frontend_catalogue(),
        generation_spec=_spec(baseline),
        probe_budget=1)

    evaluation = evaluate_generated_device(generated, Doubling(), reference=_ref())

    assert tuple(result.stimulus.fingerprint for result in evaluation.results) == \
        tuple(stimulus.fingerprint for stimulus in generated.stimuli)
    generation = dict(evaluation.telemetry)["generation"]
    assert generation == freeze(generated.portfolio.as_dict())
    assert evaluation.catalogue is generated.catalogue
    assert evaluation.as_dict()["catalogue_digest"] == generated.catalogue.digest()
    with pytest.raises(api.Refused, match="evaluation-generated-set-not-typed"):
        evaluate_generated_device(generated.portfolio, Doubling(), reference=_ref())
    with pytest.raises(
            api.Refused, match="evaluation-generation-telemetry-replaced"):
        evaluate_generated_device(
            generated, Doubling(), reference=_ref(),
            telemetry={"generation": {"forged": True}})

    with pytest.raises(TypeError, match="derived only by generate_stimuli"):
        api.GeneratedStimulusSet(task, generated.portfolio, generated.catalogue)


def test_generation_result_requires_real_provenance_for_every_new_member():
    baseline = _stimulus([0])
    generated = _stimulus([1])

    with pytest.raises(api.Refused, match="generation-result-provenance-missing"):
        GenerationResult(
            "forged", (baseline,), (generated,), {},
            {"provider_policy": "forged/v1"},
            contract=contract().digest, generation_spec=_spec(baseline))


def test_manifest_conflicts_and_unknown_generation_targets_refuse_before_select():
    baseline = _stimulus([0])
    task = api.admit(contract(), realization())

    monitor_goal = next(iter(Monitor(task.contract).goals().values()))
    conflicting = CatalogueManifest((Goal(
        monitor_goal.id, COVERAGE, provider="forged-frontend",
        policy="forged/v1"),))
    provider = _Provider({}, {})
    provider.name, provider.policy = "never-called", "never-called/v1"
    provider.targetable_goals = ()
    with pytest.raises(api.Refused, match="catalogue-conflicting-goal"):
        api.generate_stimuli(
            task, (baseline,), (provider,), frontend_catalogue=conflicting,
            generation_spec=_spec(baseline), probe_budget=1)
    assert provider.probes == []

    provider.targetable_goals = ("undeclared.goal",)
    with pytest.raises(
            api.Refused, match="generation-provider-target-outside-catalogue"):
        api.generate_stimuli(
            task, (baseline,), (provider,),
            frontend_catalogue=_frontend_catalogue(),
            generation_spec=_spec(baseline), probe_budget=1)
    assert provider.probes == []


def test_source_guidance_catalogue_does_not_replace_artifact_denominator():
    """B* -> A asks source goals while A keeps its own scored catalogue."""

    baseline = _stimulus([0])
    task = api.admit(contract(), realization())
    source_goal = Goal(
        "reference.choice:T", COVERAGE, family="decision",
        provider="python-structural", event="T", policy="reference/v1")
    source_catalogue = CatalogueManifest((source_goal,))
    request = ProbeRequest(
        (source_goal.id,), occurrence="reference:process:0",
        mutable_fields=(StimulusField("in", 0, 0, 3),), rank=0,
        catalogue_digest=source_catalogue.digest())

    class SourceProvider(_Provider):
        name = "reference-behavior->candidate-rtl"
        policy = "reference-behavior-guidance/v1"
        targetable_goals = (source_goal.id,)

        @staticmethod
        def catalogue_manifest():
            return source_catalogue

    provider = SourceProvider(
        {((0,),): (request,), ((7,),): ()},
        {request.occurrence: lambda seed: _replace(seed, 0, 7)})
    generated = api.generate_stimuli(
        task, (baseline,), (provider,),
        frontend_catalogue=_frontend_catalogue(),
        generation_spec=_spec(baseline), probe_budget=1)

    artifact_catalogue = api.evaluation_manifest(task, _frontend_catalogue())
    assert generated.catalogue == artifact_catalogue
    assert source_goal.id not in generated.catalogue
    assert len(generated.stimuli) == 2
    search = generated.searches[0]
    assert dict(search.telemetry)["catalogue_digest"] == source_catalogue.digest()
    assert dict(search.telemetry)["catalogue_confirmations"] == 1
    row = dict(next(iter(search.provenance.values()))[0])
    assert row["catalogue_digest"] == source_catalogue.digest()


def test_empty_selection_still_confirms_the_catalogue_actually_used():
    baseline = _stimulus([0])
    expected = _frontend_catalogue()
    other = CatalogueManifest((Goal(
        "other", COVERAGE, family="decision", provider="python-structural",
        event="T", policy="other/v1"),))

    class WrongEmpty(_Provider):
        def select(self, _seed, *, targets=None):
            return ProbeSelection((), other.digest())

    provider = WrongEmpty({}, {})
    assert provider.catalogue_manifest() == expected
    with pytest.raises(Refused, match="generation-selection-catalogue-mismatch"):
        generate(
            (baseline,), provider, contract=contract(),
            generation_spec=_spec(baseline), probe_budget=1)


@pytest.mark.parametrize("digest", ["", "0" * 63, "A" * 64, None])
def test_probe_request_requires_one_exact_lowercase_sha256_catalogue(digest):
    with pytest.raises(TypeError, match="lowercase SHA-256"):
        ProbeRequest(
            ("g1",), occurrence="source:0", mutable_fields=(), rank=0,
            catalogue_digest=digest)


def test_public_generation_requires_a_source_catalogue():
    """Missing provider declarations cannot silently mean an empty source."""

    class LegacyOptionalProvider:
        name = "legacy/optional"
        policy = "legacy/optional-v1"
        targetable_goals = ()

        @staticmethod
        def select(_seed):
            raise AssertionError("catalogue validation precedes Select")

        @staticmethod
        def probe(_seed, _request):
            raise AssertionError("catalogue validation precedes Probe")

    baseline = _stimulus([0])
    task = api.admit(contract(), realization())
    with pytest.raises(
            api.Refused, match="generation-provider-catalogue-missing"):
        api.generate_stimuli(
            task, (baseline,), (LegacyOptionalProvider(),),
            frontend_catalogue=_frontend_catalogue(),
            generation_spec=_spec(baseline), probe_budget=1)

def test_two_searches_finding_one_candidate_merge_both_provenance_rows():
    baseline = _stimulus([0])
    providers = []
    for name in ("behavior", "rtl"):
        provider = _Provider(
            {((0,),): (_request(name),), ((7,),): ()},
            {name: lambda seed: _replace(seed, 0, 7)})
        provider.name, provider.policy = name, f"{name}/v1"
        providers.append(provider)

    got = generate_independently(
        (baseline,), tuple(providers), contract=contract(),
        generation_spec=_spec(baseline), probe_budget=2)

    generated = next(stimulus for stimulus in got.stimuli
                     if dict(stimulus.queues)["in"] == ((7,),))
    rows = got.provenance[generated.fingerprint]
    assert {dict(row)["provider"] for row in rows} == {"behavior", "rtl"}


def test_generated_members_inherit_their_baseline_schedule_provenance():
    baseline = _stimulus([0])
    first = _request("first")
    second = _request("second")
    provider = _Provider(
        {((0,),): (first,), ((1,),): (second,), ((2,),): ()},
        {"first": lambda seed: _replace(seed, 0, 1),
         "second": lambda seed: _replace(seed, 0, 2)})
    portfolio = generate_independently(
        (baseline,), (provider,), contract=contract(),
        generation_spec=_spec(baseline), probe_budget=2)
    baseline_row = {
        "schedule": "recorded", "of": "provider-run", "digest": "abc",
        "slots": len(baseline.slots),
    }

    got = schedule_provenance_after_generation(
        portfolio, {baseline.fingerprint: [baseline_row]})

    assert set(got) == {stimulus.fingerprint for stimulus in portfolio.stimuli}
    assert all(rows == [baseline_row] for rows in got.values())


def test_search_union_refuses_a_different_baseline():
    first, second = _stimulus([0]), _stimulus([1])
    spec = _spec(first)
    left = GenerationResult(
        "left", (first,), (), {}, {"provider_policy": "left/v1"},
        contract=contract().digest, generation_spec=spec)
    right = GenerationResult(
        "right", (second,), (), {}, {"provider_policy": "right/v1"},
        contract=contract().digest, generation_spec=spec)

    with pytest.raises(api.Refused, match="same canonical baseline"):
        combine_generation((left, right))


def test_search_union_refuses_a_different_contract_or_generation_spec():
    baseline = _stimulus([0])
    spec = _spec(baseline)
    left = GenerationResult(
        "left", (baseline,), (), {}, {"provider_policy": "left/v1"},
        contract=contract().digest, generation_spec=spec)
    other_contract = GenerationResult(
        "right", (baseline,), (), {}, {"provider_policy": "right/v1"},
        contract="another-contract", generation_spec=spec)
    with pytest.raises(api.Refused, match="generation-search-contract-mismatch"):
        combine_generation((left, other_contract))

    other_spec = GenerationSpec(
        spec.horizon + 1, spec.queue_bound, spec.reset_hold_cycles, spec.drain)
    other_policy = GenerationResult(
        "right", (baseline,), (), {}, {"provider_policy": "right/v1"},
        contract=contract().digest, generation_spec=other_spec)
    with pytest.raises(api.Refused, match="generation-search-spec-mismatch"):
        combine_generation((left, other_policy))

    other_baseline_policy = GenerationResult(
        "right", (baseline,), (), {}, {"provider_policy": "right/v1"},
        contract=contract().digest, generation_spec=spec,
        baseline_policy="generation-baseline/other")
    with pytest.raises(
            api.Refused, match="generation-search-baseline-policy-mismatch"):
        combine_generation((left, other_baseline_policy))


def test_result_report_is_json_shaped_not_frozen_pair_internals():
    baseline = _stimulus([0])
    request = _request("json")
    provider = _Provider(
        {((0,),): (request,), ((2,),): ()},
        {"json": lambda seed: _replace(seed, 0, 2)})
    result = generate(
        (baseline,), provider, contract=contract(),
        generation_spec=_spec(baseline), probe_budget=2)

    report = result.as_dict()
    row = report["provenance"][result.generated[0].fingerprint][0]
    assert isinstance(row, dict)
    assert isinstance(row["mutable_fields"][0], dict)
    assert isinstance(report["telemetry"]["outcomes"], dict)
    json.dumps(report, sort_keys=True)


def test_inconsistent_display_digest_is_refused_before_provenance_is_merged():
    baseline = _stimulus([0])
    collision = _replace(baseline, 0, 2)
    # Exercise the collision guard independently of SHA-256's assumed strength.
    object.__setattr__(collision, "_fingerprint", baseline.fingerprint)
    request = _request("collision")
    provider = _Provider(
        {((0,),): (request,)}, {"collision": lambda _seed: collision})

    result = generate(
        (baseline,), provider, contract=contract(),
        generation_spec=_spec(baseline), probe_budget=1)

    telemetry = _telemetry(result)
    assert dict(telemetry["refusals"]) == {
        "generation-candidate-fingerprint-inconsistent": 1}
    assert telemetry["candidate_refusals"] == 1
    assert baseline.fingerprint not in result.provenance
    assert result.generated == ()


def test_behavior_exact_adapter_uses_accepted_positions_and_preserves_schedule():
    from behave.sim.behavior import instance as behavior_instance
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
    from behave.sim.engines.behavior.generation import (
        AcceptedTraceOccurrences,
        BehaviorExactProvider,
    )

    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.seen = 0\n"
              "    def process(self, a):\n"
              "        if a == 7:\n"
              "            self.seen = 1\n"
              "        return {'y': self.seen}\n")
    schema = TransactionSchema(
        [Endpoint("a", "input", 8), Endpoint("y", "output", 8)],
        source="test", authority=Authority.TASK,
        reading=Reading.ELABORATED)
    instance = behavior_instance.lower(source, schema, bound=2)
    goal = next(goal for goal in sorted(instance.goals()) if goal.endswith(":T"))

    plain = _stimulus([0, 1])
    # Queue row 0 is offered at 3 and accepted at 4 without a new offer.
    # Its origin remains 3; the next queue row is first offered at 5.
    slots = plain.slots[:4] + (EventSlot(controls=dict(plain.slots[3].controls)),) + plain.slots[4:]
    seed = Stimulus(queues=dict(plain.queues), slots=slots, source="stalled")
    records = []
    for slot in range(len(seed.slots)):
        if slot == 3:
            accepted = {"in": None}
        elif slot == 4:
            accepted = {"in": (0,)}
        elif slot == 5:
            accepted = {"in": (1,)}
        else:
            accepted = {}
        records.append(SlotRecord(accepted=accepted))
    trace = AuditedTrace(
        seed, records=records, artifact="artifact-A", domain="domain-A")
    source_positions = AcceptedTraceOccurrences(
        instance.proc, contract(), lambda offered: trace,
        replay_artifact="artifact-A", replay_domain="domain-A")
    selected = source_positions(seed)
    assert [item.rank for item in selected] == [4, 5]
    assert [dict(item.bindings)["a"].source_position for item in selected] == [3, 5]
    from behave.sim.behavior.task_boundary import task_signature_boundary
    from behave.sim.engines.behavior.generation_job import _occurrences

    worker_selected = _occurrences(
        instance, task_signature_boundary(contract(), instance.proc.signature),
        seed, trace, contract())
    assert [item["rank"] for item in worker_selected] == [4, 5]
    assert [dict(item["bindings"])["a"].source_position
            for item in worker_selected] == [3, 5]
    provider = BehaviorExactProvider(
        instance, source_positions, targetable_goals=(goal,), timeout_ms=20_000,
        provider_id="B-star->artifact-A")

    result = generate(
        (seed,), provider, contract=contract(),
        generation_spec=GenerationSpec(
            len(seed.slots), 2, contract().clocks[0].reset_hold_cycles, 1),
        probe_budget=1)

    assert len(result.generated) == 1
    candidate = result.generated[0]
    assert dict(candidate.queues)["in"][0] == (7,)
    assert candidate.slots == seed.slots, "generation changed the recorded schedule"
    row = dict(result.provenance[candidate.fingerprint][0])
    mutable = dict(row["mutable_fields"][0])
    assert mutable["transaction"] == 0
    assert mutable["source_position"] == 3, \
        "the pending queue field originates before its accepting slot"

    wrong_artifact = AuditedTrace(
        seed, records=records, artifact="artifact-B", domain="domain-A")
    refused = AcceptedTraceOccurrences(
        instance.proc, contract(), lambda _seed: wrong_artifact,
        replay_artifact="artifact-A", replay_domain="domain-A")(seed)
    assert refused.kind == "behavior-generation-source-trace-wrong-artifact"


def test_behavior_adapter_uses_declared_field_bindings_not_field_order():
    from behave.sim.behavior import instance as behavior_instance
    from behave.sim.core.schema import (
        Authority,
        Reading,
        TransactionSchema,
    )
    from behave.sim.core.schema import Endpoint as SchemaEndpoint
    from behave.sim.engines.behavior.generation import (
        AcceptedTraceOccurrences,
        BehaviorExactProvider,
    )

    u8 = api.FiniteType(8)
    task = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("logical_b", u8, "b"),
                 api.Field("logical_a", u8, "a")],
                api.ready_valid("in", "in_valid", "in_ready"), clock="clk"),
            api.Endpoint(
                "out", api.Direction.OUT, [api.Field("y", u8, "y")],
                api.ready_valid("out", "out_valid", "out_ready"), clock="clk"),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")], source="test")
    source = ("class M:\n"
              "    def reset(self):\n"
              "        pass\n"
              "    def process(self, a, b):\n"
              "        if a == 7:\n"
              "            return {'y': b}\n"
              "        return {'y': 0}\n")
    schema = TransactionSchema(
        [SchemaEndpoint("a", "input", 8), SchemaEndpoint("b", "input", 8),
         SchemaEndpoint("y", "output", 8)],
        source="test", authority=Authority.TASK, reading=Reading.ELABORATED)
    instance = behavior_instance.lower(source, schema, bound=1)
    goal = next(goal for goal in sorted(instance.goals()) if goal.endswith(":T"))
    seed = api.reset_then(
        task, [(0, 0)], controls={"out_ready": 1}, drain=1, source="baseline")
    records = [
        SlotRecord(accepted={"in": (0, 0)})
        if "in" in slot.offers else SlotRecord()
        for slot in seed.slots]
    trace = AuditedTrace(
        seed, records=records, artifact="artifact-A", domain="domain-A")
    occurrences = AcceptedTraceOccurrences(
        instance.proc, task, lambda _seed: trace,
        replay_artifact="artifact-A", replay_domain="domain-A")
    selected = occurrences(seed)

    bindings = dict(selected[0].bindings)
    assert bindings["a"].field == 1
    assert bindings["b"].field == 0

    provider = BehaviorExactProvider(
        instance, occurrences, targetable_goals=(goal,), timeout_ms=20_000,
        provider_id="B-star->artifact-A")
    result = generate(
        (seed,), provider, contract=task,
        generation_spec=GenerationSpec(
            len(seed.slots), 1, task.clocks[0].reset_hold_cycles, 1),
        probe_budget=1)
    assert dict(result.generated[0].queues)["in"][0][1] == 7

    unknown = BehaviorExactProvider(
        instance, occurrences, targetable_goals=("not-in-catalogue",),
        provider_id="B-star->artifact-A")
    assert unknown.select(seed).kind == \
        "behavior-generation-target-outside-catalogue"

    impossible_goal = "test:declared-but-unencoded"
    impossible_proc = instance.proc.derived(
        impossible={**instance.proc.impossible,
                    impossible_goal: "test-declared impossible"})
    with pytest.raises(
            api.Refused, match="behavior-catalogue-goal-undeclared"):
        behavior_instance.BehaviorInstance(
            impossible_proc, instance.env, source=instance.source,
            file=instance.file, schema=instance.schema, bound=instance.bound)


def test_behavior_occurrence_state_resets_at_every_reset_schedule_slot():
    from behave.sim.behavior import instance as behavior_instance
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
    from behave.sim.engines.behavior.generation import AcceptedTraceOccurrences

    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.seen = 0\n"
              "    def process(self, a):\n"
              "        if a == 1:\n"
              "            self.seen = 1\n"
              "        return {'y': self.seen}\n")
    schema = TransactionSchema(
        [Endpoint("a", "input", 8), Endpoint("y", "output", 8)],
        source="test", authority=Authority.TASK, reading=Reading.ELABORATED)
    instance = behavior_instance.lower(source, schema, bound=2)
    plain = _stimulus([1, 0])
    slots = plain.slots[:4] + (EventSlot(reset=True),) + plain.slots[4:]
    seed = Stimulus(queues=dict(plain.queues), slots=slots, source="mid-reset")
    records = [SlotRecord() for _slot in seed.slots]
    records[3] = SlotRecord(accepted={"in": (1,)})
    records[5] = SlotRecord(accepted={"in": (0,)})
    trace = AuditedTrace(
        seed, records=records, artifact="artifact-A", domain="domain-A")

    occurrences = AcceptedTraceOccurrences(
        instance.proc, contract(), lambda _seed: trace,
        replay_artifact="artifact-A", replay_domain="domain-A")(seed)

    assert len(occurrences) == 2
    assert [dict(item.state)["seen"] for item in occurrences] == [0, 0]
    assert [dict(item.bindings)["a"].source_position
            for item in occurrences] == [3, 5]

    reset_free = Stimulus(
        queues={"in": [(0,)]}, slots=[EventSlot(offers=("in",))],
        source="reset-free")
    reset_free_records = [SlotRecord(accepted={"in": (0,)})]
    empty_initial = AuditedTrace(
        reset_free, records=reset_free_records,
        artifact="artifact-A", domain="domain-A")
    service = AcceptedTraceOccurrences(
        instance.proc, contract(), lambda _seed: empty_initial,
        replay_artifact="artifact-A", replay_domain="domain-A")
    assert dict(service(reset_free)[0].state)["seen"] == 0

    unmapped_initial = AuditedTrace(
        reset_free, initial_state={"opaque-artifact-state": 1},
        records=reset_free_records, artifact="artifact-A", domain="domain-A")
    refused = AcceptedTraceOccurrences(
        instance.proc, contract(), lambda _seed: unmapped_initial,
        replay_artifact="artifact-A", replay_domain="domain-A")(reset_free)
    assert refused.kind == "behavior-generation-initial-state-unmapped"

    ticked = Stimulus(
        queues={"in": [(0,)]},
        slots=[EventSlot(ticks=("clk",)), EventSlot(offers=("in",))],
        source="idle-tick")
    ticked_trace = AuditedTrace(
        ticked, records=[SlotRecord(), SlotRecord(accepted={"in": (0,)})],
        artifact="artifact-A", domain="domain-A")
    refused = AcceptedTraceOccurrences(
        instance.proc, contract(), lambda _seed: ticked_trace,
        replay_artifact="artifact-A", replay_domain="domain-A")(ticked)
    assert refused.kind == "behavior-generation-idle-tick-capability"
