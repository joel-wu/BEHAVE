"""Skip equal, already proved local questions, never unhit goal declarations."""

import copy
from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.engines import probing
from behave.sim.engines.behavior import generation_job as jobs
from behave.sim.generation import ProbeSelection, generate
from behave.sim.generation_cache import GenerationCacheTelemetry
from behave.sim.generation_feedback import ReplayFeedback
from behave.sim.generation_protocol import QueryPlan, QueryTemplate

from .test_behavior_local_unsat_reuse import _key_fixture, _selection, _trace
from .test_generation_scheduler import (
    _canonical,
    _Provider,
    _request,
    _stimulus,
    contract,
)

pytestmark = pytest.mark.solver


def public_selection(count=100, **kwargs):
    built, selected = _selection(count, **kwargs)
    # The worker-only fixture omits a physical ready control. Public scheduling
    # requires the complete stimulus, including that declared environment pin.
    seed = api.Stimulus(dict(selected.seed.queues), [api.EventSlot(
        reset=slot.reset, offers=slot.offers, controls={"result_ready": 1, "rst": int(slot.reset)})
        for slot in selected.seed.slots])
    assert not seed.faults_against(built[0])
    return built, jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(),
                                    list(selected.targets))


def _exact(selected):
    """The exact local questions, one per recorded occurrence."""
    return tuple(item for item in selected.requests.templates
                 if item.occurrence in selected.occurrences)


class SourceProvider:
    name = "source-equivalence-test"
    policy = "source-equivalence-test/1"
    replay_guided = True

    def __init__(self, built, *, equivalent=True):
        self.built, self.equivalent = built, equivalent
        self.targetable_goals = tuple(sorted(built[6]))
        self.selected, self.probes = {}, []

    def catalogue_manifest(self):
        return self.built[5]

    def cache_telemetry(self):
        return GenerationCacheTelemetry()

    def select(self, seed, *, targets=None):
        selected = jobs.SelectedTrace(self.built, seed.as_dict(), _trace(seed).as_dict(),
                                      sorted(targets))
        self.selected[seed.fingerprint] = selected
        wire = selected.as_dict()
        # Only the exact local questions. Relaxed proposals and path/context
        # queries share no UNSAT answer; they explore other seeds instead.
        wire["requests"] = selected.requests.retaining(_exact(selected)).as_dict()
        if not self.equivalent:
            wire["requests"]["equivalents"] = []  # Select's groups travel in the plan.
        return jobs.decode_selection_reply(wire)

    def probe(self, seed, request):
        self.probes.append(request)
        wire = self.selected[seed.fingerprint].probe(request.as_dict(), 20_000)
        return jobs.decode_probe_reply(wire, expected_catalogue_digest=self.built[5].digest())


def test_native_source_equivalence_removes_transport_work_not_goals():
    built, selected = public_selection()
    # Select still exposes all100 physical locations, even before any proof.
    exact = tuple(item.identity for item in _exact(selected))
    assert len(exact) == 100
    assert exact in selected.equivalent_requests
    results, providers = [], []
    for equivalent in (False, True):
        provider = SourceProvider(built, equivalent=equivalent)
        provider.targetable_goals = selected.targets
        results.append(generate((selected.seed,), provider, contract=built[0],
                                generation_spec=built[1], probe_budget=None))
        providers.append(provider)
    before, after = results
    assert [len(p.probes) for p in providers] == [100, 1]
    assert before.generated == after.generated == ()
    for field in ("coverage", "provider_targets", "catalogue_digest", "termination"):
        assert before.telemetry[field] == after.telemetry[field]
    assert after.telemetry["requests_selected"] == 100
    assert after.telemetry["requests_equivalent"] == 99
    wire = after.as_dict()["telemetry"]
    assert wire["outcomes"]["local-unsat"] == 1
    assert len(wire["answers"]) == 1
    proof, = wire["local_unsat_reuse"]
    assert proof["proof_request"] == providers[1].probes[0].identity
    assert set(proof["requests"]) == set(exact) - {proof["proof_request"]}
    assert wire["coverage"]["remaining"]  # Not global unreachability or goal retirement.


