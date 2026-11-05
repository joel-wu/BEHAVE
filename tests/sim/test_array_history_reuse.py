"""Immutable history sharing preserves the complete finite-array relation."""

import pytest
import z3

from behave.sim.symbolic import functional_array as arrays
from behave.sim.symbolic.encoder import _Commands
from behave.sim.symbolic.functional_array import FunctionalArray

pytestmark = pytest.mark.solver


def test_literal_history_decodes_each_immutable_address_once(monkeypatch):
    encoder = _Commands(None, {})
    array = FunctionalArray.uniform(128, z3.BitVecVal(0, 16))
    guards = [z3.Bool(f"literal_history_guard_{i}") for i in range(128)]
    for cell, guard in enumerate(guards):
        address = z3.BitVecVal(cell, 9) - z3.BitVecVal(0, 9)
        updated = encoder.array_store(array, address, z3.BitVecVal(cell + 1, 16))
        array = FunctionalArray.choose(guard, updated, array)
    original = z3.BitVecNumRef.as_signed_long
    calls = 0

    def decode(term):
        nonlocal calls
        calls += 1
        return original(term)

    monkeypatch.setattr(z3.BitVecNumRef, "as_signed_long", decode)
    for cell, guard in enumerate(guards):
        actual = array.select(z3.BitVecVal(cell, 16))
        expected = z3.If(guard, z3.BitVecVal(cell + 1, 16), z3.BitVecVal(0, 16))
        solver = z3.Solver()
        solver.add(actual != expected)
        assert solver.check() == z3.unsat
    # Two decodes per read suffice: its bounds and its literal address. The
    # immutable stored addresses need no native conversion on subsequent reads.
    assert calls <= 2 * 128


@pytest.mark.parametrize("private_context", [False, True])
def test_same_width_store_and_choice_share_only_unchanged_history(private_context):
    ctx = z3.Context() if private_context else None
    index, read = z3.BitVecs("history_index history_read", 4, ctx=ctx)
    value = z3.BitVec("history_value", 6, ctx=ctx)
    active, branch = z3.Bools("history_active history_branch", ctx=ctx)
    before = FunctionalArray.uniform(3, z3.BitVecVal(-2, 6, ctx=ctx))
    before = before.store(index, value, active)
    old_read = before.select(read)
    assert before.widen(6) is before
    assert before.widen(6).select(read) is old_read
    updated = before.store(z3.BitVecVal(1, 4, ctx=ctx), value + 1, active)
    assert updated.writes[0] is before.writes[0]
    chosen = FunctionalArray.choose(branch, updated, before)
    assert chosen.writes[0] is before.writes[0]
    # Neither another version nor its read cache can replace the old one.
    assert before.select(read) is old_read
    expected = z3.BitVecVal(0, 6, ctx=ctx)
    for at in range(3):
        cell = z3.If(z3.And(active, index == at), value, z3.BitVecVal(-2, 6, ctx=ctx))
        if at == 1:
            cell = z3.If(z3.And(branch, active), value + 1, cell)
        expected = z3.If(read == at, cell, expected)
    solver = z3.Solver(ctx=ctx)
    solver.add(chosen.select(read) != expected)
    assert solver.check() == z3.unsat


def test_widened_store_and_choice_rebuild_their_signed_carriers():
    guard = z3.Bool("wide_history_guard")
    value = z3.BitVec("wide_history_value", 5)
    original = FunctionalArray.uniform(3, value).store(z3.BitVecVal(1, 4), value, guard)
    widened = original.widen(9)
    assert widened is not original and widened.writes[0] is not original.writes[0]
    updated = original.store(z3.BitVecVal(2, 4), z3.BitVecVal(-100, 9), guard)
    assert updated.writes[0] is not original.writes[0]
    chosen = FunctionalArray.choose(guard, original, updated)
    read = z3.BitVec("wide_history_read", 4)
    expected = z3.If(z3.And(read >= 0, read < 3), z3.SignExt(4, value), z3.BitVecVal(0, 9))
    solver = z3.Solver()
    solver.add(chosen.select(read) != expected)
    assert solver.check() == z3.unsat
    assert original.element_width == 5 and chosen.element_width == 9


