"""Compact memory boundaries agree with native old-state/reset semantics."""

import pytest
import z3

from behave.sim import api
from behave.sim.behavior import atomic_exact
from behave.sim.symbolic import typecheck as types
from behave.sim.behavior.atomic_selection import AtomicSelectedTrace
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.behavior.multiclock import DomainOwnership, atomic_effect, atomic_successor
from behave.sim.symbolic.uniform_array import UniformArray
from behave.sim.behavior.value_records import canonical_memory, domain_tree
from behave.sim.core.clock_schedule import GlobalEventSlot, GlobalSchedule
from behave.sim.core.memory import MemoryEnvironment, MemoryInterface, MemoryPort, MemoryRun
from tests.support.atomic_behavior import _contract, _schedule

from .test_behavior_atomic_exact import _facts
from .test_behavior_atomic_operation_rules import _row
from .test_behavior_atomic_selection import _solver


def test_compact_boundary_does_not_enumerate_memory(monkeypatch):
    def dense(*args, **kwargs):
        pytest.fail("compact boundary must not enumerate the address space")

    monkeypatch.setattr(UniformArray, "__getitem__", dense)
    monkeypatch.setattr(UniformArray, "materialize", dense)
    memory = UniformArray.memory(1 << 20, "ram", 8, {13: 7, 999: 251})
    bounds = atomic_exact._initial_range(memory)
    assert (bounds.lo, bounds.hi) == (0, 255)
    encoded = atomic_exact._constant(memory, bounds, types.Array(len(memory), types.INTEGER), "ram")
    assert type(encoded) is FunctionalArray
    assert len(encoded.writes) == 2
    image = domain_tree(canonical_memory(memory, memory))
    assert len(image["writes"]) <= 2
    solver = z3.Solver()
    solver.add(encoded.select(z3.BitVecVal(999, 22)) != 251)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("same_address, identical", [(False, False), (True, False), (True, True)])
def test_sparse_atomic_commit_matches_scalar_cells(same_address, identical):
    old = UniformArray.memory(1 << 20, "ram", 8, {13: 0, 999: 0})
    left_index, right_index = 13, 13 if same_address else 999
    left = old.store(left_index, 7)
    right = old.store(right_index, 7 if identical else 9)
    effects = [atomic_effect("a", {"ram": old}, {"ram": left}),
               atomic_effect("b", {"ram": old}, {"ram": right})]
    assert effects[0].writes == ((("ram", left_index), 7),)
    ownership = DomainOwnership({"ram": ("a", "b")}, {"ram": "identical"} if identical else {})
    slot = _schedule((2, 2), n_slots=1).slots[0]
    if same_address and not identical:
        with pytest.raises(api.Refused, match="multiclock-write-collision"):
            atomic_successor({"ram": old}, effects, ownership, slot)
    else:
        actual = atomic_successor({"ram": old}, effects, ownership, slot)["ram"]
        assert actual[left_index] == 7
        assert actual[right_index] == (7 if identical else 9)

    invalid = []
    symbolic = atomic_exact._merge_atomic_leaf(FunctionalArray.from_uniform(old, 10),
        [(z3.BoolVal(True), FunctionalArray.from_uniform(left, 10)),
         (z3.BoolVal(True), FunctionalArray.from_uniform(right, 10))],
        identical=identical, halted=z3.BoolVal(False), invalid=invalid)
    solver = z3.Solver()
    solver.add(z3.Or(invalid))
    assert solver.check() == (z3.sat if same_address and not identical else z3.unsat)
    if not same_address or identical:
        solver = z3.Solver()
        solver.add(z3.Or(symbolic.select(z3.BitVecVal(left_index, 22)) != 7,
                        symbolic.select(z3.BitVecVal(right_index, 22)) != (7 if identical else 9)))
        assert solver.check() == z3.unsat


SOURCE = '''class M:
    def reset(self):
        self.ram = [0] * 4
    def reset_a(self):
        self.ram = [0] * 4
    def process_a(self, a):
        self.ram[a] = a + 4
        return {"oa": a}
    def process_b(self, b):
        if self.ram[1] == 5 and b == 2:
            return {"ob": 9}
        return {"ob": self.ram[b]}
'''


def _memory_context(monkeypatch, reset):
    monkeypatch.setattr(atomic_exact, "_AtomicFormula", lambda *a, **k:
                        pytest.fail("local memory selection constructed a whole formula"))
    base = _contract()
    memory = MemoryInterface("ram", 2, 4, "a", [MemoryPort("rw")])
    contract = api.TaskContract(base.endpoints, base.clocks, memories=(memory,))
    environment = MemoryEnvironment([MemoryRun("ram", initial={0: 0, 1: 0, 2: 7, 3: 0},
                                              ready={None: [1] * 3})])
    base_schedule = _schedule((2, 2))
    schedule = GlobalSchedule(base_schedule.timings, (base_schedule.slots[0],
        GlobalEventSlot(2, event=api.EventSlot(ticks=("a", "b"), resets=reset)),
        base_schedule.slots[2]))
    return atomic_exact.prepare_atomic_context(SOURCE, "atomic_memory.py", contract, schedule,
        construction=_facts({"ram": [0] * 4}), memory_environment=environment, memory_horizon=3)


