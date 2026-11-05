"""Persistent finite arrays used by large bounded Behavior state."""

import gc
import time
import weakref

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic.encoder import _Commands
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.symbolic.uniform_array import UniformArray
from behave.sim.core.memory import memory_junk_value


def _signed(model, term):
    return model.eval(term, model_completion=True).as_signed_long()


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('base', ['constant', 'packed', 'memory', 'parameter', 'merge'])
def test_unguarded_range_sources_preserve_union_without_building_conditions(monkeypatch, private, base):
    context = z3.Context() if private else z3.main_ctx()
    integer = lambda n: z3.BitVecVal(n, 8, ctx=context)
    uniform = FunctionalArray.uniform(4, integer(-2))
    if base == 'constant':
        array = uniform
    elif base == 'packed':
        array = FunctionalArray.from_packed(z3.BitVec('source_cells', 32, ctx=context), 4, 8)
    elif base == 'memory':
        array = FunctionalArray.memory(4, 'source_memory', 5, 8)
    elif base == 'parameter':
        # Parameter constructors use their own ordinary context; translate
        # before attaching the private-context writes below.
        array = FunctionalArray.parameter(4, 'source_parameter', 8).translate(context)
    else:
        array = FunctionalArray.merge_bits(uniform, uniform, uniform, 8, signed=True)
    index = z3.BitVec('source_index', 8, ctx=context)
    enabled = z3.Bool('source_enabled', ctx=context)
    # Shared diamonds, invalid/overwritten writes, and two different histories.
    # Even impossible source guards remain in this deliberately coarse union.
    for i in range(4):
        left = array.store(index, integer(i + 3), enabled)
        right = array.store(integer(-1), integer(-i - 7), z3.Not(enabled))
        array = FunctionalArray.choose(z3.Bool('source_choice_' + str(i), ctx=context), left, right)
    expected = array.range_cases()

    def forbidden(*_args, **_kwargs):
        raise AssertionError('an unguarded source union must not build or walk guard formulas')

    monkeypatch.setattr(FunctionalArray, 'formula_terms', property(forbidden))
    for name in ('And', 'Or', 'Not', 'BoolVal'):
        monkeypatch.setattr(z3, name, forbidden)
    actual = array.range_cases(guarded=False)
    key = lambda row: (row[1].get_id(), row[2]) if row[1] is not None else (None, row[2])
    assert {key(row) for row in actual} == {key(row) for row in expected}
    assert all(row[0] is None for row in actual)


@pytest.mark.parametrize("lower", [-5, 0, 7])
def test_arraystore_expression_binds_closed_addresses_before_history(lower):
    encoder = _Commands(None, {})
    enabled = z3.Bool("closed_store_enabled")
    array = FunctionalArray.uniform(16, z3.BitVecVal(-2, 9))
    for cell in range(16):
        # Source array coordinates become zero-based SolverIR addresses. Even
        # a zero lower bound otherwise leaves a nonliteral `cell - 0` term.
        address = z3.BitVecVal(cell + lower, 8) - z3.BitVecVal(lower, 8)
        updated = encoder.array_store(array, address, z3.BitVecVal(cell, 9))
        assert z3.is_bv_value(updated.writes[-1].index)
        assert updated.writes[-1].index.as_signed_long() == cell
        assert z3.is_true(updated.writes[-1].guard)
        array = FunctionalArray.choose(enabled, updated, array)
    assert not encoder.definitions  # Literal folding introduces no symbols.
    read = z3.BitVec("closed_store_read", 8)
    expected = z3.BitVecVal(0, 9)
    for cell in range(16):
        expected = z3.If(read == cell, z3.If(enabled, cell, z3.BitVecVal(-2, 9)), expected)
    solver = z3.Solver()
    solver.add(array.select(read) != expected)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("lower", [-5, 0, 7])
