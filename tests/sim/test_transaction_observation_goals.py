"""Task declarations, not source instrumentation, own transaction predicates."""

import pickle
from collections import Counter
from itertools import pairwise
from time import perf_counter
from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.goal_provider import CONCRETE_PHASE, EXACT_ANALYSIS_PHASE
from behave.sim.core.observation_monitor import TransactionMonitor, execution_monitor_for
from behave.sim.core.stimulus import SlotRecord
from behave.sim.goal_policy import evaluation_goal_policy

from .test_baseline_public_sequence import REFERENCE, RTL, _contract
from .test_command_handler import _BaselineOnly


def _with_goals(contract, goals):
    return api.TaskContract(contract.endpoints, contract.clocks,
                            protocol_instances=contract.protocol_instances,
                            observation_goals=goals)


def _rules():
    return (
        api.TransactionGoal("input-max", "in", api.GoalCondition("eq", api.GoalField("a"), 255)),
        api.TransactionGoal("output-max", "out", api.GoalCondition("eq", api.GoalField("y"), 255)),
        api.TransactionGoal("output-rise", "out", api.GoalCondition(
            "gt", api.GoalField("y"), api.GoalField("y", previous=True))),
        api.TransactionGoal("output-bit-fall", "out", api.GoalCondition("and",
            api.GoalCondition("eq", api.GoalField("y", previous=True, bit=7), True),
            api.GoalCondition("eq", api.GoalField("y", bit=7), False))),
        # A legitimate unreachable value predicate stays in the catalogue.
        api.TransactionGoal("outside-carrier", "out", api.GoalCondition("eq", api.GoalField("y"), 256)),
    )


def test_contract_roundtrip_identity_and_capabilities():
    old = _contract()
    old_image = old.as_dict()
    old_image["version"] = "task-contract/9"
    del old_image["observation_goals"]
    del old_image["transaction_monitors"]
    del old_image["latency_goals"]
    del old_image["operation_rules"]
    del old_image["source_read_rules"]
    assert api.TaskContract.from_dict(old_image).digest == old.digest
    contract = _with_goals(old, _rules())
    assert api.TaskContract.from_dict(contract.as_dict()).as_dict() == contract.as_dict()
    assert contract.digest != old.digest
    assert _with_goals(old, tuple(reversed(_rules()))).digest == contract.digest
    assert pickle.loads(pickle.dumps(contract)).as_dict() == contract.as_dict()
    for kind in ("behavior", "rtl"):
        policy = evaluation_goal_policy(contract, CatalogueManifest(()), artifact_kind=kind)
        policy.validate(CONCRETE_PHASE)
        assert CatalogueManifest(execution_monitor_for(contract, kind).goals()) == policy.manifest
        names = {name for name in policy.manifest if name.startswith("observation:")}
        assert names == {"observation:" + rule.name for rule in _rules()}
        # Ordinary transaction-step Behavior and RTL both bind history products.
        # This declaration check is not a proof, nor an atomic-Behavior claim.
        policy.validate(EXACT_ANALYSIS_PHASE)


@pytest.mark.parametrize("condition", [
    api.GoalCondition("eq", api.GoalField("missing"), 0),
    api.GoalCondition("eq", api.GoalField("y", bit=8), False),
    api.GoalCondition("eq", api.GoalField("y"), True),
    api.GoalCondition("and", api.GoalField("y"), True),
])
def test_bad_binding_is_rejected_before_any_replay(condition):
    with pytest.raises((TypeError, ValueError)):
        _with_goals(_contract(), (api.TransactionGoal("bad", "out", condition),))


def test_duplicate_names_and_unknown_wire_fields_are_rejected():
    with pytest.raises(ValueError, match="distinct"):
        _with_goals(_contract(), (_rules()[0], _rules()[0]))
    image = _with_goals(_contract(), _rules()).as_dict()
    image["observation_goals"][0]["ignore_me"] = True
    with pytest.raises(TypeError):
        api.TaskContract.from_dict(image)


@pytest.mark.parametrize("behavior", (False, True))
def test_uses_actual_ordered_transactions_and_retains_unreadable_previous(behavior):
    monitor = TransactionMonitor(_with_goals(_contract(), _rules()), behavior=behavior)

    def record(value):
        return (SlotRecord(accepted={"in": (value,)}, outputs={"out": ((value,),)})
                if behavior else SlotRecord(accepted={"in": (value,), "out": (value,)}))

    trace = SimpleNamespace(records=[record(0), record(255), record(None), record(0), record(255), record(0)])
    report = monitor.check(None, trace)
    by_goal = {name: [hit.slot for hit in report.hits if hit.goal == name]
               for name in ("observation:output-rise", "observation:output-bit-fall")}
    assert by_goal == {"observation:output-rise": [1, 4], "observation:output-bit-fall": [5]}
    assert report.opportunities["observation:output-rise"] == 5
    assert any(row.slot == 3 and row.goal == "observation:output-rise" for row in report.unresolved)
    assert not any(hit.goal == "observation:outside-carrier" for hit in report.hits)
    # Checking another run does not inherit the previous run's last transaction.
    assert not any(hit.goal == "observation:output-rise"
                   for hit in monitor.check(None, SimpleNamespace(records=[record(255)])).hits)


def test_behavior_batch_order_and_rtl_nontransfer_are_not_pin_snapshots():
    contract = _with_goals(_contract(), _rules())
    behavior = TransactionMonitor(contract, behavior=True)
    trace = SimpleNamespace(records=[SlotRecord(outputs={"out": ()}),
        SlotRecord(outputs={"out": ((0,), (255,), (0,))})])
    report = behavior.check(None, trace)
    assert report.opportunities["observation:output-rise"] == 2
    assert [(hit.goal, hit.slot) for hit in report.hits] == [
        ("observation:output-max", 1), ("observation:output-rise", 1),
        ("observation:output-bit-fall", 1)]
    rtl = TransactionMonitor(contract, behavior=False)
    report = rtl.check(None, SimpleNamespace(records=[
        SlotRecord(accepted={"out": None}, outputs={"y": 255, "out_valid": 1})]))
    assert not report.hits and not report.opportunities


