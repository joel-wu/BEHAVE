"""Native-prefix local queries; no whole atomic formula is available."""

from dataclasses import replace

import pytest
import z3

from behave.sim import api
from behave.sim.behavior import atomic_exact
from behave.sim.behavior.atomic_selection import AtomicSelectedTrace
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.clock_schedule import GlobalEventSlot, GlobalSchedule
from behave.sim.core.refusal import Refused
from tests.support.atomic_behavior import SOURCE, _contract, _schedule

from .test_behavior_atomic_exact import _facts, _rows
from .test_behavior_atomic_operation_rules import _row, _rules
from .test_behavior_atomic_transactions import IDENTITY, _declarations

pytestmark = [pytest.mark.solver, pytest.mark.isolation]


def _context(monkeypatch, source=IDENTITY, *, contract=None, schedule=None, values=None):
    monkeypatch.setattr(atomic_exact, "_AtomicFormula", lambda *a, **k:
                        pytest.fail("local selection must not construct the whole product"))
    return atomic_exact.prepare_atomic_context(
        source, "atomic_selection.py", contract or _contract(), schedule or _schedule(),
        construction=_facts({} if values is None else values))


def _solver(step, *, row=None, free=()):
    solver = z3.Solver()
    solver.add(*step.formula.full_constraints)
    if row is not None:
        solver.add(*(step.slot.inputs[name] == value for name, value in row.items() if name not in free))
    solver.push()
    solver.add(step.invalid)
    assert solver.check() == z3.unsat
    solver.pop()
    return solver


def test_noninitial_native_state_is_selected_once_and_requests_do_not_replay(monkeypatch):
    base = _contract()
    context = _context(monkeypatch, SOURCE, values={"x": 0, "y": 8},
        contract=api.TaskContract(base.endpoints, base.clocks, operation_rules=_rules()),
        schedule=_schedule((2, 2), n_slots=2))
    selected = AtomicSelectedTrace(context, [_row(), _row()])
    monkeypatch.setattr(context, "source_audit", lambda *a, **k: pytest.fail("query replayed seed"))
    encode_step = atomic_exact.encoder.encode_step
    encoded = []

    def count(*args, **kwargs):
        encoded.append(kwargs["tag"])
        return encode_step(*args, **kwargs)

    monkeypatch.setattr(atomic_exact.encoder, "encode_step", count)
    assert dict(selected.prefixes[1].state) == {"x": 8, "y": 0}
    assert selected.prefixes[1].released == {"a", "b"}
    step = selected.encode(1)
    assert encoded == ["@1.a", "@1.b"]
    solver = _solver(step, row=_row(), free=("b.b",))
    solver.add(step.after.state["y"] == 10)
    assert solver.check() == z3.sat
    assert solver.model().eval(step.slot.inputs["b.b"]).as_long() == 2
    assert len(selected.prefixes) == 2


@pytest.mark.parametrize("reset", ((), ("a", "b")))
@pytest.mark.parametrize("safety", (False, True))
def test_cross_slot_native_monitor_prefix_finds_and_replays_missed_goal(monkeypatch, reset, safety):
    base = _schedule((2, 2))
    schedule = GlobalSchedule(base.timings, (
        GlobalEventSlot(0, event=api.EventSlot(ticks=("a", "b"), offers=("a_in",))),
        GlobalEventSlot(2, event=api.EventSlot(ticks=("a", "b"), resets=reset)),
        GlobalEventSlot(4, event=api.EventSlot(ticks=("a", "b"), offers=("b_in",)))))
    context = _context(monkeypatch, contract=_declarations(safety=safety), schedule=schedule)
    rows = [_row(1, 0, invoke_b=0), _row(invoke_a=0, invoke_b=0), _row(0, 0, invoke_a=0)]
    selected = AtomicSelectedTrace(context, rows)
    goal = "monitor:cross:hit"
    assert goal not in selected.hits
    before = selected.boundary(2)
    assert [z3.is_true(term) for term in before.transactions["automata"]["cross"]] == [False, True]
    step = selected.encode(2)
    solver = _solver(step, row=rows[2], free=("b.b",))
    solver.add(step.slot.hits[goal])
    assert solver.check() == z3.sat
    new_rows = [dict(row) for row in rows]
    new_rows[2]["b.b"] = solver.model().eval(step.slot.inputs["b.b"]).as_long()
    assert new_rows[2]["b.b"] == 2
    confirmed = AtomicSelectedTrace(context, new_rows)
    assert goal in confirmed.prefixes[2].hits
    assert context.goals()[goal].cls == (api.SAFETY if safety else api.COVERAGE)
    # Reinitializing the history would lose this legitimate local witness.
    empty_history = replace(before, transactions=context.observations.initial())
    lost = atomic_exact._encode_atomic_slot(context, empty_history, schedule.slots[2], position=2)
    solver = _solver(lost, row=new_rows[2])
    solver.add(lost.slot.hits[goal])
    assert solver.check() == z3.unsat