def test_arraystore_bound_address_preserves_symbolic_aliases_and_wrapping(lower):
    encoder = _Commands(None, {})
    source, read = z3.BitVecs("store_source_coordinate store_read_coordinate", 5)
    address = source - z3.BitVecVal(lower, 5)
    value = z3.BitVec("store_symbolic_value", 9)
    array = FunctionalArray.uniform(4, z3.BitVecVal(-2, 7))
    array = encoder.array_store(array, address, value)
    array = encoder.array_store(array, z3.BitVecVal(3, 3), z3.BitVecVal(21, 9))
    # Signed -1 is invalid, not an alias for the narrow unsigned-looking 3.
    array = encoder.array_store(array, z3.BitVecVal(3, 2), z3.BitVecVal(99, 9))
    expected = z3.BitVecVal(0, 9)
    for cell in range(4):
        item = z3.If(address == cell, value, z3.BitVecVal(-2, 9))
        expected = z3.If(read == cell, z3.BitVecVal(21, 9) if cell == 3 else item, expected)
    solver = z3.Solver()
    solver.add(*(definition.assertion for definition in encoder.definitions))
    solver.add(array.select(read) != expected)
    assert solver.check() == z3.unsat


def test_repeated_read_reuses_syntax_and_retains_discarded_index(monkeypatch):
    array = FunctionalArray.uniform(8, z3.BitVecVal(11, 9))
    index = z3.BitVecVal(3, 5)
    retained = weakref.ref(index)
    first = array.select(index)
    original = FunctionalArray._select
    calls = []

    def count(self, address):
        calls.append(address)
        return original(self, address)

    monkeypatch.setattr(FunctionalArray, "_select", count)
    for _ in range(20):
        # A new Python wrapper denotes the same native expression.
        assert array.select(z3.BitVecVal(3, 5)) is first
    assert not calls
    del index
    gc.collect()
    assert retained() is not None
    del array
    gc.collect()
    assert retained() is None


def test_read_memo_is_version_width_and_context_local():
    array = FunctionalArray.uniform(5, z3.BitVecVal(7, 9))
    positive = z3.BitVecVal(3, 4)
    negative = z3.BitVecVal(3, 2)  # Signed -1, not the same address.
    assert z3.simplify(array.select(positive)).as_long() == 7
    assert z3.simplify(array.select(negative)).as_long() == 0
    updated = array.store(positive, z3.BitVecVal(-9, 12), z3.BoolVal(True))
    assert z3.simplify(updated.select(positive)).as_signed_long() == -9
    assert array.select(positive).size() == 9
    widened = array.widen(12)
    assert widened.select(positive).size() == 12
    context = z3.Context()
    translated = updated.translate(context)
    result = translated.select(positive.translate(context))
    assert result.ctx == context and z3.simplify(result).as_signed_long() == -9
    # Out-of-range reads are context-independent zeros. Even here the cache
    # must not return a zero belonging to a different solver context.
    result = array.select(negative.translate(context))
    assert result.ctx == context and z3.simplify(result).as_long() == 0
    with pytest.raises(TypeError, match="bit-vector"):
        array.select(3)


@pytest.mark.parametrize("signed", (False, True))
def test_shared_merge_dag_computes_each_read_once_and_matches_dense_oracle(monkeypatch, signed):
    length, width = 3, 5
    cells = [z3.BitVec(f"merge_read_cell_{i}", width) for i in range(length)]
    values = [z3.BitVec(f"merge_read_data_{i}", width) for i in range(length)]
    masks = [z3.BitVec(f"merge_read_mask_{i}", width) for i in range(length)]
    current, data, mask = (FunctionalArray.from_cells(items) for items in (cells, values, masks))
    left = FunctionalArray.merge_bits(current, data, mask, width, signed=signed)
    right = FunctionalArray.merge_bits(data, current, mask, width, signed=signed)
    merged = FunctionalArray.merge_bits(left, right, mask, width, signed=signed)
    address = z3.BitVec("merge_read_address", 4)
    original = FunctionalArray._read_plan
    calls = {}

    def count(self, index, **read):
        calls[id(self)] = calls.get(id(self), 0) + 1
        return original(self, index, **read)

    monkeypatch.setattr(FunctionalArray, "_read_plan", count)
    first = merged.select(address)
    assert merged.select(z3.BitVec("merge_read_address", 4)) is first
    assert set(calls.values()) == {1} and len(calls) == 6
    expected = z3.BitVecVal(0, width + int(not signed))
    for i, (a, b, m) in enumerate(zip(cells, values, masks)):
        left_cell = (a & ~m) | (b & m)
        right_cell = (b & ~m) | (a & m)
        cell = (left_cell & ~m) | (right_cell & m)
        expected = z3.If(address == i, cell if signed else z3.ZeroExt(1, cell), expected)
    solver = z3.SolverFor("QF_BV")
    solver.add(first != expected)
    assert solver.check() == z3.unsat
    # The uncached construction is the unchanged read-over-write definition,
    # including all nested versions, not a sampled subset of the address set.
    from behave.sim.symbolic.functional_array import _array_versions
    for version in _array_versions(merged):
        version._selections.clear()
    assert z3.eq(first, merged.select(address))


