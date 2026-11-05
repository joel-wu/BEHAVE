"""Independent oracles for sets of events at actual logical execution entries."""

import pickle
from itertools import combinations, product
from types import SimpleNamespace

import pytest
import z3

from behave.sim import api
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.goal_provider import CONCRETE_PHASE, EXACT_ANALYSIS_PHASE, RTL_LOCAL_GUIDANCE_POLICY
from behave.sim.core.observation_product import TransactionProduct
from behave.sim.core.observation_symbolic import condition_terms
from behave.sim.core.ready_valid_product import ConcreteAlgebra
from behave.sim.core.stimulus import SlotRecord
from behave.sim.core.transaction_monitor import DeclaredTransactionMonitor
from behave.sim.engines.probing import Outcome
from behave.sim.goal_policy import evaluation_goal_policy
from behave.sim.rtl.task_symbolic import Z3Algebra

from .test_baseline_public_sequence import REFERENCE, RTL, _contract
from .test_transaction_goal_queries import _lower, _probe


def _rule(events=None, *, order=("out",), safety=False):
    events = events or (
        api.TransactionEvent("a", "out", api.GoalCondition("eq", api.GoalField("y"), 7)),
        api.TransactionEvent("b", "out", api.GoalCondition("eq", api.GoalField("y"), 9)),
        api.TransactionEvent("other", "out"))
    labels = sorted({case.event for case in events})
    alphabet = tuple(event for size in range(1, len(labels) + 1) for event in combinations(labels, size))
    rows = []
    for state, event in product((0, 1, 2), alphabet):
        hits = []
        if event == ("a", "b"):
            hits.append("together")
        if state == 1 and event == ("b",):
            hits.append("ordered")
        if state == 2 and event == ("a",):
            hits.append("reversed")
        if event == ("a", "b", "other"):
            hits.append("all-three")
        rows.append(api.FiniteMonitorTransition(state, event,
            1 if event == ("a",) else 2 if event == ("b",) else 0, hits))
    return api.TransactionMonitorSpec("entry", api.FiniteMonitor(
        (0, 1, 2), alphabet, 0, ("together", "ordered", "reversed", "all-three"), rows),
        events, order=order, sampling="entry-set", violations=("together",) if safety else ())


def _contract_with(rule):
    base = _contract()
    return api.TaskContract(base.endpoints, base.clocks, transaction_monitors=(rule,))


def _bool(term):
    return z3.is_true(z3.simplify(term))


def test_entry_policy_wire_and_distinct_semantic_identity():
    contract = _contract_with(_rule())
    assert api.TaskContract.from_dict(contract.as_dict()).digest == contract.digest
    assert pickle.loads(pickle.dumps(contract)).as_dict() == contract.as_dict()
    legacy = _contract().as_dict()
    legacy.pop("latency_goals")
    legacy.pop("operation_rules")
    legacy.pop("source_read_rules")
    legacy["version"] = "task-contract/11"
    assert api.TaskContract.from_dict(legacy).digest == _contract().digest
    old = contract.as_dict()
    old.pop("latency_goals")
    old.pop("operation_rules")
    old.pop("source_read_rules")
    old["version"] = "task-contract/11"
    with pytest.raises(TypeError, match="task-contract/12"):
        api.TaskContract.from_dict(old)
    for kind in ("behavior", "rtl"):
        policy = evaluation_goal_policy(contract, CatalogueManifest(()), artifact_kind=kind)
        policy.validate(CONCRETE_PHASE)
        policy.validate(EXACT_ANALYSIS_PHASE)
        goal = policy.manifest["monitor:entry:together"]
        assert goal.family == "schedule-interleaving" and goal.cls == api.COVERAGE
        assert goal.event == "logical-entry-event-set"


@pytest.mark.parametrize("alphabet", ((("a",),), (("a",), ("b",), ("b", "a")),
                                      (("a",), ("b",), ("a", "a"))))
def test_entry_alphabet_cannot_omit_or_reorder_members(alphabet):
    fsm = api.FiniteMonitor((0,), alphabet, 0, ("seen",), [
        api.FiniteMonitorTransition(0, event, 0, ("seen",)) for event in alphabet])
    with pytest.raises(ValueError, match="entry-set"):
        api.TransactionMonitorSpec("bad", fsm,
            (api.TransactionEvent("a", "in"), api.TransactionEvent("b", "out")),
            order=("in", "out"), sampling="entry-set")