FRAMED = '''class M:
    def reset(self):
        pass
    def process_a(self, a, start, end):
        return {"oa": a}
    def process_b(self, b):
        return {"ob": b}
'''


def _framed_contract():
    base = _contract()
    input_ = base.endpoint("a_in")
    fields = tuple(input_.fields) + tuple(api.Field(name, api.FiniteType(1), name)
                                         for name in ("start", "end"))
    framed = api.Endpoint("a_in", "in", fields, input_.adapter, "a",
                         framing=api.InputFraming((api.FrameLevel("start", "end", 3),)))
    return api.TaskContract((framed,) + base.endpoints[1:], base.clocks)


def _framed_row(start, end, **kwargs):
    return {**_row(**kwargs), "a.start": start, "a.end": end}


def test_framing_pins_real_counter_and_preserves_fixed_suffix(monkeypatch):
    context = _context(monkeypatch, FRAMED, contract=_framed_contract(), schedule=_schedule((2, 2)))
    rows = [_framed_row(1, 0), _framed_row(0, 0), _framed_row(0, 1)]
    selected = AtomicSelectedTrace(context, rows)
    assert selected.prefixes[1].frames == {"a_in": (1,)}
    step = selected.encode(1)
    solver = _solver(step, row=rows[1], free=("a.end", "a.a"))
    solver.push()
    solver.add(step.slot.inputs["a.end"] == 1)
    assert solver.check() == z3.unsat  # Closing early invalidates the fixed next start=0.
    solver.pop()
    solver.add(step.slot.inputs["a.a"] == 3)
    assert solver.check() == z3.sat
    candidate = [dict(row) for row in rows]
    candidate[1]["a.a"] = 3
    assert context.source_audit((candidate,)).status is AuditCheckStatus.AGREED
    # The slot alone permits that marker; the suffix restriction is necessary.
    bare = atomic_exact._encode_atomic_slot(context, selected.boundary(1), context.schedule.slots[1], position=1)
    solver = _solver(bare, row={**rows[1], "a.end": 1})
    assert solver.check() == z3.sat


def test_reset_cuts_frame_dependency_but_not_native_transaction_history(monkeypatch):
    base = _schedule((2, 2))
    schedule = GlobalSchedule(base.timings, (base.slots[0],
        GlobalEventSlot(2, event=api.EventSlot(ticks=("a", "b"), resets=("a", "b"))), base.slots[2]))
    context = _context(monkeypatch, FRAMED, contract=_framed_contract(), schedule=schedule)
    rows = [_framed_row(1, 0), _framed_row(0, 0, invoke_a=0, invoke_b=0), _framed_row(1, 1)]
    selected = AtomicSelectedTrace(context, rows)
    assert selected.prefixes[2].frames == {"a_in": (0,)}
    assert not selected.prefixes[2].released
    step = selected.encode(0)
    solver = _solver(step, row={**rows[0], "a.end": 1})
    assert solver.check() == z3.sat


