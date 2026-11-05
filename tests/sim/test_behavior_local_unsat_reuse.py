"""Exact negative questions keep state/domain scope; standalone jobs stay independent."""

import copy
from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.core.record_value import thaw_record_value
from behave.sim.engines import probing
from behave.sim.engines.behavior import generation as parent
from behave.sim.engines.behavior import generation_job as jobs
from tests.support.reference import load

from .test_behavior_generation_provider import SOURCE, _contract

pytestmark = pytest.mark.solver


def _trace(seed):
    queues = dict(seed.queues)
    return api.AuditedTrace(seed, records=[api.SlotRecord()] + [
        api.SlotRecord(accepted={name: values[i] for name, values in queues.items()})
        for i in range(len(queues["left"]))], artifact="target-a", domain="test-domain")


def _selection(count=100, *, stateful=False, history_goal=False):
    # Equal integers cannot sum to an odd integer: not a literal-false node.
    source = SOURCE.replace(b"a < b", b"a == b and a + b == 5").replace(
        b"y = a - b", b"y = b")
    if stateful:
        source = source.replace(b"        pass", b"        self.count = 1").replace(
            b"a + b == 5", b"a + b == self.count").replace(
            b'        return {"y": y}',
            b'        self.count = (self.count + 1) & 255\n        return {"y": y}')
    reference = load({"behavior.py": source}, config={"class": "M"})
    contract = _contract()
    if history_goal:
        contract = api.TaskContract(contract.endpoints, contract.clocks, observation_goals=(
            api.TransactionGoal("pair", "result", api.GoalCondition("and",
                api.GoalCondition("eq", api.GoalField("result_data", previous=True), 5),
                api.GoalCondition("eq", api.GoalField("result_data"), 9))),))
    payload = {"source": thaw_record_value(parent._authenticated_source_image(reference)),
               "contract": contract.as_dict(), "target_artifact": "target-a",
               "generation_spec": api.GenerationSpec(count + 1, count, 1, 0, 0).as_dict()}
    built = jobs._build(payload)
    targets = (["observation:pair"] if history_goal else
               [goal for goal in built[6] if built[5][goal].family == "decision"])
    right = [(3,), (5,), (3,)] if history_goal else [(3,)] * count
    seed = api.Stimulus({"left": [(2,)] * count, "right": right},
                        [api.EventSlot(reset=True)] + [
                            api.EventSlot(offers=("left", "right"))] * count)
    return built, jobs.SelectedTrace(built, seed.as_dict(), _trace(seed).as_dict(), targets)


def _exact(selected):
    """The exact local questions in occurrence order.

    Path proposals and contexts are scheduled around them, and a reached state
    may be scheduled before an earlier occurrence.
    """
    return tuple(sorted((r for r in selected.requests if r.occurrence in selected.occurrences),
                        key=lambda r: (selected.occurrences[r.occurrence]["rank"], r.rank)))


def _count_queries(monkeypatch):
    original, calls = jobs._local_hit, []

    def query(*args, **kwargs):
        calls.append(args[1]["id"])
        return original(*args, **kwargs)

    monkeypatch.setattr(jobs, "_local_hit", query)
    return calls


def test_one_hundred_equivalent_occurrences_keep_catalogue_and_local_scope(monkeypatch):
    built, selected = _selection()
    calls = _count_queries(monkeypatch)
    before = selected.as_dict()
    requests = _exact(selected)
    assert len(requests) == 100
    answers = [selected.probe(request.as_dict(), 20_000) for request in requests]
    assert len(calls) == 1
    assert {answer["outcome"] for answer in answers} == {"local-unsat"}
    assert {answer["scope"] for answer in answers} == {"exact-local-hit"}
    assert all(answer["candidate"] is None and answer["goal"] is None for answer in answers)
    assert all("no solver query" in answer["detail"] for answer in answers[1:])
    assert all(answer["solve_ms"] == 0 for answer in answers[1:])
    assert selected.as_dict() == before
    assert all(goal in selected.manifest for request in requests for goal in request.targets)
    # Request validation is still mandatory even after a negative answer exists.
    with pytest.raises(TypeError, match="positive integer"):
        selected.probe(requests[0].as_dict(), 0)
    changed = requests[0].as_dict()
    changed["targets"] = ["not-a-selected-goal"]
    with pytest.raises((api.Refused, ValueError)):
        selected.probe(changed, 20_000)
    assert len(calls) == 1
    # Another audited selection owns another proof, even for identical bytes.
    second = jobs.SelectedTrace(built, selected.seed.as_dict(),
                               _trace(selected.seed).as_dict(), list(selected.targets))
    assert second.probe(_exact(second)[0].as_dict(), 20_000)["outcome"] == "local-unsat"
    assert len(calls) == 2