def test_shared_base_and_row_identity_need_no_recursive_ast_comparison(monkeypatch):
    original = FunctionalArray.uniform(3, z3.BitVec("same_base", 7))
    original = original.store(z3.BitVec("same_index", 4), z3.BitVec("same_value", 7), z3.Bool("same_guard"))

    def unexpected(*_args):
        raise AssertionError("an immutable node was compared to itself recursively")

    monkeypatch.setattr(z3, "eq", unexpected)
    assert arrays._base_same(original._base, original._base)
    assert arrays._same_write(original.writes[0], original.writes[0])


def test_internal_append_and_merge_do_not_revalidate_old_immutable_rows(monkeypatch):
    first = z3.BitVec("checked_history_old", 9)
    before = FunctionalArray.uniform(8, z3.BitVecVal(0, 9))
    before = before.store(z3.BitVecVal(1, 5), first, z3.BoolVal(True))
    original_size = z3.BitVecRef.size

    def size(term):
        assert term is not first, "an unchanged, validated write was checked again"
        return original_size(term)

    monkeypatch.setattr(z3.BitVecRef, "size", size)
    after = before.store(z3.BitVecVal(2, 5), z3.BitVec("checked_history_new", 9), z3.BoolVal(True))
    merged = FunctionalArray.choose(z3.Bool("checked_history_choice"), after, before)
    assert merged.writes[0] is before.writes[0]


@pytest.mark.parametrize("memory", [False, True])
@pytest.mark.parametrize("bad_row", [
    (1, 0, 0),
    arrays._Write(True, z3.BitVecVal(0, 4), z3.BitVecVal(0, 8)),
    arrays._Write(z3.BoolVal(True), 0, z3.BitVecVal(0, 8)),
    arrays._Write(z3.BoolVal(True), z3.BitVecVal(0, 4), 0),
    arrays._Write(z3.BoolVal(True), z3.BitVecVal(0, 4), z3.BitVecVal(0, 9)),
])
def test_supplied_history_is_still_checked_at_its_construction_boundary(memory, bad_row):
    with pytest.raises(TypeError, match="functional array store"):
        if memory:
            FunctionalArray.memory(3, "mem", 8, 8, [bad_row])
        else:
            FunctionalArray(3, z3.BitVecVal(0, 8), [bad_row])


@pytest.mark.parametrize("base_kind", ["uniform", "memory", "packed", "parameter", "merge"])
def test_reads_use_the_width_established_by_all_base_constructors(monkeypatch, base_kind):
    base = FunctionalArray.uniform(3, z3.BitVec("base_cells", 5))
    if base_kind == "memory":
        base = FunctionalArray.memory(3, "mem", 4, 5)
    elif base_kind == "packed":
        base = FunctionalArray.symbolic(3, "packed", 5, -16, 15)
    elif base_kind == "parameter":
        base = FunctionalArray.parameter(3, "parameter", 5)
    elif base_kind == "merge":
        base = FunctionalArray.merge_bits(base, base, base, 4, signed=False)
    address = z3.BitVec("width_read", 4)
    enabled = z3.Bool("width_enabled")
    # Same-width indices let the test forbid any *cell* widening during read;
    # index normalization is still independently signed and exact.
    updated = base.store(z3.BitVec("width_write", 4), z3.BitVec("width_data", 9), enabled)
    chosen = FunctionalArray.choose(enabled, updated, base)
    expected = chosen.select(address)
    original_widen = arrays._widen

    def widen(left, right):
        assert left.size() == right.size() == 4, "reading a typed cell repeated width inference"
        return original_widen(left, right)

    monkeypatch.setattr(arrays, "_widen", widen)
    actual = chosen._select(address)
    assert actual.size() == 9 and z3.eq(actual, expected)