@pytest.mark.parametrize("safety", (False, True))
def test_native_entry_does_not_serialize_batch_or_advance_padding(safety):
    binding = DeclaredTransactionMonitor(_contract_with(_rule(safety=safety)), behavior=True)
    def check(*batches):
        records = [SlotRecord(outputs={"out": tuple((value,) for value in batch)}) for batch in batches]
        records.insert(1, SlotRecord())  # No transaction, not an invented clock tick.
        records += [SlotRecord(monitor={"padding": True}, outputs={"out": ((7,), (9,), (0,))})]
        return binding.check(None, SimpleNamespace(records=records))

    together = check((7, 9, 7))
    assert {item.goal for item in (*together.hits, *together.violations)} == {"monitor:entry:together"}
    assert bool(together.violations) is safety
    assert together.opportunities["monitor:entry:together"] == 1
    assert {item.goal for item in check((7,), (9,)).hits} == {"monitor:entry:ordered"}
    assert {item.goal for item in check((9,), (7,)).hits} == {"monitor:entry:reversed"}
    assert len(binding.goals()) == 4  # Unhit/impossible-on-this-artifact goals remain declared.


def test_native_symbolic_and_independent_set_oracle_exhaustive():
    contract = _contract_with(_rule())
    native = DeclaredTransactionMonitor(contract, behavior=True)
    binding = TransactionProduct(contract)
    # Include reordered/repeated labels, empty batches, an unsampled matching
    # value, and all three labels; a per-transaction accumulator is not an oracle.
    batches = ((), (7,), (9,), (0,), (7, 9), (9, 7, 7), (7, 9, 0), (7, 7))
    for pair in product(batches, repeat=2):
        states = [binding.monitors.initial(algebra) for algebra in (ConcreteAlgebra, Z3Algebra())]
        prior = set()
        expected_slots = set()
        for slot, batch in enumerate(pair):
            labels = {"a" if value == 7 else "b" if value == 9 else "other" for value in batch}
            expected = set()
            if labels:
                if labels == {"a", "b"}:
                    expected.add("monitor:entry:together")
                if prior == {"a"} and labels == {"b"}:
                    expected.add("monitor:entry:ordered")
                if prior == {"b"} and labels == {"a"}:
                    expected.add("monitor:entry:reversed")
                if labels == {"a", "b", "other"}:
                    expected.add("monitor:entry:all-three")
                prior = labels
            expected_slots.update((slot, goal) for goal in expected)
            samples = {"out": tuple((True, (value,)) for value in batch) + ((False, (9,)),)}
            for i, algebra in enumerate((ConcreteAlgebra, Z3Algebra())):
                states[i], hits = binding.monitors.advance(states[i],
                    samples, algebra, condition=condition_terms if i else None)
                actual = {goal for goal, value in hits.items() if (_bool(value) if i else value)}
                assert actual == expected, (pair, slot, i)
            assert states[0]["entry"] == tuple(map(_bool, states[1]["entry"]))
        report = native.check(None, SimpleNamespace(records=[
            SlotRecord(outputs={"out": tuple((value,) for value in batch)}) for batch in pair]))
        assert {(hit.slot, hit.goal) for hit in report.hits} == expected_slots


def test_unreadable_entry_member_is_not_dropped_and_poison_survives_silence():
    contract = _contract_with(_rule())
    product_ = TransactionProduct(contract)
    states = [product_.monitors.initial(algebra) for algebra in (ConcreteAlgebra, Z3Algebra())]
    records = []
    for batch in ((7, None), (), (7, 9)):
        records.append(SlotRecord(outputs={"out": tuple((value,) for value in batch)}))
        for i, algebra in enumerate((ConcreteAlgebra, Z3Algebra())):
            states[i], hits = product_.monitors.advance(states[i],
                {"out": tuple((True, (value,)) for value in batch)}, algebra,
                condition=condition_terms if i else None)
            observed = map(_bool, hits.values()) if i else hits.values()
            assert not any(observed)
    assert states[0]["entry"] == (False, False, False)
    report = DeclaredTransactionMonitor(contract, behavior=True).check(None, SimpleNamespace(records=records))
    assert not report.hits and report.unresolved


