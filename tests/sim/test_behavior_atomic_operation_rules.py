"""Operation predicates observe old-state handlers, not a serial approximation."""

from behave.sim.core.source_rules import SourceGoalRules
import ast
from itertools import product

import pytest

pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.behavior.atomic_exact import build_atomic_behavior
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.clock_schedule import GlobalEventSlot, GlobalSchedule
from tests.support.atomic_behavior import SOURCE, _contract, _schedule

from .test_behavior_atomic_exact import _facts
from .test_command_handler import _BaselineOnly


def _rules(operator="add", target=10):
    return (
        api.OperationRule(operator, api.OperationGoal("completed", api.GoalCondition("eq", 0, 0))),
        api.OperationRule(operator, api.OperationGoal("target", api.GoalCondition(
            "eq", api.GoalField("result"), target))),
        api.OperationRule(operator, api.OperationGoal("outside", api.GoalCondition(
            "eq", api.GoalField("result"), 300))),
    )


def _build(source=SOURCE, *, values=None, schedule=None, rules=None):
    rules = _rules() if rules is None else rules
    base = _contract()
    contract = api.TaskContract(base.endpoints, base.clocks, operation_rules=rules)
    instance = build_atomic_behavior(
        source, "atomic_operations.py", contract, schedule or _schedule(n_slots=1),
        construction=_facts({"x": 0, "y": 8} if values is None else values))
    plan = plan_source(source_model(source, file="atomic_operations.py"), source,
                       "atomic_operations.py", source_rules=SourceGoalRules(operations=rules))
    expressions = {
        (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset):
            ast.get_source_segment(source, node)
        for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.BinOp, ast.UnaryOp))
    }
    goals = {
        (expressions[(binding.site.line, binding.site.col, binding.site.end_line,
                      binding.site.end_col)], binding.predicate.rule.name): binding.id
        for binding in plan.operation_goals
    }
    assert instance.frontend_catalogue_manifest() == plan.manifest
    return instance, goals


def _row(a=0, b=0, *, invoke_a=1, invoke_b=1):
    return {"a.__invoke__": invoke_a, "a.a": a, "b.__invoke__": invoke_b, "b.b": b}


def test_atomic_operation_queries_agree_with_all_inputs_and_old_state_oracle():
    instance, goals = _build()
    traces, reachable = [], set()
    for a, b in product(range(4), repeat=2):
        rows = [_row(a, b)]
        replay, witness = instance.replay_witness(rows, instance.bind().domain.digest())
        assert not replay.broken and not replay.error
        assert witness.states[-1] == {"x": 8 + a + int(a == 1), "y": b}
        # Both handlers see initial (x=0,y=8). A serial a→b execution would
        # make b's addition observe the new x and change both output and hits.
        values = {"self.y + a": 8 + a, "self.x + b": b}
        if a == 1:
            values["self.x + 1"] = 10
        expected = {goals[(expr, "completed")] for expr in values}
        expected.update(goals[(expr, "target")] for expr, value in values.items() if value == 10)
        assert set(replay.hits) & set(goals.values()) == expected
        traces.append(rows)
        reachable.update(expected)
    assert instance.audit(traces).status is AuditCheckStatus.AGREED
    verdict = instance.query(tuple(goals.values()), 1, timeout_ms=10_000, workers=1)
    assert not verdict.undecided
    assert set(verdict.reachable) == reachable
    assert verdict.unreachable == set(goals.values()) - reachable
    # This is a fixed one-slot domain, not a global source unreachability claim.
    assert set(goals.values()) <= set(instance.catalogue_manifest())
    for goal, trace in verdict.reachable.items():
        assert goal in instance.replay(trace).hits


@pytest.mark.isolation
def test_atomic_operation_witnesses_replay_in_the_isolated_original_source():
    instance, goals = _build()
    target = goals[("self.x + 1", "target")]
    verdict = instance.query((target,), 1, timeout_ms=10_000, workers=1)
    assert not verdict.undecided and target in verdict.reachable
    witness = verdict.reachable[target]
    assert instance.source_audit((witness,)).status is AuditCheckStatus.AGREED


READY_RESET = '''class M:
    def reset(self):
        self.x = 0
        self.y = 0
    def reset_a(self):
        self.x = 1 + 0
    def reset_b(self):
        self.y = 1 + 0
    def a_ready(self):
        return (self.x + 1) < 2
    def process_a(self, a):
        self.x = self.x + a
        return {"oa": self.x}
    def process_b(self, b):
        self.y = self.y + b
        return {"ob": self.y}
'''


