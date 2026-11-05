"""Local preparation owns execution facts, not a bounded-analysis certificate."""

from behave.sim.core.source_rules import SourceGoalRules
import ast
from dataclasses import replace
from itertools import product

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.behavior import atomic_exact
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.audit import AuditCheckStatus, AuditedWitness
from behave.sim.core.clock_schedule import GlobalEventSlot, GlobalSchedule
from behave.sim.core.refusal import Refused
from tests.support.atomic_behavior import SOURCE, _contract, _schedule

from .test_behavior_atomic_exact import _facts, _rows
from .test_behavior_atomic_operation_rules import _row, _rules
from .test_behavior_atomic_transactions import IDENTITY, _declarations


def _prepare(*, exact=False, source=SOURCE, values=None):
    build = (atomic_exact.build_atomic_behavior if exact
             else atomic_exact.prepare_atomic_context)
    contract = _contract()
    return build(source, "atomic_context.py", contract, _schedule(),
                 task_contract=contract.digest,
                 construction=_facts({"x": 0, "y": 8} if values is None else values))


def test_context_prepares_real_horizon_without_product_and_assembly_needs_no_solver(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("execution preparation must not build/prove the bounded product")

    with monkeypatch.context() as patch:
        patch.setattr(atomic_exact, "_AtomicFormula", forbidden)
        patch.setattr(atomic_exact, "_encode_atomic_slot", forbidden)
        # Component output/carrier admission is still real work and may use
        # a solver. Only the subsequent context assembly must need no solver.
        contract = _contract()
        prepared = atomic_exact._prepare_atomic_inputs(
            SOURCE, "atomic_context.py", contract, _schedule(),
            construction=_facts({"x": 0, "y": 8}))
        patch.setattr(z3, "Solver", forbidden)
        context = atomic_exact._AtomicContext(**prepared)
    assert len(context.schedule.slots) == 3
    assert all(component.proc.state_range_bound == 3
               for component in context.components.values())
    for capability in ("_formula", "bind", "query", "feasible", "audit_request",
                       "well_formed", "trusted_assumptions"):
        assert not hasattr(context, capability)
    assert not context.replay(_rows()).broken


def test_context_and_full_product_share_manifest_and_independent_old_state_results():
    context, exact = _prepare(), _prepare(exact=True)
    assert context.identity == exact.identity
    assert context.catalogue_manifest() == exact.catalogue_manifest()
    for a0, b0, a1, b2 in product(range(4), repeat=4):
        rows = _rows(a0, b0, a1, b2)
        left = context._semantic_witness(rows, context._domain.digest())
        right = exact._semantic_witness(rows, exact._domain.digest())
        x0, y0 = 8 + a0 + int(a0 == 1), b0
        x1 = y0 + a1 + int(a1 == 1)
        assert left == right
        assert list(left.states) == [
            {"x": 0, "y": 8}, {"x": x0, "y": y0},
            {"x": x1, "y": y0}, {"x": x1, "y": x1 + b2}]


@pytest.mark.isolation
def test_context_source_audit_does_not_need_the_bounded_formula(monkeypatch):
    context = _prepare()
    with monkeypatch.context() as patch:
        patch.setattr(atomic_exact, "_AtomicFormula",
                      lambda *a, **k: pytest.fail("source audit constructed the bounded formula"))
        checked = context.source_audit((_rows(), _rows(0, 3, 1, 2)))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()


def test_context_does_not_turn_an_invalid_product_into_analysis_authority():
    source = '''class M:
    def reset(self):
        pass
    def process_a(self, a):
        return {"oa": 1 // a}
    def process_b(self, b):
        return {"ob": 1 // b}
'''
    context = _prepare(source=source, values={})
    assert not context.replay(_rows(1, 1, 1, 1)).broken
    invalid = context.replay(_rows(0, 0, 1, 1))
    assert invalid.broken and "behavior-atomic-error-collision" in invalid.broken
    with pytest.raises(Refused) as error:
        _prepare(exact=True, source=source, values={})
    assert error.value.refusal.kind == "behavior-atomic-transition-not-total"


@pytest.mark.isolation
def test_audited_slots_supply_the_actual_noninitial_state_to_a_local_query(monkeypatch):
    base = _contract()
    rules = _rules()
    contract = api.TaskContract(base.endpoints, base.clocks, operation_rules=rules)
    schedule = _schedule((2, 2), n_slots=2)

    def no_product(*args, **kwargs):
        pytest.fail("a source-audited local witness must not use a bounded product")

    monkeypatch.setattr(atomic_exact, "_AtomicFormula", no_product)
    context = atomic_exact.prepare_atomic_context(
        SOURCE, "atomic_context.py", contract, schedule,
        construction=_facts({"x": 0, "y": 8}))
    plan = plan_source(source_model(SOURCE, "atomic_context.py"), SOURCE,
                       "atomic_context.py", source_rules=SourceGoalRules(operations=rules))
    expression = next(node for node in ast.walk(ast.parse(SOURCE))
                      if isinstance(node, ast.BinOp)
                      and ast.get_source_segment(SOURCE, node) == "self.x + b")
    goal = next(binding.id for binding in plan.operation_goals
                if binding.site.line == expression.lineno
                and binding.predicate.rule.name == "target")
    observed = []

    def collect(trace, position, before, hits, outputs):
        observed.append((position, dict(before), hits, outputs))

    rows = [_row(), _row()]
    checked = context.source_audit((rows,), observe_step=collect)
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()
    assert [position for position, *_ in observed] == [0, 1]
    assert all(goal not in hits for _, _, hits, _ in observed)
    state = observed[1][1]
    assert state == {"x": 8, "y": 0}
    # This source control has no framing or transaction monitors. Those need
    # their full audited prefix too; this fixture does not manufacture it.
    assert context.observations is None
    assert not any(c.proc.input_framing for c in context.components.values())
    before = replace(atomic_exact._AtomicBoundary.initial(context),
        state={name: atomic_exact._constant(value, context.state_env[name],
               context.state_types[name], name, width=context.scalar_width)
               for name, value in state.items()}, released=frozenset(context.domains))
    step = atomic_exact._encode_atomic_slot(context, before, schedule.slots[1], position=1)
    solver = z3.Solver()
    solver.add(*step.formula.full_constraints)
    solver.add(*(term == rows[1][name] for name, term in step.slot.inputs.items()
                 if name != "b.b"))
    # Invalid-transition obligations are separate from the candidate domain.
    solver.push()
    solver.add(step.invalid)
    assert solver.check() == z3.unsat
    solver.pop()
    solver.add(step.slot.hits[goal])
    assert solver.check() == z3.sat
    candidate = [dict(row) for row in rows]
    candidate[1]["b.b"] = solver.model().eval(step.slot.inputs["b.b"]).as_long()
    assert candidate[1]["b.b"] == 2
    observed.clear()
    confirmed = context.source_audit((candidate,), observe_step=collect)
    assert confirmed.status is AuditCheckStatus.AGREED, confirmed.as_dict()
    assert goal in observed[1][2]


@pytest.mark.isolation
def test_audit_feedback_includes_failure_but_not_its_absorbed_tail():
    source = '''class M:
    def reset(self):
        pass
    def process_a(self, a):
        return {"oa": 1 // a}
    def process_b(self, b):
        return {"ob": b}
'''
    context = _prepare(source=source, values={})
    observed = []
    result = context.source_audit((_rows(1, 2, 0, 3),),
        observe_step=lambda *row: observed.append(row))
    assert result.status is AuditCheckStatus.AGREED, result.as_dict()
    assert [row[1] for row in observed] == [0, 1]
    assert not observed[1][4]  # No coincident output survives a failed slot.
    assert any(context.goals()[goal].cls == api.SAFETY for goal in observed[1][3])


@pytest.mark.isolation
def test_source_ir_disagreement_publishes_no_feedback(monkeypatch):
    context = _prepare()
    semantic = context._semantic_witness

    def wrong(*args, **kwargs):
        run = semantic(*args, **kwargs)
        states = [dict(row) for row in run.states]
        states[1]["x"] += 1
        return AuditedWitness(domain=run.domain, states=states, inputs=run.inputs,
                              outputs=run.outputs, hit_sets=run.hit_sets,
                              terminal_error=run.terminal_error)

    monkeypatch.setattr(context, "_semantic_witness", wrong)
    observed = []
    result = context.source_audit((_rows(),), observe_step=lambda *row: observed.append(row))
    assert result.status is AuditCheckStatus.INCONSISTENT, result.as_dict()
    assert observed == []


@pytest.mark.isolation
@pytest.mark.parametrize("reset", ((), ("a", "b")))
@pytest.mark.parametrize("safety", (False, True))
def test_audited_slot_hits_keep_transaction_history_across_silent_or_reset_slot(reset, safety):
    base = _schedule((2, 2))
    schedule = GlobalSchedule(base.timings, (
        GlobalEventSlot(0, event=api.EventSlot(ticks=("a", "b"), offers=("a_in",))),
        GlobalEventSlot(2, event=api.EventSlot(ticks=("a", "b"), resets=reset)),
        GlobalEventSlot(4, event=api.EventSlot(ticks=("a", "b"), offers=("b_in",)))))
    context = atomic_exact.prepare_atomic_context(
        IDENTITY, "atomic_history.py", _declarations(safety=safety), schedule,
        construction=_facts({}))
    rows = [_row(1, 0, invoke_b=0), _row(invoke_a=0, invoke_b=0),
            _row(0, 2, invoke_a=0)]
    observed = []
    checked = context.source_audit((rows,), observe_step=lambda *row: observed.append(row))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()
    assert [row[1] for row in observed] == [0, 1, 2]
    assert not any("monitor:cross:hit" in row[3] for row in observed[:2])
    assert "monitor:cross:hit" in observed[2][3]
    assert context.goals()["monitor:cross:hit"].cls == (api.SAFETY if safety else api.COVERAGE)