@pytest.mark.parametrize("capacity", [1, 3, 32, 512])
@pytest.mark.parametrize("symbolic", [False, True])
def test_repeated_scalar_literal_keeps_exact_uniform_store_semantics(capacity, symbolic):
    seed = z3.BitVec("literal_seed", 9) if symbolic else z3.BitVecVal(-3, 9)
    array = _Commands.array_literal([seed] * capacity)
    assert type(array) is FunctionalArray and not array.writes
    assert array.length == capacity
    index, read = z3.BitVecs("literal_write literal_read", 12)
    value = z3.BitVec("literal_update", 11)
    enabled = z3.Bool("literal_enabled")
    updated = array.store(index, value, enabled)
    assert len(updated.writes) == 1
    # Independent dense semantics, including negative/out-of-bounds reads and
    # writes, symbolic defaults, and widening stores. No domain is restricted.
    expected = z3.BitVecVal(0, 11)
    for i in range(capacity):
        cell = z3.If(z3.And(enabled, index == i), value, z3.SignExt(2, seed))
        expected = z3.If(read == i, cell, expected)
    solver = z3.SolverFor("QF_BV")
    mismatch = updated.select(read) != expected
    deadline = time.monotonic() + 10

    def check():
        remaining = int(1000 * (deadline - time.monotonic()))
        assert remaining > 0, "the complete array equivalence proof exceeded 10s"
        solver.set(timeout=remaining)
        assert solver.check() == z3.unsat

    # Exact partition, not sampled addresses: every in-range read and the
    # entire invalid-read complement. This avoids asking bit-blasting to
    # rediscover a 512-way selector split. Writes/data/enable remain symbolic;
    # the same dense oracle and one total 10s proof budget are retained.
    for address in range(capacity):
        solver.push()
        solver.add(z3.substitute(
            mismatch, (read, z3.BitVecVal(address, read.size()))))
        check()
        solver.pop()
    solver.add(z3.Or(read < 0, read >= capacity), mismatch)
    check()


def test_literal_compaction_requires_equal_scalar_terms_not_equal_looking_shapes():
    a, b = z3.BitVecs("literal_a literal_b", 8)
    assert _Commands.array_literal([]) == []
    assert type(_Commands.array_literal([a, b])) is list
    assert type(_Commands.array_literal([z3.BitVecVal(0, 8), z3.BitVecVal(0, 9)])) is list
    nested = [[a, b], [a, b]]
    copied = _Commands.array_literal(nested)
    assert type(copied) is list and all(type(row) is list for row in copied)
    assert copied[0] is not nested[0] and copied[0] is not copied[1]


