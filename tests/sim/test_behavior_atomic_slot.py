"""The shared atomic slot relation, before wiring it into public local search.

The finite reference fixtures still prepare a bounded instance. The slot under
test gets only compiled components and a pinned boundary: no schedule, solver,
prefix expansion or access to that bounded formula. Source replay separately
checks the SAT witnesses; agreement with the fold alone is not the oracle.
"""

from dataclasses import replace
from itertools import product
from types import SimpleNamespace

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.behavior import atomic_exact
from behave.sim.symbolic import encoder
from behave.sim.behavior.atomic_exact import _AtomicBoundary, _encode_atomic_slot
from behave.sim.behavior.multiclock import DomainOwnership
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.clock_schedule import GlobalEventSlot, GlobalSchedule
from behave.sim.core.refusal import Refused
from tests.support.atomic_behavior import _contract, _schedule

from .test_behavior_atomic_exact import _facts, _rows
from .test_behavior_atomic_operation_rules import READY_RESET, _build, _row, _rules
from .test_behavior_atomic_transactions import _build as _transactions


def _kernel(instance):
    return SimpleNamespace(**{name: getattr(instance, name) for name in (
        "components", "scalar_width", "initial", "state_env", "state_types",
        "ownership", "error_codes", "occurrence_carriers", "reset_occurrence_carriers",
        "ready_occurrence_carriers", "observations", "external_fields")})


def _read(model, value):
    if z3.is_expr(value):
        return model.eval(value, model_completion=True)
    if isinstance(value, dict):
        return {name: _read(model, item) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_read(model, item) for item in value)
    return value


def _pinned(instance, rows, position):
    # Test-only oracle. Production selection must take an independently audited
    # prefix, not a model of its own query, as evidence for a concrete pre-state.
    solver = z3.Solver()
    solver.add(instance._formula.constraint, instance._formula.constrain_rows(rows))
    assert solver.check() == z3.sat
    model = solver.model()
    boundary = instance._formula.boundaries[position]
    return replace(boundary, state=_read(model, boundary.state),
                   halted=_read(model, boundary.halted), error=_read(model, boundary.error),
                   framing=_read(model, boundary.framing),
                   transactions=_read(model, boundary.transactions))


def _solver(step, row=None):
    solver = z3.Solver()
    solver.add(*step.formula.full_constraints)
    if row is not None:
        solver.add(*(step.slot.inputs[name] == value for name, value in row.items()))
    return solver


def _hits(model, step):
    return {goal for goal, value in step.slot.hits.items() if z3.is_true(_read(model, value))}


def test_local_slot_has_no_prefix_expansion_or_solver_and_reads_coincident_old_state(monkeypatch):
    instance, goals = _build(schedule=_schedule((2, 2), n_slots=2))
    before = _pinned(instance, [_row(), _row()], 1)
    assert {name: value.as_long() for name, value in before.state.items()} == {"x": 8, "y": 0}
    encode = encoder.encode_step
    calls = []

    def counted(*args, **kwargs):
        calls.append(kwargs["tag"])
        return encode(*args, **kwargs)

    def no_solver(*args, **kwargs):
        pytest.fail("a single-slot constructor must not run totality or any other query")

    with monkeypatch.context() as patch:
        patch.setattr(encoder, "encode_step", counted)
        patch.setattr(z3, "Solver", no_solver)
        step = _encode_atomic_slot(_kernel(instance), before, instance.schedule.slots[1], position=1)
    assert calls == ["@1.a", "@1.b"]
    solver = _solver(step)
    for a, b in product(range(4), repeat=2):
        solver.push()
        solver.add(*(step.slot.inputs[name] == value for name, value in _row(a, b).items()))
        assert solver.check() == z3.sat
        model = solver.model()
        assert not z3.is_true(_read(model, step.invalid))
        assert {name: value.as_long() for name, value in _read(model, step.after.state).items()} == {
            "x": a + int(a == 1), "y": 8 + b}
        expected = {goals[("self.y + a", "completed")], goals[("self.x + b", "completed")]}
        if a == 1:
            expected.add(goals[("self.x + 1", "completed")])
        if b == 2:
            expected.add(goals[("self.x + b", "target")])
        assert _hits(model, step) & set(goals.values()) == expected
        solver.pop()
    assert {name: value.as_long() for name, value in before.state.items()} == {"x": 8, "y": 0}


