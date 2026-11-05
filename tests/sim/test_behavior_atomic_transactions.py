"""Atomic queries and native records share transaction history, not handler order."""

from itertools import product

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.behavior.atomic_exact import build_atomic_behavior
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.clock_schedule import GlobalEventSlot, GlobalSchedule
from tests.support.atomic_behavior import ATOMIC_PARTIAL_FAILURE_SOURCE, SOURCE, _contract, _schedule

from .test_behavior_atomic_exact import _facts, _rows
from .test_command_handler import _BaselineOnly

IDENTITY = '''class M:
    def reset(self):
        pass
    def process_a(self, a):
        return {"oa": a}
    def process_b(self, b):
        return {"ob": b}
'''


def _declarations(*, safety=False, order=("a_out", "b_out")):
    fsm = api.FiniteMonitor((0, 1), ("a-one", "b-two", "other"), 0, ("hit",), [
        api.FiniteMonitorTransition(state, event, int(event == "a-one"),
            ("hit",) if state == 1 and event == "b-two" else ())
        for state, event in product((0, 1), ("a-one", "b-two", "other"))])
    sequence = api.TransactionMonitorSpec("cross", fsm, (
        api.TransactionEvent("a-one", "a_out", api.GoalCondition("eq", api.GoalField("oa"), 1)),
        api.TransactionEvent("other", "a_out"),
        api.TransactionEvent("b-two", "b_out", api.GoalCondition("eq", api.GoalField("ob"), 2)),
        api.TransactionEvent("other", "b_out")), order=order,
        violations=("hit",) if safety else ())
    scalar = (
        api.TransactionGoal("zero-input", "a_in", api.GoalCondition("eq", api.GoalField("a"), 0)),
        api.TransactionGoal("two-output", "b_out", api.GoalCondition("eq", api.GoalField("ob"), 2)),
        api.TransactionGoal("a-changed", "a_out", api.GoalCondition("ne",
            api.GoalField("oa", previous=True), api.GoalField("oa"))),
        api.TransactionGoal("outside", "b_out", api.GoalCondition("eq", api.GoalField("ob"), 16)),
    )
    base = _contract()
    return api.TaskContract(base.endpoints, base.clocks, observation_goals=scalar,
                            transaction_monitors=(sequence,))


def _build(*, source=IDENTITY, schedule=None, safety=False, order=("a_out", "b_out")):
    contract = _declarations(safety=safety, order=order)
    return build_atomic_behavior(source, "atomic_transactions.py", contract, schedule or _schedule(),
        task_contract=contract.digest, construction=_facts({"x": 0, "y": 8} if source == SOURCE else {}))


@pytest.mark.parametrize("safety", (False, True))
def test_atomic_cross_domain_history_is_observed_and_solved(safety):
    instance = _build(safety=safety)
    rows = _rows(a0=1, b0=2, a1=2, b2=3)
    replay = instance.replay(rows)
    assert not replay.broken and not replay.error
    assert {"monitor:cross:hit", "observation:two-output", "observation:a-changed"} <= set(replay.hits)
    assert "observation:outside" not in replay.hits
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED
    verdict = instance.query(("monitor:cross:hit", "observation:a-changed", "observation:outside"),
                              3, timeout_ms=10_000, workers=1)
    assert not verdict.undecided
    assert {"monitor:cross:hit", "observation:a-changed"} <= verdict.reachable.keys()
    assert verdict.unreachable == {"observation:outside"}
    assert instance.catalogue_manifest()["monitor:cross:hit"].cls == (api.SAFETY if safety else api.COVERAGE)


@pytest.mark.parametrize("order", (("a_out", "b_out"), ("b_out", "a_out")))
def test_all_single_slot_values_match_an_independent_oracle_and_declared_order(order):
    instance = _build(schedule=_schedule(n_slots=1), order=order)
    goal_ids = set(instance.observations.catalogue)
    traces, reachable = [], set()
    for a, b in product(range(4), repeat=2):
        rows = [{"a.__invoke__": 1, "a.a": a, "b.__invoke__": 1, "b.b": b}]
        expected = set()
        if a == 0:
            expected.add("observation:zero-input")
        if b == 2:
            expected.add("observation:two-output")
        if a == 1 and b == 2 and order == ("a_out", "b_out"):
            expected.add("monitor:cross:hit")
        assert set(instance.replay(rows).hits) & goal_ids == expected
        traces.append(rows)
        reachable.update(expected)
    assert instance.audit(traces).status is AuditCheckStatus.AGREED
    verdict = instance.query(tuple(sorted(goal_ids)), 1, timeout_ms=10_000, workers=1)
    assert not verdict.undecided
    assert set(verdict.reachable) == reachable
    assert verdict.unreachable == goal_ids - reachable