def test_changed_state_finds_sat_then_native_replay_confirms_hit(monkeypatch):
    built, selected = _selection(2, stateful=True)
    calls = _count_queries(monkeypatch)
    requests = _exact(selected)
    assert len(requests) == 2
    assert selected.probe(requests[0].as_dict(), 20_000)["outcome"] == "local-unsat"
    answer = selected.probe(requests[1].as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(),
                                   list(selected.targets))
    assert answer["goal"] not in selected.replay.hits
    assert answer["goal"] in confirmed.replay.hits
    # SAT is never memoized, even when exactly the same request repeats.
    assert selected.probe(requests[1].as_dict(), 20_000)["outcome"] == "sat"
    assert len(calls) == 3


def test_changed_native_history_finds_sat_with_identical_model_state(monkeypatch):
    built, selected = _selection(3, history_goal=True)
    calls = _count_queries(monkeypatch)
    requests = _exact(selected)
    assert len(requests) == 3
    assert all(not row["state"] for row in selected.occurrences.values())
    assert selected.probe(requests[0].as_dict(), 20_000)["outcome"] == "local-unsat"
    assert selected.probe(requests[1].as_dict(), 20_000)["outcome"] == "local-unsat"
    answer = selected.probe(requests[2].as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), _trace(candidate).as_dict(),
                                   list(selected.targets))
    assert answer["goal"] not in selected.replay.hits
    assert answer["goal"] in confirmed.replay.hits
    assert len(calls) == 3


@pytest.mark.parametrize("another_seed", [False, True])
def test_fixed_frame_suffix_changes_the_question_and_reset_releases_it(monkeypatch, another_seed):
    from .test_input_framing import contract, nested

    task = contract(nested(2, 3))
    source = b'''class M:
    def reset(self):
        pass
    def process(self, pixel, sof, eof, sol, eol):
        if sof and eof:
            return {"out_pixel": 255}
        return {"out_pixel": pixel}
'''
    reference = load({"behavior.py": source}, config={"class": "M"})
    built = jobs._build({"source": thaw_record_value(parent._authenticated_source_image(reference)),
        "contract": task.as_dict(), "target_artifact": "target-a",
        "generation_spec": api.GenerationSpec(6, 3, 1, 0, 0).as_dict()})
    targets = [goal for goal in built[6] if built[5][goal].family == "decision"]
    seed = api.Stimulus({"in": [(5, 1, 0, 1, 1), (6, 0, 1, 1, 1), (7, 1, 0, 1, 1)]},
        [api.EventSlot(reset=True), api.EventSlot(offers=("in",)), api.EventSlot(offers=("in",)),
         api.EventSlot(reset=True), api.EventSlot(offers=("in",)), api.EventSlot(reset=True)])

    def trace(stimulus):
        rows = iter(dict(stimulus.queues)["in"])
        return api.AuditedTrace(stimulus, records=[api.SlotRecord() if slot.reset else
            api.SlotRecord(accepted={"in": next(rows)}) for slot in stimulus.slots],
            artifact="target-a", domain="test-domain")

    selected = jobs.SelectedTrace(built, seed.as_dict(), trace(seed).as_dict(), targets)
    calls = _count_queries(monkeypatch)
    requests = tuple(request for request in _exact(selected)
                     if len(request.mutable_fields) == 5)
    assert len(requests) == 3  # Compare unchanged full-field framing questions.
    assert not any(requests[0].identity in group and requests[2].identity in group
                   for group in selected.equivalent_requests)
    assert selected.probe(requests[0].as_dict(), 20_000)["outcome"] == "local-unsat"
    # Closing the first frame early invalidates the fixed sof=0 in its suffix.
    # After reset the same model state and same goal can legally close a frame.
    assert not any(requests[0].identity in group and requests[2].identity in group
                   for group in selected.equivalent_requests)
    if another_seed:
        seed = api.Stimulus({"in": [(7, 1, 0, 1, 1)]}, [api.EventSlot(reset=True),
            api.EventSlot(offers=("in",)), api.EventSlot(reset=True)])
        selected = jobs.SelectedTrace(built, seed.as_dict(), trace(seed).as_dict(), targets,
                                     proved_questions=selected._unsat_questions)
        request = next(request for request in _exact(selected)
                       if len(request.mutable_fields) == 5)
    else:
        request = requests[2]
    answer = selected.probe(request.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), trace(candidate).as_dict(), targets)
    assert answer["goal"] not in selected.replay.hits
    assert answer["goal"] in confirmed.replay.hits
    assert len(calls) == 2


