"""Seed-local field scopes widen without weakening framing or native evidence."""

import pytest

from behave.sim import api
from behave.sim.core.record_value import thaw_record_value
from behave.sim.engines.behavior import generation as parent
from behave.sim.engines.behavior import generation_job as jobs
from behave.sim.generation import StimulusField
from tests.support.reference import load
from .test_behavior_generation_provider import _endpoint

pytestmark = pytest.mark.solver


def test_width_order_is_name_independent_groups_ties_and_keeps_full_domain():
    bindings = tuple((name, StimulusField("in", 0, i, 1))
                     for i, name in enumerate(("payload", "tag", "op", "addr")))
    domains = dict(zip((name for name, _ in bindings),
                       map(api.FiniteType, (80, 8, 2, 4))))
    sets = parent._field_sets(bindings, domains)
    assert [[f.field for f in fs] for fs in sets] == [[2], [2, 3], [1, 2, 3], [0, 1, 2, 3]]
    assert parent._field_sets(bindings, dict.fromkeys(domains, api.FiniteType(8))) == (
        tuple(field for _name, field in bindings),)
    assert parent._field_sets((), {}) == ()


def test_recursive_field_scopes_keep_structures_whole_and_enumerations_typed():
    from behave.sim.core.value_type import ValueType

    domains = {
        "wide": ValueType.array(2, api.FiniteType(8)),
        "record": ValueType.record({"items": ValueType.array(3, api.FiniteType(2)),
                                    "flag": ValueType.boolean()}),
        "tag": ValueType.enumeration(("z", "a", "q")),
        "flag": ValueType.boolean(),
        "empty": ValueType.array(0, api.FiniteType(8)),
        "scalar": api.FiniteType(7),
    }
    bindings = tuple((name, StimulusField("in", 0, i, 1))
                     for i, name in enumerate(domains))
    selected = parent._field_sets(bindings, domains)
    assert [[field.field for field in fields] for fields in selected] == [
        [4], [3, 4], [2, 3, 4], [1, 2, 3, 4, 5], [0, 1, 2, 3, 4, 5]]
    # Recursive leaves affect cost, not the external queue-field coordinates.
    values = {"wide": [2, 9], "record": {"items": [1, 2, 3], "flag": True},
              "tag": "z", "flag": False, "empty": [], "scalar": 4}
    fixed = parent._fixed_inputs(bindings, selected[-2], values)
    assert fixed == {"wide": [2, 9]}
    assert fixed["wide"] is values["wide"]


def selection(condition="a == 2 and b == 7", values=((0, 0),)):
    source = f'''class M:
    def reset(self): pass
    def process(self, a, b):
        if {condition}:
            return {{"y": 1}}
        return {{"y": 0}}
'''.encode()
    contract = api.TaskContract([
        _endpoint("left", api.Direction.IN, "a", "a", api.FiniteType(2)),
        _endpoint("right", api.Direction.IN, "b", "b", api.FiniteType(8)),
        _endpoint("result", api.Direction.OUT, "y", "y", api.FiniteType(1)),
    ], [api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=1)])
    reference = load({"behavior.py": source}, config={"class": "M"})
    built = jobs._build({
        "source": thaw_record_value(parent._authenticated_source_image(reference)),
        "contract": contract.as_dict(), "target_artifact": "target-a",
        "generation_spec": api.GenerationSpec(len(values)+1, len(values), 1, 0, 0).as_dict()})
    seed = api.Stimulus({"left": [(a,) for a, _b in values], "right": [(b,) for _a, b in values]},
        [api.EventSlot(reset=True)] + [api.EventSlot(offers=("left", "right"))] * len(values))
    targets = [g for g in built[6] if built[5][g].family == "decision" and g.endswith(":T")]
    assert len(targets) == 1
    return built, jobs.SelectedTrace(built, seed.as_dict(), trace(seed).as_dict(), targets)


def trace(seed):
    queues = dict(seed.queues)
    return api.AuditedTrace(seed, records=[api.SlotRecord()] + [
        api.SlotRecord(accepted={name: values[i] for name, values in queues.items()})
        for i in range(len(queues["left"]))], artifact="target-a", domain="test-domain")


def exact_requests(selected):
    """The exact local questions, without the path proposals and contexts ordered around them."""
    return tuple(r for r in selected.requests if r.occurrence in selected.occurrences)