def test_unreadable_entry_prefix_is_not_reported_as_local_unsat():
    _c, instance, binding = _lower('        return [{"y": 7}, {"y": a}]\n', monitors=(_rule(),))
    previous = {}
    binding.observe(previous, {"a": 0}, ({"y": 7}, {"y": None}))
    answer = _probe(instance, binding, previous, "monitor:entry:together", position=1)
    assert answer.outcome is Outcome.UNSUPPORTED


@pytest.mark.parametrize("safety", (False, True))
def test_local_query_changes_entry_values_and_replay_confirms_set_not_sequence(safety):
    _c, instance, binding = _lower('        return [{"y": 7}, {"y": a}]\n',
                                  monitors=(_rule(safety=safety),), bound=1)
    goal = "monitor:entry:together"
    assert goal not in instance.replay([{"a": 0}]).hits
    result = _probe(instance, binding, {}, goal)
    assert result.outcome is Outcome.SAT and result.candidate == {"a": 9}
    replay = instance.replay([result.candidate])
    assert goal in replay.hits and "monitor:entry:ordered" not in replay.hits
    assert instance.audit(([result.candidate],)).status is AuditCheckStatus.AGREED
    assert instance.catalogue_manifest()[goal].cls == (api.SAFETY if safety else api.COVERAGE)


def test_audit_detects_an_incorrect_symbolic_entry_hit():
    from behave.sim.behavior import residual

    _c, instance, binding = _lower('        return [{"y": 7}, {"y": 9}]\n',
                                  monitors=(_rule(),), bound=1)
    trace = [{"a": 0}]
    assert "monitor:entry:together" in instance.replay(trace).hits
    run = residual.unroll(instance.proc, instance.env, 1,
                          plan=instance._position_plan, transactions=binding)
    run.transaction_components["monitor:entry:together"] = (z3.BoolVal(False),)
    instance._prepared_transaction_audit = run
    assert instance.audit((trace,)).status is AuditCheckStatus.INCONSISTENT


@pytest.mark.parametrize("bound", (1, 2))
def test_whole_history_distinguishes_entry_sets_and_separate_invocations(bound):
    _c, instance, _binding = _lower('        return {"y": a}\n', monitors=(_rule(),), bound=bound)
    goals = ("monitor:entry:together", "monitor:entry:ordered")
    result = instance.query(goals, bound, timeout_ms=10_000, workers=1)
    assert not result.undecided
    assert "monitor:entry:together" in result.unreachable  # One output per invocation.
    if bound == 1:
        assert "monitor:entry:ordered" in result.unreachable
    else:
        witness = result.reachable["monitor:entry:ordered"]
        assert "monitor:entry:ordered" in instance.replay(witness).hits
        assert instance.audit((witness,)).status is AuditCheckStatus.AGREED
    assert set(goals) <= set(instance.catalogue_manifest())


@pytest.mark.parametrize("order", (("a_out", "b_out"), ("b_out", "a_out")))
def test_atomic_entry_groups_coincident_handlers_and_keeps_silent_reset_history(order):
    from behave.sim.behavior.atomic_exact import build_atomic_behavior
    from behave.sim.core.clock_schedule import GlobalEventSlot, GlobalSchedule
    from tests.support.atomic_behavior import _contract as atomic_contract
    from tests.support.atomic_behavior import _schedule

    from .test_behavior_atomic_exact import _facts
    from .test_behavior_atomic_transactions import IDENTITY

    rule = _rule((
        api.TransactionEvent("a", "a_out", api.GoalCondition("eq", api.GoalField("oa"), 1)),
        api.TransactionEvent("other", "a_out"),
        api.TransactionEvent("b", "b_out", api.GoalCondition("eq", api.GoalField("ob"), 2)),
        api.TransactionEvent("other", "b_out")), order=order)
    base = atomic_contract()
    contract = api.TaskContract(base.endpoints, base.clocks, transaction_monitors=(rule,))
    instance = build_atomic_behavior(IDENTITY, "entry_atomic.py", contract, _schedule(n_slots=1),
                                    task_contract=contract.digest, construction=_facts({}))
    goal = "monitor:entry:together"
    for a, b in product(range(4), repeat=2):
        rows = [{"a.__invoke__": 1, "a.a": a, "b.__invoke__": 1, "b.b": b}]
        assert (goal in instance.replay(rows).hits) == (a == 1 and b == 2)
        assert "monitor:entry:ordered" not in instance.replay(rows).hits
        assert instance.audit((rows,)).status is AuditCheckStatus.AGREED
    verdict = instance.query((goal, "monitor:entry:ordered"), 1, timeout_ms=10_000, workers=1)
    assert not verdict.undecided and goal in verdict.reachable
    assert verdict.unreachable == {"monitor:entry:ordered"}

    schedule = _schedule((2, 2), n_slots=3)
    slots = list(schedule.slots)
    slots[1] = GlobalEventSlot(slots[1].time, event=api.EventSlot(ticks=("a", "b"), resets=("a", "b"),
        controls={"a_rst": 1, "b_rst": 1, "oa_ready": 1, "ob_ready": 1}))
    instance = build_atomic_behavior(IDENTITY, "entry_atomic.py", contract,
        GlobalSchedule(schedule.timings, slots), task_contract=contract.digest, construction=_facts({}))
    rows = [
        {"a.__invoke__": 1, "a.a": 1, "b.__invoke__": 0, "b.b": 0},
        {"a.__invoke__": 0, "a.a": 0, "b.__invoke__": 0, "b.b": 0},
        {"a.__invoke__": 0, "a.a": 0, "b.__invoke__": 1, "b.b": 2},
    ]
    replay = instance.replay(rows)
    assert "monitor:entry:ordered" in replay.hits and goal not in replay.hits
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED


