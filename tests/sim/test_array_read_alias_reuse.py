"""Read-over-write shares alias tests, not states or solver answers."""
import pytest
import z3

from behave.sim.symbolic import functional_array as arrays


@pytest.mark.parametrize("private_context", [False, True])
@pytest.mark.parametrize("mixed_widths", [False, True])
def test_repeated_address_reads_preserve_full_order_and_signed_bounds(private_context, mixed_widths, monkeypatch):
    ctx = z3.Context() if private_context else z3.main_ctx()
    addresses = [z3.BitVec("alias_address", 4, ctx=ctx),
                 z3.BitVecVal(2, 4, ctx=ctx), z3.BitVecVal(-1, 4, ctx=ctx)]
    if mixed_widths:
        addresses += [z3.SignExt(3, addresses[0]), z3.BitVecVal(2, 7, ctx=ctx)]
    array = arrays.FunctionalArray.uniform(5, z3.BitVecVal(-3, 9, ctx=ctx))
    cells = [array.default] * 5
    for i in range(40):
        address = addresses[i % len(addresses)]
        value = z3.BitVec(f"alias_value_{i}", 9, ctx=ctx)
        guard = z3.BoolVal(True, ctx=ctx) if i % 3 == 0 else z3.Bool(f"alias_guard_{i}", ctx=ctx)
        array = array.store(address, value, guard)
        cells = [z3.If(z3.And(guard, address == j), value, old) for j, old in enumerate(cells)]
    calls = []
    original = arrays._same_index

    def counted(left, right):
        calls.append((right.ctx, right.get_id()))
        return original(left, right)

    monkeypatch.setattr(arrays, "_same_index", counted)
    read = z3.BitVec("alias_read", 6, ctx=ctx)
    value = array.select(read)
    assert len(calls) <= len(addresses)
    assert len(calls) == len(set(calls))
    expected = z3.BitVecVal(0, 9, ctx=ctx)
    for i, cell in enumerate(cells):
        expected = z3.If(read == i, cell, expected)
    solver = z3.Solver(ctx=ctx)
    solver.add(value != expected)
    assert solver.check() == z3.unsat


def test_a_new_array_version_or_read_index_does_not_reuse_an_old_answer():
    index = z3.BitVec("versioned_alias", 4)
    base = arrays.FunctionalArray.uniform(5, z3.BitVecVal(0, 8))
    first = base.store(index, z3.BitVecVal(11, 8), z3.BoolVal(True))
    first_read = first.select(z3.BitVecVal(2, 4))
    second = first.store(index, z3.BitVecVal(22, 8), z3.BoolVal(True))
    solver = z3.Solver()
    solver.add(index == 2)
    assert solver.check() == z3.sat
    assert solver.model().eval(first_read).as_long() == 11
    assert solver.model().eval(second.select(z3.BitVecVal(2, 4))).as_long() == 22
    assert solver.model().eval(second.select(z3.BitVecVal(3, 4))).as_long() == 0


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('branches', [2, 8])
def test_one_read_shares_address_predicates_across_version_branches(
        private_context, branches, monkeypatch):
    ctx = z3.Context() if private_context else z3.main_ctx()
    length, width = 5, 9
    write = z3.BitVec('shared_write', 4, ctx=ctx)
    addresses = (write, z3.SignExt(3, write))
    read = z3.BitVec('shared_read', 6, ctx=ctx)
    joined = None
    expected = None
    for i in range(branches):
        default = z3.BitVecVal(i, width, ctx=ctx)
        value = z3.BitVec(f'data_{i}', width, ctx=ctx)
        enabled = z3.Bool(f'enabled_{i}', ctx=ctx)
        address = addresses[i % len(addresses)]
        leaf = arrays.FunctionalArray.uniform(length, default).store(address, value, enabled)
        dense = z3.BitVecVal(0, width, ctx=ctx)
        for cell in range(length):
            dense = z3.If(read == cell, z3.If(z3.And(enabled, address == cell), value, default), dense)
        if joined is None:
            joined, expected = leaf, dense
        else:
            select = z3.Bool(f'branch_{i}', ctx=ctx)
            joined = arrays.FunctionalArray.choose(select, leaf, joined)
            expected = z3.If(select, dense, expected)
    aliases, bounds = [], []
    same_index, in_bounds = arrays._same_index, arrays._in_bounds

    def compare(left, right):
        aliases.append(right.get_id())
        return same_index(left, right)

    def bounded(index, count):
        bounds.append((index.get_id(), count))
        return in_bounds(index, count)

    monkeypatch.setattr(arrays, '_same_index', compare)
    monkeypatch.setattr(arrays, '_in_bounds', bounded)
    result = joined.select(read)
    assert len(aliases) == len(set(aliases)) == len(addresses)
    assert len(bounds) == 1
    solver = z3.Solver(ctx=ctx)
    solver.add(result != expected)
    assert solver.check() == z3.unsat
