"""Factoring consecutive write guards preserves aliases and store order."""
import pytest
import z3

from behave.sim.symbolic.functional_array import FunctionalArray


@pytest.mark.parametrize("private_context", [False, True])
@pytest.mark.parametrize("read_kind", ["symbolic", "literal", "negative"])
def test_guard_groups_preserve_dense_array_semantics(private_context, read_kind):
    ctx = z3.Context() if private_context else z3.main_ctx()
    bits = lambda value, width=7: z3.BitVecVal(value, width, ctx=ctx)
    a, b = z3.Bools("group_a group_b", ctx=ctx)
    x, y = z3.BitVecs("group_x group_y", 4, ctx=ctx)
    array = FunctionalArray.uniform(5, bits(-3))
    cells = [bits(-3)] * 5
    # The separated a blocks must not commute across b. Repeated symbolic
    # aliases, signed indices, different widths and an unconditional store
    # retain their original last-write-wins semantics.
    rows = [(a, x, 4), (a, bits(1, 4), -7), (a, y, 5),
            (b, y, 10), (b, x, -1), (a, x, 8), (a, bits(2, 4), 12),
            (z3.BoolVal(True, ctx=ctx), x, 19),
            (a, z3.SignExt(3, x), -20), (a, bits(-1, 7), 31)]
    for guard, index, value in rows:
        array = array.store(index, bits(value), guard)
        cells = [z3.If(z3.And(guard, index == i), bits(value), old)
                 for i, old in enumerate(cells)]
    read = (z3.BitVec("group_read", 6, ctx=ctx) if read_kind == "symbolic"
            else bits(1 if read_kind == "literal" else -1, 6))
    expected = bits(0)
    for i, value in enumerate(cells):
        expected = z3.If(read == i, value, expected)
    solver = z3.Solver(ctx=ctx)
    solver.add(array.select(read) != expected)
    assert solver.check() == z3.unsat


def test_a_shared_guard_is_not_reconstructed_per_written_cell(monkeypatch):
    array = FunctionalArray.uniform(32, z3.BitVecVal(0, 8))
    guard = z3.Bool("bulk_write_guard")
    for i in range(32):
        array = array.store(z3.BitVecVal(i, 7), z3.BitVecVal(i + 1, 8), guard)
    original = z3.And
    calls = []

    def conjunction(*terms):
        calls.append(terms)
        return original(*terms)

    monkeypatch.setattr(z3, "And", conjunction)
    array.select(z3.BitVec("bulk_read", 7))
    # Only the signed finite-domain bound needs a conjunction. The guard
    # wraps the complete ordered write block, not each of its 32 addresses.
    assert len(calls) == 1


def test_an_unconditional_alias_still_shadows_the_old_base(monkeypatch):
    import behave.sim.symbolic.functional_array as arrays

    index = z3.BitVecVal(2, 4)
    array = FunctionalArray.memory(5, "shadowed_memory", 8, 8)
    array = array.store(index, z3.BitVecVal(11, 8), z3.BoolVal(True))
    array = array.store(index, z3.BitVecVal(22, 8), z3.Bool("later_write"))

    def unexpected(*_args):
        raise AssertionError("a shadowed memory base was expanded")

    monkeypatch.setattr(arrays, "_base_leaf_select", unexpected)
    solver = z3.Solver()
    solver.add(array.select(index) != z3.If(z3.Bool("later_write"),
                                          z3.BitVecVal(22, 8), z3.BitVecVal(11, 8)))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("private_context", [False, True])
@pytest.mark.parametrize("variant", ["complete", "partial", "different_value", "different_guard"])
def test_fill_recognition_requires_a_complete_uniform_guarded_domain(private_context, variant):
    ctx = z3.Context() if private_context else z3.main_ctx()
    guard, other = z3.Bools("fill_guard fill_other", ctx=ctx)
    value = z3.BitVec("fill_value", 8, ctx=ctx)
    array = FunctionalArray.uniform(5, z3.BitVecVal(-13, 8, ctx=ctx))
    cells = [array.default] * 5
    for i in range(4 if variant == "partial" else 5):
        g = other if variant == "different_guard" and i == 2 else guard
        v = value + 1 if variant == "different_value" and i == 2 else value
        array = array.store(z3.BitVecVal(i, 4, ctx=ctx), v, g)
        cells[i] = z3.If(g, v, cells[i])
    fill = any(not rows for _, rows in array._read_history())
    assert fill == (variant == "complete")
    read = z3.BitVec("fill_read", 7, ctx=ctx)
    expected = z3.BitVecVal(0, 8, ctx=ctx)
    for i, cell in enumerate(cells):
        expected = z3.If(read == i, cell, expected)
    solver = z3.Solver(ctx=ctx)
    solver.add(array.select(read) != expected)
    assert solver.check() == z3.unsat


def test_fill_then_alias_write_then_fill_keeps_all_branches_and_old_versions():
    a, b = z3.Bools("first_fill second_fill")
    index = z3.BitVec("fill_write_index", 5)
    read = z3.BitVec("fill_version_read", 5)
    first = FunctionalArray.uniform(5, z3.BitVecVal(0, 8))
    for i in range(5):
        first = first.store(z3.BitVecVal(i, 5), z3.BitVecVal(-3, 8), a)
    first_read = first.select(read)
    second = first.store(index, z3.BitVecVal(7, 8), z3.BoolVal(True))
    for i in range(5):
        second = second.store(z3.BitVecVal(i, 5), z3.BitVecVal(12, 8), b)
    expected = z3.If(b, z3.BitVecVal(12, 8),
                    z3.If(read == index, z3.BitVecVal(7, 8), first_read))
    expected = z3.If(z3.And(read >= 0, read < 5), expected, z3.BitVecVal(0, 8))
    solver = z3.Solver()
    solver.add(second.select(read) != expected)
    assert solver.check() == z3.unsat
    assert first.select(read) is first_read
    assert first._read_history() is first._read_history()


def test_uniform_fill_eliminates_per_cell_alias_tests(monkeypatch):
    import behave.sim.symbolic.functional_array as arrays

    array = FunctionalArray.uniform(128, z3.BitVecVal(0, 8))
    guard = z3.Bool("large_fill")
    for i in range(128):
        array = array.store(z3.BitVecVal(i, 9), z3.BitVecVal(42, 8), guard)

    def unexpected(*_args):
        raise AssertionError("a complete fill still expanded an individual address")

    monkeypatch.setattr(arrays, "_same_index", unexpected)
    array.select(z3.BitVec("large_fill_read", 9))