@pytest.mark.parametrize("stateful,history_goal", [(True, False), (False, True)])
def test_changed_context_still_searches_and_native_confirms_new_hit(stateful, history_goal):
    built, selected = public_selection(2 if stateful else 3, stateful=stateful, history_goal=history_goal)
    results, providers = [], []
    for equivalent in (False, True):
        provider = SourceProvider(built, equivalent=equivalent)
        provider.targetable_goals = selected.targets
        results.append(generate((selected.seed,), provider, contract=built[0],
                                generation_spec=built[1], probe_budget=10))
        providers.append(provider)
    before, after = results
    assert len(before.generated) == len(after.generated) == 1
    assert [r.identity for r in providers[0].probes] == [r.identity for r in providers[1].probes]
    # The history goal leaves input a free. Z3 may choose different legal a's;
    # compare the query obligations and independently source-confirmed target,
    # not an arbitrary model assignment or its resulting record fingerprint.
    for result in results:
        assert result.telemetry["termination"] == "goals-covered"
        assert not result.telemetry["coverage"]["remaining"]
        new = set(result.telemetry["coverage"]["hits"]) - set(selected.replay.hits)
        assert new.intersection(selected.targets)


class GroupedProvider(_Provider):
    def __init__(self, answers, *, scope=probing.Scope.EXACT_LOCAL_HIT):
        super().__init__({}, {})
        self.responses, self.scope = answers, scope

    def select(self, seed, *, targets=None):
        requests = tuple(_request(str(i), transaction=i, rank=i) for i in range(3))
        return ProbeSelection(requests, self.catalogue_manifest().digest(),
                              equivalent_requests=[tuple(r.identity for r in requests)])

    def probe(self, seed, request):
        self.probes.append((seed.fingerprint, request.identity))
        outcome = self.responses[int(request.occurrence)]
        return probing.ProbeResult(outcome, self.scope, candidate=(
            seed if outcome is probing.Outcome.SAT else None))


@pytest.mark.parametrize("outcome", [probing.Outcome.UNKNOWN, probing.Outcome.UNSUPPORTED,
                                    probing.Outcome.SAT])
def test_nonproof_answers_keep_each_physical_attempt(outcome):
    provider = GroupedProvider([outcome] * 3)
    result = generate((_stimulus([0, 0, 0]),), provider, contract=contract(),
                      generation_spec=None, probe_budget=None)
    assert len(provider.probes) == 3
    assert result.telemetry["requests_equivalent"] == 0
    assert not result.telemetry["local_unsat_reuse"]


def test_unknown_can_finish_later_then_skip_equal_remaining_work():
    provider = GroupedProvider([probing.Outcome.UNKNOWN, probing.Outcome.LOCAL_UNSAT,
                                probing.Outcome.UNKNOWN])
    result = generate((_stimulus([0, 0, 0]),), provider, contract=contract(),
                      generation_spec=None, probe_budget=None)
    assert len(provider.probes) == 2
    assert result.telemetry["requests_equivalent"] == 1
    assert result.as_dict()["telemetry"]["local_unsat_reuse"][0]["proof_request"] == provider.probes[1][1]


def test_equivalent_skip_still_obeys_search_cancellation(monkeypatch):
    from behave.sim import generation
    from behave.sim.search_policy import SearchStopped

    provider = GroupedProvider([probing.Outcome.LOCAL_UNSAT] * 3)

    def check():
        if provider.probes:
            raise SearchStopped("time-budget-exhausted")

    monkeypatch.setattr(generation, "check_search_work", check)
    seed = _stimulus([0, 0, 0])
    result = generate((seed,), provider, contract=contract(), generation_spec=None, probe_budget=None)
    assert len(provider.probes) == 1 and result.telemetry["requests_equivalent"] == 0
    assert result.baseline == (seed,) and result.generated == ()
    assert result.telemetry["termination"] == "time-budget-exhausted"


@pytest.mark.parametrize("scope", [probing.Scope.PATH_PREFIX, probing.Scope.RTL_LOCAL_HIT])
def test_other_local_scopes_cannot_borrow_exact_one_call_equivalence(scope):
    provider = GroupedProvider([probing.Outcome.LOCAL_UNSAT] * 3, scope=scope)
    result = generate((_stimulus([0, 0, 0]),), provider, contract=contract(),
                      generation_spec=None, probe_budget=None)
    assert len(provider.probes) == 3 and result.telemetry["requests_equivalent"] == 0