def test_repeated_literal_stores_and_selection_share_the_existing_proof_source():
    import time

    from behave.sim.symbolic import ir
    from behave.sim.symbolic.closure_proof import _StructuralProofWork, _SyntacticClosureProof
    from behave.sim.symbolic.ranges import Range

    capacity, history = 256, 32
    enc = _Commands(None, {})
    array = enc.array_literal([z3.BitVecVal(0, 16)] * capacity)
    for i in range(history):
        array = array.store(z3.BitVec(f"literal_store_{i}", 16),
                            z3.BitVec(f"literal_data_{i}", 16) & 255,
                            z3.Bool(f"literal_guard_{i}"))
    terms = [enc.value(ir.Sel(ir.Var("array"), ir.Lit(i)), {"array": array})
             for i in range(capacity)]
    assert len(enc.array_selections) == capacity
    assert len(array.writes) == history
    work = _StructuralProofWork(4 * capacity + 8 * history, time.monotonic() + 30)
    proof = _SyntacticClosureProof({}, work=work, array_selections=enc.array_selections)
    assert all(proof.coarse_signed_hull(t) == Range(0, 255) for t in terms)


def test_guarded_store_and_selection_match_componentwise_array_semantics():
    index = z3.BitVec("functional_index", 4)
    value = z3.BitVec("functional_value", 5)
    enabled = z3.Bool("functional_enabled")
    array = FunctionalArray.uniform(6, z3.BitVecVal(0, 5)).store(
        index, value, enabled)

    for concrete_index in range(-1, 8):
        for concrete_value in (-3, 7):
            for concrete_enabled in (False, True):
                solver = z3.Solver()
                solver.add(
                    index == concrete_index,
                    value == concrete_value,
                    enabled == concrete_enabled,
                )
                assert solver.check() == z3.sat
                model = solver.model()
                expected = [0] * 6
                if concrete_enabled and 0 <= concrete_index < 6:
                    expected[concrete_index] = concrete_value
                assert [
                    _signed(model, array.select(z3.BitVecVal(position, 4)))
                    for position in range(6)
                ] == expected


def test_conditional_store_chains_have_extensional_qfbv_equality():
    choose_left = z3.Bool("functional_choose_left")
    index = z3.BitVec("functional_equal_index", 4)
    base = FunctionalArray.uniform(8, z3.BitVecVal(0, 6))
    left = base.store(index, z3.BitVecVal(11, 6), z3.BoolVal(True))
    right = base.store(index, z3.BitVecVal(12, 6), z3.BoolVal(True))
    chosen = FunctionalArray.choose(choose_left, left, right)

    solver = z3.Solver()
    solver.add(index == 3, choose_left)
    solver.add(z3.Not(chosen.equal(left)))
    assert solver.check() == z3.unsat

    solver = z3.Solver()
    solver.add(index == 3, z3.Not(choose_left))
    solver.add(chosen.equal(left))
    assert solver.check() == z3.unsat

    # The representation remains in QF_BV: no Z3 array-typed term is created.
    assert all(not z3.is_array(term) for term in chosen.formula_terms)


@pytest.mark.parametrize("read_width", (3, 5, 9))
@pytest.mark.parametrize("base_kind", ("uniform", "packed", "memory"))
def test_read_over_write_matches_dense_oracle_with_mixed_indices(read_width, base_kind):
    """All addresses/guards/data, not just successful in-range examples."""
    length, width = 5, 9
    if base_kind == "uniform":
        seed = z3.BitVec("read_over_write_seed", width)
        array = FunctionalArray.uniform(length, seed)
        cells = [seed] * length
    elif base_kind == "packed":
        array = FunctionalArray.symbolic(length, "read_over_write_base", width, 0, 255)
        atom = array.formula_terms[0]
        cells = [z3.ZeroExt(1, z3.Extract(8 * i + 7, 8 * i, atom))
                 for i in range(length)]
    else:
        array = FunctionalArray.memory(length, "read_over_write_memory", 8, width)
        cells = [z3.BitVecVal(memory_junk_value("read_over_write_memory", i, 8), width)
                 for i in range(length)]
    symbolic_index = z3.BitVec("read_over_write_index", 4)
    enabled, later = z3.Bools("read_over_write_enabled read_over_write_later")
    writes = [
        (z3.BitVecVal(1, 3), z3.BitVec("first", width), z3.BoolVal(True)),
        (symbolic_index, z3.BitVec("indirect", width), enabled),
        (z3.BitVecVal(-1, 3), z3.BitVec("negative", width), z3.BoolVal(True)),
        (z3.BitVecVal(5, 5), z3.BitVec("past_end", width), z3.BoolVal(True)),
        (z3.BitVecVal(1, 5), z3.BitVec("last", width), later),
    ]
    for index, value, guard in writes:
        array = array.store(index, value, guard)
        # Independent componentwise oracle compares widened signed indices.
        widened = z3.SignExt(9 - index.size(), index)
        cells = [z3.If(z3.And(guard, widened == i), value, cell)
                 for i, cell in enumerate(cells)]
    read = z3.BitVec("read_over_write_read", read_width)
    addresses = [read] + [z3.BitVecVal(i, read_width) for i in (-1, 0, 1, 4, 5)]
    for address in addresses:
        widened = z3.SignExt(9 - address.size(), address)
        expected = z3.BitVecVal(0, width)
        for i, cell in enumerate(cells):
            expected = z3.If(widened == i, cell, expected)
        solver = z3.SolverFor("QF_BV")
        solver.add(array.select(address) != expected)
        assert solver.check() == z3.unsat