def test_restricted_unsat_cannot_discharge_full_question():
    built, selected = selection()
    small, full = exact_requests(selected)
    assert len(small.mutable_fields) == 1 and len(full.mutable_fields) == 2
    assert selected.probe(small.as_dict(), 20_000)["outcome"] == "local-unsat"
    answer = selected.probe(full.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    assert dict(candidate.queues) == {"left": ((2,),), "right": ((7,),)}
    confirmed = jobs.SelectedTrace(built, candidate.as_dict(), trace(candidate).as_dict(), list(small.targets))
    assert answer["goal"] in confirmed.replay.hits


def test_kept_seed_value_is_part_of_equivalence_and_unsat_scope():
    _built, selected = selection(values=((0, 0), (0, 7)))
    small0, full0, small1, full1 = exact_requests(selected)
    assert [len(r.mutable_fields) for r in (small0, full0, small1, full1)] == [1, 2, 1, 2]
    assert small0.rank < full0.rank < small1.rank < full1.rank
    equivalent = selected.equivalent_requests
    assert any(set(group) == {full0.identity, full1.identity} for group in equivalent)
    assert not any(small0.identity in group or small1.identity in group for group in equivalent)
    assert selected.probe(small0.as_dict(), 20_000)["outcome"] == "local-unsat"
    answer = selected.probe(small1.as_dict(), 20_000)
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    assert dict(candidate.queues)["right"] == dict(selected.seed.queues)["right"]
    assert dict(candidate.queues)["left"] == ((0,), (2,))


def test_fixed_fields_do_not_bypass_legal_input_constraints():
    from types import SimpleNamespace
    _built, selected = selection(values=((0, 7),))
    selected.input_domain = SimpleNamespace(
        context_key=lambda _rank: ("exclude-a-two",),
        constraints=lambda _rank, inputs: (inputs["a"] != 2,))
    for request in selected.requests.values():
        answer = selected.probe(request.as_dict(), 20_000)
        if answer["scope"] == "state-preparation":
            # A relaxed proposal claims no UNSAT, and still names no illegal candidate.
            assert answer["outcome"] == "unknown" and answer["candidate"] is None, answer
        else:
            assert answer["outcome"] == "local-unsat", answer


@pytest.mark.parametrize("outcome", ["unknown", "constructing"])
def test_field_scopes_retain_only_their_latest_exact_unfinished_question(monkeypatch, outcome):
    from behave.sim.engines import probing

    _built, selected = selection(values=((0, 0), (0, 7)))
    small0, full0, small1, full1 = exact_requests(selected)
    marker = object()
    owners = []

    def unfinished(*args, **kwargs):
        owners.append((kwargs["fixed_inputs"], kwargs["constructions"],
                       kwargs["proved_unsat"], marker in kwargs["constructions"]))
        kwargs["constructions"][marker] = object()
        kwargs["proved_unsat"].add("completed-subquestion")
        return probing.ProbeResult(probing.Outcome(outcome), probing.Scope.EXACT_LOCAL_HIT)

    monkeypatch.setattr(jobs, "_local_hit", unfinished)
    for request in (small0, full0, small0, full1, small1, small0):
        assert selected.probe(request.as_dict(), 1000)["outcome"] == outcome
        assert len(selected._partial_queries) <= 2
    assert [item[3] for item in owners] == [False, False, True, True, False, False]
    assert owners[0][1] is owners[2][1] and owners[1][1] is owners[3][1]
    assert owners[0][2] is owners[2][2] and owners[1][2] is owners[3][2]
    assert owners[0][1] is not owners[1][1]
    # A changed fixed value replaces, rather than archives, its scope owner.
    assert owners[4][0] == {"b": 7} and owners[5][0] == {"b": 0}
    assert not selected._unsat_questions


@pytest.mark.parametrize("second_b", [7, 0])
def test_resumed_encoder_crosses_only_equivalent_occurrences(monkeypatch, second_b):
    from behave.sim.symbolic import encoder
    from behave.sim.engines import local_ir

    built, selected = selection(values=((0, 7), (0, second_b)))
    small0, full0, small1, _full1 = exact_requests(selected)
    encode = local_ir._encode_query_step

    def pause_new_owner(*args, **kwargs):
        owner = kwargs["_construction"]
        if owner.iterator is None and owner.result is None:
            def pause():
                raise encoder._EncodingPaused
            kwargs["_checkpoint"] = pause
        return encode(*args, **kwargs)

    monkeypatch.setattr(local_ir, "_encode_query_step", pause_new_owner)
    assert selected.probe(small0.as_dict(), 20_000)["outcome"] == "constructing"
    assert selected.probe(full0.as_dict(), 20_000)["outcome"] == "constructing"
    answer = selected.probe(small1.as_dict(), 20_000)
    if second_b == 0:
        assert answer["outcome"] == "constructing"  # The kept value changes the question.
        assert selected.probe(small1.as_dict(), 20_000)["outcome"] == "local-unsat"
    else:
        assert answer["outcome"] == "sat", answer
        candidate = api.Stimulus.from_dict(answer["candidate"])
        assert dict(candidate.queues) == {"left": ((0,), (2,)), "right": ((7,), (7,))}
        confirmed = jobs.SelectedTrace(built, candidate.as_dict(), trace(candidate).as_dict(), list(small1.targets))
        assert answer["goal"] in confirmed.replay.hits
    assert ("b",) not in selected._partial_queries  # Finished work releases its owner.