def _key_fixture(monkeypatch, outcome=probing.Outcome.LOCAL_UNSAT):
    selected = jobs.SelectedTrace.__new__(jobs.SelectedTrace)
    selected.instance = object()
    selected._query_preparation = None
    selected.observations = SimpleNamespace(catalogue={"observed"})
    selected.input_domain = SimpleNamespace(context_key=lambda rank: ())
    selected._unsat_questions = {}
    selected._partial_queries = {}
    occurrence = {"state": {"array": [[1, 2], [3, 4]]}, "previous": {
        "input": (1,), ("latency", "pending"): (0, 1)}, "rank": 1,
        "bindings": (), "input": {}}
    request = SimpleNamespace(targets=("observed",), identity="request", mutable_fields=())
    calls = []

    def query(*args, **_kwargs):
        calls.append(copy.deepcopy(args[1]))
        return probing.ProbeResult(outcome, probing.Scope.EXACT_LOCAL_HIT)

    monkeypatch.setattr(jobs, "_local_hit", query)
    return selected, occurrence, request, calls


@pytest.mark.parametrize("component", ["state", "typed-state", "goals", "history", "typed-history", "domain"])
def test_exact_question_components_are_not_conflated(monkeypatch, component):
    selected, occurrence, request, calls = _key_fixture(monkeypatch)
    selected.local_hit(occurrence, request, 1000)
    selected.local_hit(occurrence, request, 2000)
    assert len(calls) == 1  # A proved answer does not depend on a later timeout.
    if component == "state":
        occurrence["state"]["array"][0][0] = 9
    elif component == "typed-state":
        occurrence["state"]["array"][0][0] = True
    elif component == "goals":
        request.targets = ("observed", "another-goal")
    elif component == "history":
        occurrence["previous"][("latency", "pending")] = (1, 1)
    elif component == "typed-history":
        occurrence["previous"]["input"] = (True,)
    else:
        selected.input_domain.context_key = lambda rank: (("input", (0,), ({"end": 1},)),)
    selected.local_hit(occurrence, request, 1000)
    assert len(calls) == 2


def test_ir_goal_does_not_depend_on_unqueried_monitor_history(monkeypatch):
    selected, occurrence, request, calls = _key_fixture(monkeypatch)
    request.targets = ("source-goal",)
    selected.local_hit(occurrence, request, 1000)
    occurrence["rank"] = 37
    occurrence["previous"]["input"] = (99,)
    selected.local_hit(occurrence, request, 1000)
    assert len(calls) == 1


def test_compact_memory_question_keeps_base_and_writes_without_expanding(monkeypatch):
    from behave.sim.symbolic.uniform_array import UniformArray

    selected, occurrence, request, calls = _key_fixture(monkeypatch)
    memory = UniformArray.memory(1 << 30, "ram", 32).store(7, 19)
    occurrence["state"] = {"ram": memory}
    selected.local_hit(occurrence, request, 1000)
    occurrence["state"] = {"ram": UniformArray.memory(1 << 30, "ram", 32).store(7, 19)}
    selected.local_hit(occurrence, request, 1000)
    assert len(calls) == 1
    for changed in (memory.store(7, 20),
                    UniformArray.memory(1 << 30, "other", 32).store(7, 19),
                    UniformArray.memory(1 << 29, "ram", 32).store(7, 19)):
        occurrence["state"] = {"ram": changed}
        selected.local_hit(occurrence, request, 1000)
    assert len(calls) == 4


@pytest.mark.parametrize("outcome", [probing.Outcome.UNKNOWN, probing.Outcome.UNSUPPORTED])
def test_incomplete_answers_always_query_again(monkeypatch, outcome):
    selected, occurrence, request, calls = _key_fixture(monkeypatch, outcome)
    for _ in range(2):
        assert selected.local_hit(occurrence, request, 1000).outcome is outcome
    assert len(calls) == 2
    assert not selected._unsat_questions