def test_literal_read_builds_no_unrelated_history_or_shadowed_base(monkeypatch):
    from behave.sim.symbolic import functional_array

    array = FunctionalArray.uniform(128, z3.BitVecVal(0, 16))
    values = [z3.BitVec(f"read_over_write_cell_{i}", 16) for i in range(128)]
    for i, value in enumerate(values):
        array = array.store(z3.BitVecVal(i, 9), value, z3.BoolVal(True))

    def unused(*args):
        raise AssertionError("a known written cell does not need its old base")

    monkeypatch.setattr(functional_array, "_base_select", unused)
    for i, value in enumerate(values):
        assert z3.eq(array.select(z3.BitVecVal(i, 16)), value)
    assert z3.is_bv_value(array.select(z3.BitVecVal(-1, 16)))


def test_conditional_versions_retain_one_shared_persistent_prefix():
    """One guarded update adds one row instead of copying all prior rows."""

    current = FunctionalArray.uniform(256, z3.BitVecVal(0, 9))
    conditions = []
    for position in range(64):
        enabled = z3.Bool(f"functional_version_enabled_{position}")
        conditions.append(enabled)
        updated = current.store(
            z3.BitVecVal(position, 9),
            z3.BitVecVal(position + 1, 9),
            z3.BoolVal(True),
        )
        current = FunctionalArray.choose(enabled, updated, current)
        assert len(current.writes) == position + 1

    solver = z3.Solver()
    solver.add(*conditions)
    assert solver.check() == z3.sat
    model = solver.model()
    assert [
        _signed(model, current.select(z3.BitVecVal(position, 9)))
        for position in range(64)
    ] == list(range(1, 65))


def test_extensional_equality_does_not_require_equal_overwritten_defaults():
    left = FunctionalArray.uniform(2, z3.BitVecVal(0, 4))
    right = FunctionalArray.uniform(2, z3.BitVecVal(9, 4))
    for position, value in enumerate((3, 7)):
        index = z3.BitVecVal(position, 3)
        encoded = z3.BitVecVal(value, 4)
        left = left.store(index, encoded, z3.BoolVal(True))
        right = right.store(index, encoded, z3.BoolVal(True))

    solver = z3.Solver()
    solver.add(z3.Not(left.equal(right)))
    assert solver.check() == z3.unsat


def test_materialization_evaluates_each_store_once():
    first = z3.BitVec("functional_first_index", 4)
    second = z3.BitVec("functional_second_index", 4)
    array = FunctionalArray.uniform(5, z3.BitVecVal(1, 5))
    array = array.store(first, z3.BitVecVal(4, 5), z3.BoolVal(True))
    array = array.store(second, z3.BitVecVal(7, 5), z3.BoolVal(True))
    solver = z3.Solver()
    solver.add(first == 2, second == 2)
    assert solver.check() == z3.sat

    assert array.materialize(solver.model(), _signed) == [1, 1, 7, 1, 1]