@pytest.mark.solver
@pytest.mark.isolation
@pytest.mark.parametrize("reset", [(), ("a", "b"), ("a",)])
def test_native_memory_prefix_survives_reset_and_drives_new_hit(monkeypatch, reset):
    context = _memory_context(monkeypatch, reset)
    rows = [_row(1, 0, invoke_b=0), _row(invoke_a=0, invoke_b=0), _row(0, 0, invoke_a=0)]
    selected = AtomicSelectedTrace(context, rows)
    memory = selected.prefixes[2].state["ram"]
    assert type(memory) is UniformArray
    assert memory[1] == 5 and memory[2] == 7
    reset_step = selected.encode(1)
    solver = _solver(reset_step, row=rows[1])
    solver.add(reset_step.after.state["ram"].select(z3.BitVecVal(1, 4)) != 5)
    assert solver.check() == z3.unsat
    step = selected.encode(2)
    solver = _solver(step, row=rows[2], free=("b.b",))
    residual = [condition for goal, condition in step.slot.hits.items()
                if goal not in selected.hits and context.goals()[goal].cls == api.COVERAGE]
    solver.add(step.slot.inputs["b.b"] == 2, z3.Or(residual))
    assert solver.check() == z3.sat
    candidate = [dict(row) for row in rows]
    candidate[2]["b.b"] = solver.model().eval(step.slot.inputs["b.b"]).as_long()
    confirmed = AtomicSelectedTrace(context, candidate)
    assert confirmed.hits - selected.hits
    assert confirmed.prefixes[2].state["ram"][1] == 5


def test_symbolic_memory_addresses_keep_native_collision_semantics():
    old = UniformArray.uniform(4, 0)
    before = FunctionalArray.from_uniform(old, 5)
    a, b = z3.BitVecs("memory-address-a memory-address-b", 4)
    invalid = []
    merged = atomic_exact._merge_atomic_leaf(before, [
        (z3.BoolVal(True), before.store(a, z3.BitVecVal(7, 5), z3.BoolVal(True))),
        (z3.BoolVal(True), before.store(b, z3.BitVecVal(9, 5), z3.BoolVal(True)))],
        identical=False, halted=z3.BoolVal(False), invalid=invalid)
    for left in range(4):
        for right in range(4):
            solver = z3.Solver()
            solver.add(a == left, b == right, z3.Or(invalid) != (left == right))
            assert solver.check() == z3.unsat
            if left != right:
                solver = z3.Solver()
                solver.add(a == left, b == right, z3.Or([
                    merged.select(z3.BitVecVal(index, 4)) != (7 if index == left else 9 if index == right else 0)
                    for index in range(4)]))
                assert solver.check() == z3.unsat


@pytest.mark.solver
@pytest.mark.isolation
def test_coincident_memory_read_sees_old_value(monkeypatch):
    context = _memory_context(monkeypatch, ())
    rows = [_row(1, 1), _row(invoke_a=0, invoke_b=0), _row(0, 1, invoke_a=0)]
    observed = []
    checked = context.source_audit((rows,), observe_step=lambda *row: observed.append(row))
    from behave.sim.core.audit import AuditCheckStatus

    assert checked.status is AuditCheckStatus.AGREED
    assert {"b_out": {"ob": 0}} in observed[0][4]
    assert {"b_out": {"ob": 5}} in observed[2][4]


@pytest.mark.solver
@pytest.mark.isolation
def test_exact_memory_fold_and_native_audit_share_reset_contents(monkeypatch):
    formula_type = atomic_exact._AtomicFormula
    context = _memory_context(monkeypatch, ("a", "b"))
    rows = [_row(1, 1), _row(invoke_a=0, invoke_b=0), _row(0, 2, invoke_a=0)]
    formula = formula_type(context)
    solver = z3.Solver()
    solver.add(formula.constraint, formula.constrain_rows(rows))
    assert solver.check() == z3.sat
    witness = formula.decode_witness(solver.model(), context, rows, context._domain.digest())
    assert witness == context._semantic_witness(rows, context._domain.digest())
    from behave.sim.core.audit import AuditCheckStatus

    assert context.source_audit((rows,)).status is AuditCheckStatus.AGREED


def test_memory_record_is_extensional_but_does_not_replace_write_evidence():
    template = UniformArray.memory(4, "ram", 4, {1: 7})
    restored = template.store(2, template.base_value(2)).store(1, 7)
    compact = domain_tree(canonical_memory(restored, template))
    dense = domain_tree(canonical_memory(restored.materialize(), template))
    assert compact == dense == domain_tree(canonical_memory(template, template))
    # This is contents equality, deliberately not a claim that no writes took place.
    assert len(restored.sparse_items()) > len(template.sparse_items())