def test_failure_does_not_turn_proposed_atomic_outputs_into_an_entry_hit():
    from behave.sim.behavior.atomic_exact import build_atomic_behavior
    from tests.support.atomic_behavior import ATOMIC_PARTIAL_FAILURE_SOURCE, _schedule
    from tests.support.atomic_behavior import _contract as atomic_contract

    from .test_behavior_atomic_exact import _facts, _rows

    rule = _rule((api.TransactionEvent("a", "a_out"), api.TransactionEvent("b", "b_out")),
                 order=("a_out", "b_out"))
    base = atomic_contract()
    contract = api.TaskContract(base.endpoints, base.clocks, transaction_monitors=(rule,))
    instance = build_atomic_behavior(ATOMIC_PARTIAL_FAILURE_SOURCE, "entry_failure.py", contract,
        _schedule(), task_contract=contract.digest, construction=_facts({}))
    rows = _rows(a0=0, b0=2, a1=1, b2=2)
    replay = instance.replay(rows)
    assert replay.error == "DivZero" and not replay.broken
    assert not any(goal.startswith("monitor:entry:") for goal in replay.hits)
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
def test_public_baseline_then_search_confirms_an_entry_set(tmp_path, kind):
    rule = _rule((
        api.TransactionEvent("a", "in", api.GoalCondition("eq", api.GoalField("a"), 173)),
        api.TransactionEvent("other", "in"),
        api.TransactionEvent("b", "out", api.GoalCondition("eq", api.GoalField("y"), 173)),
        api.TransactionEvent("other", "out")), order=("in", "out"))
    contract = _contract_with(rule)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    if kind == "behavior":
        path = tmp_path / "candidate.py"
        path.write_text(REFERENCE)
        task = api.admit(contract, api.realization_of_behavior_source(REFERENCE, contract, file="candidate.py"),
                         reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="candidate.py", timeout_s=90),
                                      bounded_analysis=False)
        providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "queries")
                          if "candidate:" in p.name)
    else:
        path = tmp_path / "M.sv"
        path.write_text(RTL.replace("VALUE", "a"))
        task = api.admit(contract, api.realization_of_rtl_source(path.read_text(), top="M", contract=contract),
                         reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl", timeout_s=120))
        providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "queries")
                          if p.policy == RTL_LOCAL_GUIDANCE_POLICY)
    assert len(providers) == 1
    request = handle.command_request(run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100), providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True), run_analysis=False)
    result = api.handle_command(request)
    assert result.status is api.CommandStatus.EVALUATED
    assert result.evaluation.complete and result.decision.authoritative and result.decision.passed
    goal = "monitor:entry:together"
    assert goal in result.generated.searches[0].telemetry["baseline_coverage"]["unhit"]
    assert goal in result.evaluation.hits
    assert any(goal in dict(row)["targets"] for rows in result.generated.searches[0].provenance.values()
               for row in rows)