def test_failed_slot_is_queryable_but_absorbed_tail_is_not_a_prefix(monkeypatch):
    source = '''class M:
    def reset(self):
        pass
    def process_a(self, a):
        return {"oa": 1 // a}
    def process_b(self, b):
        return {"ob": b}
'''
    context = _context(monkeypatch, source)
    selected = AtomicSelectedTrace(context, _rows(1, 2, 0, 3))
    assert len(selected.prefixes) == 2
    assert not z3.is_true(selected.boundary(1).halted)
    solver = _solver(selected.encode(1), row=_rows(1, 2, 0, 3)[1])
    assert solver.check() == z3.sat
    with pytest.raises(IndexError):
        selected.boundary(2)


def test_failed_local_slot_does_not_require_an_unexecuted_frame_suffix(monkeypatch):
    source = FRAMED.replace('{"oa": a}', '{"oa": 1 // a}')
    context = _context(monkeypatch, source, contract=_framed_contract(), schedule=_schedule((2, 2)))
    rows = [_framed_row(1, 0), _framed_row(1, 1), _framed_row(1, 1)]
    selected = AtomicSelectedTrace(context, rows)
    assert len(selected.prefixes) == 1
    step = selected.encode(0)
    solver = _solver(step, row=rows[0])
    assert solver.check() == z3.sat  # a=0 fails before the malformed suffix is consumed.
    repaired = {**rows[0], "a.a": 1}
    solver = _solver(step, row=repaired)
    assert solver.check() == z3.unsat  # Repairing failure must also leave the fixed suffix legal.
    assert context.source_audit((rows,)).status is AuditCheckStatus.AGREED


def test_no_selection_survives_source_ir_disagreement(monkeypatch):
    from behave.sim.core.audit import AuditCheck

    context = _context(monkeypatch)
    monkeypatch.setattr(context, "source_audit", lambda *a, **k: AuditCheck.inconsistent("wrong state"))
    with pytest.raises(Refused) as error:
        AtomicSelectedTrace(context, _rows())
    assert error.value.refusal.kind == "behavior-generation-feedback-unverified"


@pytest.mark.parametrize("boolean", (False, True))
def test_native_latency_pending_keys_and_age_are_not_reinitialized(monkeypatch, boolean):
    base = _contract()
    source = IDENTITY
    if boolean:
        endpoints = tuple(api.Endpoint(endpoint.name, endpoint.direction,
            [api.Field(field.name, api.ValueType.boolean(), field.port) for field in endpoint.fields],
            endpoint.adapter, endpoint.clock) if endpoint.direction is api.Direction.OUT else endpoint
            for endpoint in base.endpoints)
        base = api.TaskContract(endpoints, base.clocks)
        source = IDENTITY.replace('{"oa": a}', '{"oa": bool(a & 1)}').replace(
            '{"ob": b}', '{"ob": bool(b & 1)}')
    rule = api.LatencyGoal("cross", api.LatencyEvent("a_out", ("oa",)),
        api.LatencyEvent("b_out", ("ob",)), (api.LatencyBin("one", 1, 1),),
        api.LatencyTimebase("transaction", "a_out"))
    contract = api.TaskContract(base.endpoints, base.clocks, latency_goals=(rule,))
    schedule = _schedule((2, 2), n_slots=3)
    context = _context(monkeypatch, source, contract=contract, schedule=schedule)
    rows = [_row(1, 0, invoke_b=0), _row(2, 0, invoke_b=0), _row(0, 0, invoke_a=0)]
    selected = AtomicSelectedTrace(context, rows)
    goal = "latency:cross:one"
    assert goal not in selected.hits
    history = selected.boundary(2).transactions["latency"]["cross"]
    assert history["counter"].as_long() == 2
    key_value = lambda term: int(z3.is_true(term)) if z3.is_bool(term) else term.as_long()
    assert [(at.as_long(), key_value(key[0])) for live, at, key in history["pending"]
            if z3.is_true(live)] == [(1, 1), (2, 0 if boolean else 2)]
    step = selected.encode(2)
    solver = _solver(step, row=rows[2], free=("b.b",))
    solver.add(step.slot.hits[goal])
    assert solver.check() == z3.sat
    rows[2]["b.b"] = solver.model().eval(step.slot.inputs["b.b"]).as_long()
    assert ((rows[2]["b.b"] & 1) if boolean else rows[2]["b.b"]) == 1
    assert goal in AtomicSelectedTrace(context, rows).prefixes[2].hits