@pytest.mark.isolation
def test_ready_hits_do_not_require_handler_invocation_and_reset_is_not_an_operation_site():
    base = _schedule(n_slots=5)
    schedule = GlobalSchedule(base.timings, (
        GlobalEventSlot(0, event=api.EventSlot(ticks=("a", "b"), resets=("a", "b"))),
        GlobalEventSlot(2, event=api.EventSlot(ticks=("a",), offers=("a_in",))),
        GlobalEventSlot(3, event=api.EventSlot(ticks=("b",), offers=("b_in",))),
        GlobalEventSlot(4, event=api.EventSlot(ticks=("a",))),
        GlobalEventSlot(6, event=api.EventSlot(ticks=("a", "b"), resets=("b",), offers=("a_in",))),
    ))
    instance, goals = _build(READY_RESET, values={"x": 0, "y": 0},
                             schedule=schedule, rules=_rules(target=1))
    assert not any(expr == "1 + 0" for expr, _ in goals)
    # Full reset restores (0,0). A's first input sets x=1. Subsequent a_ready
    # calls are false but their completed additions still have observations.
    rows = [_row(invoke_a=0, invoke_b=0), _row(1, invoke_b=0),
            _row(0, 1, invoke_a=0), _row(invoke_a=0, invoke_b=0),
            _row(invoke_a=0, invoke_b=0)]
    replay, witness = instance.replay_witness(rows, instance.bind().domain.digest())
    assert not replay.broken and not replay.error
    hits = [set(row) & set(goals.values()) for row in witness.hit_sets]
    ready = goals[("self.x + 1", "completed")]
    a = goals[("self.x + a", "completed")]
    b = goals[("self.y + b", "completed")]
    assert hits == [set(), {ready, a, goals[("self.x + 1", "target")],
                           goals[("self.x + a", "target")]},
                    {b, goals[("self.y + b", "target")]}, {ready}, {ready}]
    assert witness.states[-1] == {"x": 1, "y": 1}
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED
    checked = instance.source_audit((rows,))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()


@pytest.mark.isolation
def test_failed_operation_has_safety_evidence_but_no_completion_hit():
    source = '''class M:
    def reset(self):
        pass
    def process_a(self, a):
        return {"oa": 1 // a}
    def process_b(self, b):
        return {"ob": 1 // (b + 1)}
'''
    instance, goals = _build(source, values={}, rules=_rules("floor-divide", target=1))
    rows = [_row(0, 0)]
    replay = instance.replay(rows)
    assert replay.error and not replay.broken
    assert goals[("1 // a", "completed")] not in replay.hits
    assert goals[("1 // (b + 1)", "completed")] in replay.hits
    assert any(instance.catalogue_manifest()[goal].cls == api.SAFETY for goal in replay.hits)
    assert instance.audit((rows,)).status is AuditCheckStatus.AGREED
    checked = instance.source_audit((rows,))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()


@pytest.mark.isolation
def test_public_atomic_analysis_finds_operation_witness_without_rewriting_baseline_score(tmp_path):
    source = '''class M:
    def reset(self):
        pass
    def process_a(self, a):
        return {"oa": a + 1}
    def process_b(self, b):
        return {"ob": b + 1}
'''
    base = _contract()
    contract = api.TaskContract(base.endpoints, base.clocks, operation_rules=_rules(target=3))
    path = tmp_path / "atomic.py"
    path.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source.encode()}, config={"class": "M"})
    realization = api.realization_of_behavior_source(source, contract, file="stable/atomic.py")
    task = api.admit(contract, realization, reference=reference)
    generation = api.GenerationSpec(horizon=3, queue_bound=1, reset_hold_cycles=0, drain=0)
    handle = api.prepare_behavior(task,
        api.BehaviorConfig(path, file="stable/atomic.py", timeout_s=90),
        generation, bounded_analysis=True)
    plan = plan_source(source_model(source, file="stable/atomic.py"), source,
                       "stable/atomic.py", source_rules=SourceGoalRules(operations=contract.operation_rules))
    assert handle.frontend_catalogue == plan.manifest
    targets = {binding.id for binding in plan.operation_goals
               if binding.predicate.rule.name == "target"}
    outside = {binding.id for binding in plan.operation_goals
               if binding.predicate.rule.name == "outside"}
    assert len(targets) == len(outside) == 2
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command",
        baseline=api.build_baseline(contract, generation, realization=realization,
            policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1)),
        providers=(_BaselineOnly(),), probe_budget=0, run_analysis=True, reference=reference,
        feasibility_timeout_ms=30_000, query_timeout_ms=30_000,
        analysis_wall_timeout_seconds=180))
    assert result.status is api.CommandStatus.EVALUATED
    assert result.decision.authoritative and result.evaluation.passed and result.evaluation.complete
    assert targets.isdisjoint(result.evaluation.hits)
    assert result.analysis.concrete == result.evaluation.fingerprint
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    for goal in targets:
        outcome = result.analysis.per_goal[goal]
        assert outcome["status"] == "reached"
        witness = next(row for row in result.analysis.evidence["witnesses"]
                       if row["witness_ref"] == outcome["support_ref"])
        assert witness["origin"] == "bounded-query"
    assert outside <= set(result.evaluation.catalogue)