@pytest.mark.isolation
def test_local_sat_replays_a_new_hit_but_local_unsat_does_not_remove_a_goal():
    instance, goals = _build(schedule=_schedule((2, 2), n_slots=2))
    rows = [_row(), _row()]
    catalogue = instance.catalogue_manifest()
    baseline = instance.replay(rows)
    assert instance.source_audit((rows,)).status is AuditCheckStatus.AGREED
    target = goals[("self.x + b", "target")]
    locally_unreachable = goals[("self.y + a", "target")]
    assert {target, locally_unreachable}.isdisjoint(baseline.hits)
    step = _encode_atomic_slot(_kernel(instance), _pinned(instance, rows, 1),
                               instance.schedule.slots[1], position=1)
    solver = _solver(step, {name: value for name, value in rows[1].items() if name != "b.b"})
    solver.add(step.slot.hits[target])
    assert solver.check() == z3.sat
    candidate = [dict(row) for row in rows]
    candidate[1]["b.b"] = _read(solver.model(), step.slot.inputs["b.b"]).as_long()
    assert candidate[0] == rows[0] and candidate[1]["b.b"] == 2
    assert target in instance.replay(candidate).hits
    assert instance.source_audit((candidate,)).status is AuditCheckStatus.AGREED
    solver = _solver(step, {"a.__invoke__": 1, "b.__invoke__": 1})
    solver.add(step.slot.hits[locally_unreachable])
    assert solver.check() == z3.unsat
    verdict = instance.query((locally_unreachable,), 2, timeout_ms=10_000, workers=1)
    assert locally_unreachable in verdict.reachable  # Reachable from another prefix/slot.
    assert instance.catalogue_manifest() == catalogue


@pytest.mark.parametrize("position", (1, 2))
def test_local_failed_slot_and_absorbed_tail_keep_committed_state(position):
    source = '''class M:
    def reset(self):
        self.x = 0
        self.y = 0
    def process_a(self, a):
        self.x = a + 1
        return {"oa": 1 // a}
    def process_b(self, b):
        self.y = b + 1
        return {"ob": b}
'''
    instance, _ = _build(source, values={"x": 0, "y": 0}, schedule=_schedule())
    rows = _rows(a0=1, b0=2, a1=0, b2=3)
    before = _pinned(instance, rows, position)
    step = _encode_atomic_slot(_kernel(instance), before, instance.schedule.slots[position], position=position)
    solver = _solver(step, rows[position])
    assert solver.check() == z3.sat
    model = solver.model()
    assert {name: value.as_long() for name, value in _read(model, step.after.state).items()} == {"x": 2, "y": 3}
    assert z3.is_true(_read(model, step.after.halted))
    assert instance.error_codes[_read(model, step.after.error).as_long()] == "DivZero"
    assert all(not z3.is_true(_read(model, z3.And(z3.Not(step.slot.halted_before), active)))
               for _, active, _ in step.slot.outputs)
    assert bool(_hits(model, step)) == (position == 1)


@pytest.mark.isolation
def test_local_failure_discards_successful_coincident_outputs_but_keeps_source_hits():
    source = '''class M:
    def reset(self):
        self.x = 0
        self.y = 0
    def process_a(self, a):
        self.x = a + 1
        return {"oa": 1 // a}
    def process_b(self, b):
        self.y = b + 1
        return {"ob": b + 1}
'''
    instance, goals = _build(source, values={"x": 0, "y": 0})
    step = _encode_atomic_slot(_kernel(instance), _AtomicBoundary.initial(instance),
                               instance.schedule.slots[0], position=0)
    rows = [_row(0, 2)]
    solver = _solver(step, rows[0])
    assert solver.check() == z3.sat
    model = solver.model()
    assert goals[("b + 1", "completed")] in _hits(model, step)
    assert all(value.as_long() == 0 for value in _read(model, step.after.state).values())
    assert all(not z3.is_true(_read(model, active)) for _, active, _ in step.slot.outputs)
    checked = instance.source_audit((rows,))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()