@pytest.mark.isolation
def test_atomic_source_audit_reads_independent_outputs_and_monitor_hits():
    instance = _build()
    rows = _rows(a0=1, b0=2, a1=2, b2=3)
    assert instance.source_audit((rows,)).status is AuditCheckStatus.AGREED
    instance._formula.slots[0].hits["monitor:cross:hit"] = z3.BoolVal(False)
    assert instance.audit((rows,)).status is AuditCheckStatus.INCONSISTENT


def test_atomic_failed_slot_keeps_accepted_inputs_but_no_coincident_outputs_or_tail():
    instance = _build(source=ATOMIC_PARTIAL_FAILURE_SOURCE)
    rows = _rows(a0=0, b0=2, a1=1, b2=2)
    replay = instance.replay(rows)
    assert replay.error == "DivZero" and not replay.broken
    assert "observation:zero-input" in replay.hits
    assert "observation:two-output" not in replay.hits
    assert "monitor:cross:hit" not in replay.hits
    assert all(output.count == 0 for output in replay.outputs)
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED


def test_coincident_handlers_still_read_old_state():
    instance = _build(source=SOURCE)
    replay = instance.replay(_rows(a0=1, b0=2, a1=0, b2=3))
    assert not replay.broken and not replay.error
    assert "observation:two-output" in replay.hits  # B reads old x=0, not A's proposed x=10.
    assert "monitor:cross:hit" not in replay.hits
    assert instance.audit((_rows(),)).status is AuditCheckStatus.AGREED


def test_full_reset_and_silent_domains_do_not_erase_history_or_create_outputs():
    base = _schedule((2, 2), n_slots=3)
    slots = list(base.slots)
    reset = slots[1]
    slots[1] = GlobalEventSlot(reset.time, event=api.EventSlot(ticks=("a", "b"), resets=("a", "b"),
        controls={"a_rst": 1, "b_rst": 1, "oa_ready": 1, "ob_ready": 1}))
    instance = _build(schedule=GlobalSchedule(base.timings, slots))
    rows = [
        {"a.__invoke__": 1, "a.a": 1, "b.__invoke__": 0, "b.b": 0},
        {"a.__invoke__": 0, "a.a": 0, "b.__invoke__": 0, "b.b": 0},
        {"a.__invoke__": 0, "a.a": 0, "b.__invoke__": 1, "b.b": 2},
    ]
    assert "monitor:cross:hit" in instance.replay(rows).hits
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED


def test_an_output_batch_is_not_reduced_to_one_monitor_event():
    source = IDENTITY.replace('return {"oa": a}', 'return [{"oa": 0}, {"oa": 1}]')
    instance = _build(source=source)
    rows = _rows(a0=1, b0=2, a1=0, b2=3)
    assert "monitor:cross:hit" in instance.replay(rows).hits
    assert "observation:a-changed" in instance.replay(rows).hits
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED


@pytest.mark.isolation
def test_public_atomic_analysis_adds_history_witness_without_replacing_baseline_score(tmp_path):
    contract = _declarations()
    path = tmp_path / "atomic.py"
    path.write_text(IDENTITY)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": IDENTITY.encode()}, config={"class": "M"})
    realization = api.realization_of_behavior_source(IDENTITY, contract, file="stable/atomic.py")
    task = api.admit(contract, realization, reference=reference)
    generation = api.GenerationSpec(horizon=3, queue_bound=1, reset_hold_cycles=0, drain=0)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="stable/atomic.py", timeout_s=90),
        generation, bounded_analysis=True)
    scope = handle.as_dict()["analysis_request"]
    assert scope["analysis_bound_unit"] == "schedule-slots"
    assert scope["analysis_bound"] == generation.horizon > generation.queue_bound
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command",
        baseline=api.build_baseline(contract, generation, realization=realization,
            policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1)),
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=True, reference=reference,
        feasibility_timeout_ms=30_000, query_timeout_ms=30_000,
        analysis_wall_timeout_seconds=180))
    assert result.status is api.CommandStatus.EVALUATED, result.status
    assert result.decision.authoritative and result.evaluation.passed
    assert "monitor:cross:hit" not in result.evaluation.hits
    assert result.analysis.concrete == result.evaluation.fingerprint
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    outcome = result.analysis.per_goal["monitor:cross:hit"]
    assert outcome["status"] == "reached"
    witness = next(row for row in result.analysis.evidence["witnesses"]
                   if row["witness_ref"] == outcome["support_ref"])
    assert witness["origin"] == "bounded-query"