def test_control_join_can_mix_compact_and_componentwise_scalar_arrays():
    """Compactness is an optimization, not a second recursive shape."""

    choose_compact = z3.Bool("functional_mixed_choose_compact")
    compact = FunctionalArray.uniform(3, z3.BitVecVal(1, 5)).store(
        z3.BitVecVal(1, 3), z3.BitVecVal(7, 5), z3.BoolVal(True))
    componentwise = [z3.BitVecVal(value, 5) for value in (2, 3, 4)]
    encoder = _Commands(None, {})

    joined = encoder._write(
        compact, componentwise, choose_compact, "mixed_array")
    assert isinstance(joined, list) and len(joined) == 3

    for selected, expected in (
            (True, (1, 7, 1)), (False, (2, 3, 4))):
        solver = z3.Solver()
        solver.add(*encoder.defs, choose_compact == selected)
        assert solver.check() == z3.sat
        assert tuple(_signed(solver.model(), value) for value in joined) == expected


def test_indexed_memory_base_and_sparse_writes_remain_compact_and_exact():
    index = z3.BitVec("functional_memory_index", 17)
    concrete = UniformArray.memory(
        1 << 16, "ram", 32, {7: 19})
    symbolic = FunctionalArray.from_uniform(concrete, 33).store(
        index, z3.BitVecVal(23, 33), z3.BoolVal(True))

    assert len(symbolic.writes) == 2
    assert len(symbolic.formula_terms) < 10
    for address in (0, 7, 8, 32768, 65535):
        solver = z3.Solver()
        solver.add(index == 8)
        assert solver.check() == z3.sat
        got = _signed(
            solver.model(), symbolic.select(z3.BitVecVal(address, 17)))
        expected = (23 if address == 8 else 19 if address == 7
                    else memory_junk_value("ram", address, 32))
        assert got == expected


def test_symbolic_memory_junk_is_the_concrete_protocol_function():
    from behave.sim.symbolic.memory_terms import memory_junk_term

    for width in (1, 5, 8, 32, 65):
        address = z3.BitVec(f"junk_address_{width}", 17)
        term = memory_junk_term("named-memory", address, width)
        for value in (0, 1, 17, 32768, 65535):
            assert z3.simplify(z3.substitute(
                term, (address, z3.BitVecVal(value, 17)))).as_long() == \
                memory_junk_value("named-memory", value, width)


def test_arbitrary_unsigned_component_array_has_one_exact_packed_qfbv_atom():
    """Compact closure state is the same finite product, not SMT array theory."""

    array = FunctionalArray.symbolic(
        1 << 16, "packed_unsigned_memory", 40, 0, (1 << 32) - 1)
    assert len(array.formula_terms) == 1
    assert array.formula_terms[0].size() == (1 << 16) * 32
    assert array.base_restrictions == ()
    assert all(not z3.is_array(term) for term in array.formula_terms)
    cases = array.range_cases()
    assert len(cases) == 1
    assert z3.is_true(cases[0][0])
    assert cases[0][1] is None
    assert cases[0][2] == (0, (1 << 32) - 1)


def test_packed_component_selection_and_guarded_store_are_exact():
    array = FunctionalArray.symbolic(4, "packed_small", 5, 0, 7)
    index = z3.BitVec("packed_small_index", 4)
    enabled = z3.Bool("packed_small_enabled")
    updated = array.store(index, z3.BitVecVal(6, 5), enabled)

    solver = z3.Solver()
    solver.add(index == 2, enabled)
    solver.add(updated.select(z3.BitVecVal(2, 4)) != 6)
    assert solver.check() == z3.unsat

    solver = z3.Solver()
    solver.add(array.select(z3.BitVecVal(1, 4)) > 7)
    assert solver.check() == z3.unsat

    # Reading the same untouched component twice denotes the same product cell.
    solver = z3.Solver()
    solver.add(index == 2)
    solver.add(
        updated.select(z3.BitVecVal(1, 4))
        != updated.select(z3.BitVecVal(1, 4)))
    assert solver.check() == z3.unsat