@pytest.mark.parametrize("reset", (False, True))
def test_local_transaction_monitor_carries_prefix_history_through_reset(reset):
    base = _schedule((2, 2), n_slots=3)
    schedule = GlobalSchedule(base.timings, (
        GlobalEventSlot(0, event=api.EventSlot(ticks=("a", "b"), offers=("a_in",))),
        GlobalEventSlot(2, event=api.EventSlot(ticks=("a", "b"), resets=("a", "b") if reset else ())),
        GlobalEventSlot(4, event=api.EventSlot(ticks=("a", "b"), offers=("b_in",)))))
    instance = _transactions(schedule=schedule)
    rows = [_row(1, 0, invoke_b=0), _row(invoke_a=0, invoke_b=0), _row(invoke_a=0)]
    before = _pinned(instance, rows, 2)
    step = _encode_atomic_slot(_kernel(instance), before, schedule.slots[2], position=2)
    solver = _solver(step, {name: value for name, value in rows[2].items() if name != "b.b"})
    solver.add(step.slot.hits["monitor:cross:hit"])
    assert solver.check() == z3.sat
    assert _read(solver.model(), step.slot.inputs["b.b"]).as_long() == 2
    rows[2]["b.b"] = 2
    assert "monitor:cross:hit" in instance.replay(rows).hits
    # Forgetting the monitor prefix changes SAT to UNSAT, even though model state is empty.
    empty = replace(before, transactions=instance.observations.initial())
    wrong = _encode_atomic_slot(_kernel(instance), empty, schedule.slots[2], position=2)
    solver = _solver(wrong, rows[2])
    solver.add(wrong.slot.hits["monitor:cross:hit"])
    assert solver.check() == z3.unsat


def test_local_readiness_is_observed_even_when_the_handler_cannot_invoke():
    instance, goals = _build(READY_RESET, values={"x": 0, "y": 0},
                             schedule=_schedule((2, 2), n_slots=2), rules=_rules(target=2))
    rows = [_row(1, 0), _row(invoke_a=0)]
    before = _pinned(instance, rows, 1)
    step = _encode_atomic_slot(_kernel(instance), before, instance.schedule.slots[1], position=1)
    solver = _solver(step, rows[1])
    assert solver.check() == z3.sat
    assert goals[("self.x + 1", "target")] in _hits(solver.model(), step)
    solver = _solver(step, _row())
    assert solver.check() == z3.unsat


def test_local_reset_retains_domain_release_history():
    instance, _ = _build(schedule=_schedule((2, 2), n_slots=2))
    before = _pinned(instance, [_row(), _row()], 1)
    reset = GlobalEventSlot(2, event=api.EventSlot(ticks=("a", "b"), resets=("a",)))
    with pytest.raises(Refused, match="reset_<domain>"):
        _encode_atomic_slot(_kernel(instance), before, reset, position=1)
    # Initial per-domain reset is legal without a reset_a handler. It is not the
    # same boundary as an independent reset after a has already run.
    initial = _AtomicBoundary.initial(instance)
    step = _encode_atomic_slot(_kernel(instance), initial, reset, position=1)
    assert _solver(step, _row(invoke_a=0, invoke_b=0)).check() == z3.sat


def test_invalid_ownership_is_reported_not_filtered_out_of_the_input_domain():
    instance, _ = _build()
    kernel = _kernel(instance)
    kernel.ownership = DomainOwnership({"x": ("b",), "y": ("b",)})
    step = _encode_atomic_slot(kernel, _AtomicBoundary.initial(instance),
                               instance.schedule.slots[0], position=0)
    solver = _solver(step, _row(1, 2))
    solver.add(step.invalid)
    assert solver.check() == z3.sat


def test_local_frame_domain_uses_prefix_counters_and_does_not_mutate_them():
    image = _contract().as_dict()
    for endpoint in image["endpoints"]:
        if endpoint["direction"] == "in":
            domain = endpoint["clock"]
            endpoint["fields"].extend(api.Field(f"{domain}_{flag}", api.FiniteType(1),
                f"{domain}_{flag}").as_dict() for flag in ("start", "end"))
            endpoint["framing"] = api.InputFraming((api.FrameLevel(
                f"{domain}_start", f"{domain}_end", 2),)).as_dict()
    contract = api.TaskContract.from_dict(image)
    source = '''class M:
    def reset(self):
        pass
    def process_a(self, a, a_start, a_end):
        return {"oa": a}
    def process_b(self, b, b_start, b_end):
        return {"ob": b}
'''
    instance = atomic_exact.build_atomic_behavior(source, "local_framing.py", contract,
        _schedule((2, 2), n_slots=2), construction=_facts({}))
    rows = []
    for position in range(2):
        rows.append({**_row(), **{f"{d}.{d}_{flag}": int(position == i)
            for d in ("a", "b") for i, flag in enumerate(("start", "end"))}})
    before = _pinned(instance, rows, 1)
    assert before.framing["a"][0][0].as_long() == 1
    step = _encode_atomic_slot(_kernel(instance), before, instance.schedule.slots[1], position=1)
    assert _solver(step, rows[1]).check() == z3.sat
    invalid = {**rows[1], "a.a_start": 1}
    assert _solver(step, invalid).check() == z3.unsat
    assert before.framing["a"][0][0].as_long() == 1
    assert not instance.replay(rows).broken
    assert instance.replay([rows[0], invalid]).broken