def test_no_proof_crosses_seed_selection():
    provider = GroupedProvider([probing.Outcome.LOCAL_UNSAT] * 3)
    result = generate(_canonical(_stimulus([0, 0, 0]), _stimulus([1, 1, 1])), provider,
                      contract=contract(), generation_spec=None, probe_budget=None)
    assert len(provider.probes) == 2
    assert result.telemetry["requests_equivalent"] == 4
    assert len(result.telemetry["local_unsat_reuse"]) == 2


def test_scheduler_restricts_targets_before_reusing_the_local_proof():
    class ResidualProvider(GroupedProvider):
        replay_guided = True

        def select(self, seed, *, targets=None):
            requests = tuple(_request(str(i), targets=("g1", "g2"), transaction=i, rank=i)
                             for i in range(3))
            return ProbeSelection(requests, self.catalogue_manifest().digest(),
                replay=ReplayFeedback(seed.fingerprint, self.catalogue_manifest().digest(),
                                      seed.fingerprint, ("g1",)),
                equivalent_requests=[tuple(r.identity for r in requests)])

        def probe(self, seed, request):
            assert request.targets == ("g2",)
            return super().probe(seed, request)

    provider = ResidualProvider([probing.Outcome.LOCAL_UNSAT] * 3)
    result = generate((_stimulus([0, 0, 0]),), provider, contract=contract(),
                      generation_spec=None, probe_budget=None)
    assert len(provider.probes) == 1 and result.telemetry["requests_equivalent"] == 2
    assert result.as_dict()["telemetry"]["coverage"]["remaining"] == ["g2"]


@pytest.mark.parametrize("component", ["state", "typed-state", "goals", "history", "typed-history", "domain"])
def test_select_equivalence_uses_the_same_full_context_as_local_solver(monkeypatch, component):
    def plan(*requests):
        return QueryPlan(tuple(QueryTemplate(r.targets, r.occurrence, r.mutable_fields, r.rank,
                                             catalogue_digest=r.catalogue_digest)
                               for r in requests), requests[0].catalogue_digest)

    selected, first, _request_stub, _calls = _key_fixture(monkeypatch)
    # Selections are query plans grouped by rank; occurrences carry their ids.
    selected._setup_proposals, selected.prefixes = {}, {}
    second = copy.deepcopy(first)
    first["id"], second["id"] = "a", "b"
    requests = (_request("a", targets=("observed",), transaction=0, rank=0),
                _request("b", targets=("observed",), transaction=1, rank=1))
    selected.requests = plan(*requests)
    selected.occurrences = {"a": first, "b": second}
    assert selected._equivalent_requests() == ((0, 1),)
    if component == "state":
        second["state"]["array"][0][0] = 9
    elif component == "typed-state":
        second["state"]["array"][0][0] = True
    elif component == "goals":
        selected.requests = plan(requests[0], _request(
            "b", targets=("observed", "extra"), transaction=1, rank=1))
    elif component == "history":
        second["previous"]["input"] = (3,)
    elif component == "typed-history":
        second["previous"]["input"] = (True,)
    else:
        second["rank"] += 1
        selected.input_domain = SimpleNamespace(context_key=lambda rank: (rank,))
    assert not selected._equivalent_requests()


@pytest.mark.parametrize("groups", [lambda a, b: [[a]], lambda a, b: [[a, a]],
    lambda a, b: [[a, b], [a, b]], lambda a, b: [[a, "foreign"]]])
def test_boundary_rejects_malformed_equivalence(groups):
    requests = (_request("a"), _request("b"))
    with pytest.raises(ValueError):
        ProbeSelection(requests, requests[0].catalogue_digest,
                       equivalent_requests=groups(*(r.identity for r in requests)))


def test_boundary_rejects_different_target_sets_and_is_immutable():
    a, b = _request("a"), _request("b", targets=("g2",))
    with pytest.raises(ValueError, match="same original target"):
        ProbeSelection((a, b), a.catalogue_digest, equivalent_requests=[(a.identity, b.identity)])
    b = _request("b")
    group = [a.identity, b.identity]
    selected = ProbeSelection((a, b), a.catalogue_digest, equivalent_requests=[group])
    group.clear()
    assert selected.equivalent_requests == ((a.identity, b.identity),)
    with pytest.raises(AttributeError):
        selected.equivalent_requests = ()


def test_atomic_selection_does_not_claim_ordinary_behavior_equivalence():
    from behave.sim.engines.behavior.atomic_generation import AtomicGenerationSelection

    selected = AtomicGenerationSelection.__new__(AtomicGenerationSelection)
    assert selected._equivalent_requests() == ()
