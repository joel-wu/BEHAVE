"""The recursive merge primitive and its lazy QF_BV carrier have one meaning."""

import pytest
import z3

from behave.sim.symbolic import encoder, ir, primitives, typecheck as types
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.symbolic.uniform_array import UniformArray

pytestmark = pytest.mark.solver


@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("width", [1, 4, 9])
def test_lazy_merge_is_componentwise_for_all_data_masks_guards_and_indices(signed, width):
    context = encoder._Encoder(None, {})
    write, read = z3.BitVecs("write read", 4)
    active = z3.Bool("active")
    terms = [z3.BitVec(f"data{i}", width + i + 1) for i in range(6)]
    arrays = [FunctionalArray.uniform(3, terms[i]).store(write, terms[i+3], active) for i in range(3)]
    merged = context.merge_bits(*arrays, width, signed=signed)
    assert type(merged) is FunctionalArray and not merged.writes
    expected = z3.BitVecVal(0, width + int(not signed))
    for at in range(3):
        cells = [z3.If(z3.And(active, write == at), terms[i+3], z3.SignExt(3, terms[i])) for i in range(3)]
        a, b, m = (z3.Extract(width - 1, 0, cell) for cell in cells)
        value = (a & ~m) | (b & m)
        if not signed:
            value = z3.ZeroExt(1, value)
        expected = z3.If(read == at, value, expected)
    solver = z3.SolverFor("QF_BV")
    solver.add(merged.select(read) != expected)
    assert solver.check() == z3.unsat
    # Widening, later stores, choices, equality and model materialization must
    # retain the same base, not discard its hidden expression dependencies.
    updated = merged.store(z3.BitVecVal(1, 4), z3.BitVecVal(-7, 16), active)
    chosen = FunctionalArray.choose(active, updated, merged)
    solver = z3.Solver()
    solver.add(active, *(term == i * 7 for i, term in enumerate(terms)), write == 2)
    assert solver.check() == z3.sat
    model = solver.model()
    decode = lambda model, value: model.eval(value, model_completion=True).as_signed_long()
    expected_cells = [decode(model, chosen.select(z3.BitVecVal(i, 4))) for i in range(3)]
    assert chosen.materialize(model, decode) == expected_cells
    assert decode(model, chosen.compact().select(z3.BitVecVal(1, 4))) == -7
    from z3.z3util import get_vars
    assert {term.get_id() for term in terms} <= {
        variable.get_id() for term in merged.formula_terms for variable in get_vars(term)}


@pytest.mark.parametrize("signed", [False, True])
def test_concrete_sparse_merge_wraps_even_unwritten_out_of_carrier_values(signed):
    spec = primitives.primitive("smerge" if signed else "umerge")
    current = UniformArray.uniform(16, 73).store(3, -97)
    data = UniformArray.uniform(16, -3)
    mask = UniformArray.uniform(16, 0).store(7, 5)
    compact = spec.denotation(current, data, mask, 4)
    dense = spec.denotation(current.materialize(), data.materialize(), mask.materialize(), 4)
    assert compact.materialize() == dense


def test_nested_merge_has_checked_identical_integer_shapes():
    good = types.Array(2, types.Array(3))
    expr = ir.Op("umerge", ir.Var("a"), ir.Var("b"), ir.Var("m"), ir.Lit(4))
    for wrong in (types.Array(3, types.Array(3)), types.Array(2, types.Array(3, types.BOOLEAN))):
        errors = []
        assert types._of_operator(expr, {"a": good, "b": wrong, "m": good}, errors, "test") is None
        assert errors
    errors = []
    assert types._of_operator(expr, dict.fromkeys(("a", "b", "m"), good), errors, "test") == good
    assert not errors


def test_structured_output_binding_reads_compact_recursive_arrays():
    from behave.sim.symbolic.value_binding import SolverLeafPath

    row = UniformArray.uniform(3, -1).store(2, 7)
    values = {"q": UniformArray.uniform(2, row)}
    assert SolverLeafPath("q", (1, 2)).read(values) == 7
    assert SolverLeafPath("q", (0, 0)).read(values) == -1


def test_ram_merge_construction_and_one_selection_do_not_enumerate_cells(monkeypatch):
    length = 65536
    arrays = [FunctionalArray.uniform(length, z3.BitVec(f"base{i}", 33)) for i in range(3)]
    calls = []
    select = FunctionalArray.select
    def counted(self, index):
        calls.append(self)
        return select(self, index)
    monkeypatch.setattr(FunctionalArray, "select", counted)
    merged = encoder._Encoder(None, {}).merge_bits(*arrays, 32, signed=False)
    assert not calls
    result = merged.select(z3.BitVec("at", 18))
    assert len(calls) == 4 and result.size() == 33
    assert merged.range_cases()[0][2] == (0, (1 << 32) - 1)