def test_committing_a_store_history_preserves_every_finite_cell():
    first = z3.BitVec("compact_history_first", 4)
    second = z3.BitVec("compact_history_second", 4)
    enabled = z3.Bool("compact_history_enabled")
    original = FunctionalArray.uniform(5, z3.BitVecVal(1, 6))
    original = original.store(first, z3.BitVecVal(7, 6), enabled)
    original = original.store(second, z3.BitVecVal(11, 6), z3.BoolVal(True))
    compact = original.compact(-32, 31)

    assert compact.writes == ()
    for position in range(5):
        index = z3.BitVecVal(position, 4)
        solver = z3.Solver()
        solver.add(original.select(index) != compact.select(index))
        assert solver.check() == z3.unsat


def test_packed_and_concrete_memory_equality_is_extensional():
    packed = FunctionalArray.symbolic(4, "packed_audit_state", 5, 0, 7)
    concrete = FunctionalArray.from_uniform(
        UniformArray.uniform(4, 3).store(2, 6), 5)
    atom = packed.formula_terms[0]
    # Cell zero occupies the low bits.
    image = 3 | (3 << 3) | (6 << 6) | (3 << 9)

    solver = z3.Solver()
    solver.add(atom == image, z3.Not(packed.equal(concrete)))
    assert solver.check() == z3.unsat

    solver = z3.Solver()
    solver.add(atom == image ^ 1, packed.equal(concrete))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('base_kind', ('uniform', 'packed', 'memory', 'merge'))
@pytest.mark.parametrize('index_width', (4, 64))
def test_private_arena_compaction_and_equality_match_dense_cells(base_kind, index_width):
    context, length, width = z3.Context(), 3, 6
    if base_kind == 'uniform':
        array = FunctionalArray.uniform(length, z3.BitVecVal(5,width))
    elif base_kind == 'packed':
        array = FunctionalArray.symbolic(length, 'arena_base',width,0,31)
    elif base_kind == 'memory':
        array = FunctionalArray.memory(length, 'arena_memory',5,width)
    else:
        array = FunctionalArray.merge_bits(
            FunctionalArray.symbolic(length,'arena_old',width,0,31),
            FunctionalArray.symbolic(length,'arena_new',width,0,31),
            FunctionalArray.symbolic(length,'arena_mask',width,0,31),5,signed=False)
    # Capture the base oracle before compaction, then translate the actual DAG.
    cells = [array.select(z3.BitVecVal(i,4)).translate(context) for i in range(length)]
    array = array.translate(context)
    index = z3.BitVec('arena_index',index_width,ctx=context)
    value = z3.BitVec('arena_value',width,ctx=context)
    enabled = z3.Bool('arena_enabled',ctx=context)
    array = array.store(index, value, enabled)
    compact = array.compact(-32,31)
    oracle = FunctionalArray.from_cells([z3.If(z3.And(enabled,index==i),value,cell)
                                        for i, cell in enumerate(cells)])
    solver = z3.Solver(ctx=context)
    solver.add(z3.Or(z3.Not(array.equal(oracle)), z3.Not(compact.equal(oracle))))
    assert solver.check() == z3.unsat
    assert array.equal(array).ctx == context
    assert all(term.ctx == context for term in compact.difference_indices(oracle))


def test_context_neutral_memory_packing_does_not_reuse_another_arena():
    memory = FunctionalArray.memory(3,'shared_memory',5,6)
    for context in (z3.Context(), z3.Context()):
        packed = FunctionalArray.symbolic(3,'memory_image',6,0,31).translate(context)
        equal = packed.equal(memory)
        assert equal.ctx == context
        solver = z3.Solver(ctx=context)
        solver.add(equal)
        assert solver.check() == z3.sat
        solver.add(z3.Or(*(packed.select(z3.BitVecVal(i,4,ctx=context)) !=
                           memory_junk_value('shared_memory',i,5) for i in range(3))))
        assert solver.check() == z3.unsat
        assert memory.equal(memory,context=context).ctx == context
