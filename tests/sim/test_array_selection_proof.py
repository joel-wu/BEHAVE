"""Shared selection proofs preserve the original finite-array formula."""

import time

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import ir
from behave.sim.symbolic.closure_proof import (
    _StructuralProofLimit,
    _StructuralProofWork,
    _SyntacticClosureProof,
)
from behave.sim.symbolic.encoder import _Encoder
from behave.sim.symbolic.functional_array import FunctionalArray, _ArraySelection
from behave.sim.symbolic.ranges import Range


@pytest.mark.parametrize("base", ["uniform", "packed", "memory", "choice"])
@pytest.mark.parametrize("wide_write", [False, True])
def test_source_union_bounds_the_unchanged_selection(base, wide_write):
    if base == "uniform":
        array = FunctionalArray.uniform(3, z3.BitVecVal(-2, 8))
    elif base == "packed":
        array = FunctionalArray.symbolic(3, "packed", 8, 0, 7)
    elif base == "memory":
        array = FunctionalArray.memory(3, "memory", 5, 8)
    else:
        array = FunctionalArray.choose(
            z3.Bool("choose"),
            FunctionalArray.symbolic(3, "left", 8, 0, 7),
            FunctionalArray.uniform(3, z3.BitVecVal(-2, 8)),
        )
    write_at = z3.BitVec("write_at", 5)
    enabled = z3.Bool("enabled")
    value = z3.BitVecVal(-257 if wide_write else 12, 12 if wide_write else 8)
    original = array
    array = array.store(write_at, value, enabled)
    read_at = z3.BitVec("read_at", 5)
    selection = _ArraySelection(array, read_at)
    assert z3.eq(selection.term, array.select(read_at))
    proof = _SyntacticClosureProof({}, array_selections=(selection,))
    hull = proof.coarse_signed_hull(selection.term)

    # Independent componentwise update/selection, including both invalid reads
    # and invalid writes. The proof adds no restriction on index or guard.
    width = array.element_width
    dense = []
    for index in range(3):
        old = original.select(z3.BitVecVal(index, 5))
        if old.size() < width:
            old = z3.SignExt(width - old.size(), old)
        dense.append(z3.If(z3.And(enabled, write_at == index), value, old))
    expected = z3.BitVecVal(0, width)
    for index, cell in enumerate(dense):
        expected = z3.If(read_at == index, cell, expected)
    solver = z3.SolverFor("QF_BV")
    solver.set(timeout=10_000)
    solver.add(z3.Or(selection.term != expected,
                     selection.term < hull.lo, selection.term > hull.hi))
    assert solver.check() == z3.unsat


def test_zero_is_retained_even_when_all_array_cells_are_positive():
    array = FunctionalArray.uniform(4, z3.BitVecVal(7, 8))
    selection = _ArraySelection(array, z3.BitVec("index", 8))
    proof = _SyntacticClosureProof({}, array_selections=(selection,))
    assert proof.coarse_signed_hull(selection.term) == Range(0, 7)
    solver = z3.Solver()
    solver.add(selection.term == 0)
    assert solver.check() == z3.sat


def test_narrow_obligation_keeps_the_precise_original_term_proof():
    # A nonliteral impossible guard still exercises the precise proof. A
    # literal False is now eliminated during exact read-over-write folding.
    enabled = z3.Bool("narrow_obligation_enabled")
    array = FunctionalArray.uniform(2, z3.BitVecVal(7, 8)).store(
        z3.BitVecVal(0, 8), z3.BitVecVal(-30, 8),
        z3.And(enabled, z3.Not(enabled)))
    selection = _ArraySelection(array, z3.BitVecVal(0, 8))
    proof = _SyntacticClosureProof({}, array_selections=(selection,))
    assert proof.coarse_signed_hull(selection.term) == Range(-30, 7)
    assert proof.signed_hull(selection.term) == Range(7, 7)


def test_source_version_is_immutable_and_later_bad_writes_are_not_certified():
    good = FunctionalArray.uniform(4, z3.BitVecVal(0, 10))
    bad = good.store(z3.BitVecVal(2, 8), z3.BitVecVal(300, 10), z3.BoolVal(True))
    index = z3.BitVec("index", 8)
    first, second = _ArraySelection(good, index), _ArraySelection(bad, index)
    proof = _SyntacticClosureProof({}, array_selections=(first, second))
    assert proof.coarse_signed_hull(first.term) == Range(0, 0)
    assert proof.coarse_signed_hull(second.term) == Range(0, 300)
    solver = z3.Solver()
    solver.add(second.term > 255)
    assert solver.check() == z3.sat


def test_encoder_retains_derivation_across_ssa_binding():
    array = FunctionalArray.uniform(4, z3.BitVecVal(9, 8))
    encoder = _Encoder(None, {"index": Range(-1, 4)})
    term = encoder.expr(ir.Sel(ir.Var("array"), ir.Var("index")),
                        {"array": array, "index": z3.BitVec("index", 8)})
    named = encoder.bind(term)
    proof = _SyntacticClosureProof(
        {row.lhs.get_id(): row.rhs for row in encoder.definitions},
        array_selections=encoder.array_selections,
    )
    assert len(encoder.array_selections) == 1
    assert z3.eq(encoder.array_selections[0].term, term)
    assert proof.coarse_signed_hull(named) == Range(0, 9)


@pytest.mark.parametrize("capacity", [128, 512])
def test_shared_proof_work_is_additive_not_capacity_times_history(capacity):
    history = 32
    array = FunctionalArray.uniform(capacity, z3.BitVecVal(0, 16))
    for index in range(history):
        array = array.store(
            z3.BitVec(f"store_index_{index}", 16),
            z3.BitVec(f"data_{index}", 16) & 255,
            z3.Bool(f"store_guard_{index}"),
        )
    selections = tuple(_ArraySelection(array, z3.BitVecVal(index, 16))
                       for index in range(capacity))
    budget = 4 * capacity + 8 * history
    work = _StructuralProofWork(budget, time.monotonic() + 30)
    proof = _SyntacticClosureProof({}, work=work, array_selections=selections)
    assert all(proof.coarse_signed_hull(row.term) == Range(0, 255)
               for row in selections)
    assert len(proof.array_hulls) == 1
    assert budget - work.remaining <= 2 * capacity + 4 * history + 2
    # This is a structural-work assertion, not a flaky wall-clock threshold.
    ordinary = _SyntacticClosureProof(
        {}, work=_StructuralProofWork(budget, time.monotonic() + 30))
    with pytest.raises(_StructuralProofLimit):
        for row in selections:
            ordinary.coarse_signed_hull(row.term)


def test_no_independent_term_or_hull_can_be_supplied_as_selection_evidence():
    with pytest.raises(TypeError):
        _SyntacticClosureProof({}, array_selections=((z3.BitVec("forged", 8), Range(0, 0)),))