def test_wide_signed_scalar_compares_logical_values_without_enumeration():
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", api.FiniteType(64, signed=True), "a")],
                     api.settled("in"), None, kind=api.Kind.TRANSACTION)], [],
        observation_goals=[api.TransactionGoal("negative", "in", api.GoalCondition(
            "and", api.GoalCondition("lt", api.GoalField("a"), 0),
            api.GoalCondition("eq", api.GoalField("a", bit=63), True)))])
    monitor = TransactionMonitor(contract, behavior=True)
    report = monitor.check(None, SimpleNamespace(records=[
        SlotRecord(accepted={"in": (-(1 << 63),)}), SlotRecord(accepted={"in": (0,)})]))
    assert [hit.slot for hit in report.hits] == [0]


def test_predicates_match_exhaustive_small_carrier_oracle():
    condition = api.GoalCondition("or",
        api.GoalCondition("and",
            api.GoalCondition("gt", api.GoalField("a"), api.GoalField("a", previous=True)),
            api.GoalCondition("eq", api.GoalField("a", bit=3), True)),
        api.GoalCondition("not", api.GoalCondition("ne", api.GoalField("a"), 7)))
    rule = api.TransactionGoal("check", "in", condition)
    _endpoint, predicate = rule.bind(_contract().endpoints)
    for old in range(16):
        for new in range(16):
            assert predicate((new,), (old,)) is ((new > old and bool(new & 8)) or new == 7)


def test_multiple_fields_boolean_carrier_and_reset_do_not_change_sampling():
    from behave.sim.core.stimulus import EventSlot, Stimulus
    endpoint = api.Endpoint("in", "in", [
        api.Field("ready", api.ValueType.boolean(), "ready"),
        api.Field("value", api.FiniteType(8, signed=True), "value")], api.settled("in"), None,
        kind=api.Kind.TRANSACTION)
    goals = [api.TransactionGoal("active-negative", "in", api.GoalCondition(
        "and", api.GoalCondition("eq", api.GoalField("ready"), True),
        api.GoalCondition("lt", api.GoalField("value"), 0))),
        api.TransactionGoal("cross-reset", "in", api.GoalCondition(
            "lt", api.GoalField("value"), api.GoalField("value", previous=True)))]
    contract = api.TaskContract([endpoint], [], observation_goals=goals)
    monitor = TransactionMonitor(contract, behavior=True)
    stimulus = Stimulus(queues={"in": ((True, 1), (True, -1))},
                       slots=[EventSlot(), EventSlot(reset=True), EventSlot()])
    report = monitor.check(stimulus, SimpleNamespace(records=[
        SlotRecord(accepted={"in": (True, 1)}), SlotRecord(),
        SlotRecord(accepted={"in": (True, -1)})]))
    assert {(hit.goal, hit.slot) for hit in report.hits} == {
        ("observation:active-negative", 2), ("observation:cross-reset", 2)}


@pytest.mark.isolation
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
def test_public_baseline_tracks_the_declared_goals_on_both_backends(tmp_path, kind, record_property):
    started = perf_counter()
    contract = _with_goals(_contract(), _rules())
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    if kind == "behavior":
        path = tmp_path / "candidate.py"
        path.write_text(REFERENCE)
        task = api.admit(contract, api.realization_of_behavior_source(REFERENCE, contract, file="candidate.py"),
                         reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="candidate.py", timeout_s=60),
                                      bounded_analysis=False)
    else:
        source = RTL.replace("VALUE", "a")
        path = tmp_path / "candidate.sv"
        path.write_text(source)
        task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                         reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl", timeout_s=120))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", baseline=api.BaselinePolicy(),
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=False, reference=reference))
    record_property("public_lifecycle_seconds", perf_counter() - started)
    record_property("unique_baseline", len(result.generated.stimuli) if result.generated else 0)
    assert result.status is api.CommandStatus.EVALUATED, result.status
    assert result.evaluation.complete and result.evaluation.passed
    manifest = result.evaluation.catalogue
    assert {"observation:" + rule.name for rule in _rules()} <= set(manifest)
    hits = set(result.evaluation.hits)
    assert {"observation:input-max", "observation:output-max", "observation:output-rise",
            "observation:output-bit-fall"} <= hits
    assert "observation:outside-carrier" not in hits
    # Independently re-read saved transaction payloads, without another replay.
    value_hits, rises, falls = 0, 0, 0
    from behave.sim.core.compare import observed_transfers_of
    for run in result.evaluation.results:
        values = [payload[0] for payload in observed_transfers_of(task, run.trace, "out")]
        value_hits += values.count(255)
        rises += sum(b > a for a, b in pairwise(values))
        falls += sum(bool(a & 128) and not b & 128 for a, b in pairwise(values))
    assert value_hits and rises and falls
    recorded_counts = Counter(dict(row)["goal"] for _run, row in
                              dict(result.evaluation.telemetry)["monitor_hits"])
    assert recorded_counts["observation:output-max"] == value_hits
    assert recorded_counts["observation:output-rise"] == rises
    assert recorded_counts["observation:output-bit-fall"] == falls
    record_property("observation_hits", dict(recorded_counts))
    assert all(stimulus.depth("in") == 100 for stimulus in result.generated.stimuli)
    assert execution_monitor_for(contract, kind).goals()
