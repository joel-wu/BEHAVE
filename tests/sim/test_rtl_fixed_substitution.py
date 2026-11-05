"""Prepared argument vectors must implement ordinary, complete Z3 substitution."""

import gc

import pytest

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver

from behave.sim.rtl import symbolic_state
from behave.sim.rtl.symbolic_state import FixedBindings


@pytest.mark.parametrize("own_context", [False, True])
@pytest.mark.parametrize("width", [1, 8, 64])
def test_prepared_substitution_matches_z3_for_typed_terms(own_context, width):
    ctx = z3.Context() if own_context else z3.main_ctx()
    flag = z3.Bool("same_name", ctx=ctx)
    integer = z3.Int("same_name", ctx=ctx)
    bits = z3.BitVec("same_name", width, ctx=ctx)
    memory = z3.Array("memory", z3.IntSort(ctx), z3.BitVecSort(width, ctx))
    helper = z3.Function("helper", z3.IntSort(ctx), z3.BitVecSort(width, ctx))
    fixed = FixedBindings.of((flag == z3.BoolVal(False, ctx=ctx), integer == -2,
                              bits == (1 << width) - 1))
    terms = (flag, integer + 1, bits + 1, helper(integer),
             z3.Select(memory, integer) == bits, z3.Store(memory, integer, bits),
             z3.If(flag, bits, helper(integer)))
    for term in terms:
        expected = z3.substitute(term, *fixed.replacements)
        actual = fixed.substitute(term)
        assert actual.ctx == ctx
        assert actual.eq(expected)


@pytest.mark.parametrize("annotation", ["patterns", "no_patterns"])
def test_quantifiers_and_annotations_use_the_same_native_substitution(annotation):
    ctx = z3.Context()
    bound, free = z3.Ints("bound free", ctx=ctx)
    helper = z3.Function("quantified_helper", z3.IntSort(ctx), z3.IntSort(ctx), z3.IntSort(ctx))
    application = helper(bound, free)
    term = z3.ForAll([bound], application > free, **{annotation: [application]})
    fixed = FixedBindings.of((free == 7,))
    expected = z3.substitute(term, *fixed.replacements)
    actual = fixed.substitute(term)
    assert actual.eq(expected)
    assert actual.sexpr() == expected.sexpr()


def test_duplicate_and_conflicting_pairs_retain_native_order():
    value = z3.Int("duplicate")
    first = FixedBindings.of((value == 1, value == 1))
    second = FixedBindings.of((value == 2,))
    fixed = FixedBindings.combine((first, FixedBindings.of(()), second))
    assert len(fixed.equations) == len(fixed.replacements) == 3
    term = value > 0
    assert fixed.substitute(term).eq(z3.substitute(term, *fixed.replacements))
    solver = z3.Solver()
    solver.add(*fixed.equations, fixed.substitute(term))
    assert solver.check() == z3.unsat


def test_argument_vectors_are_reused_without_dropping_irrelevant_pairs(monkeypatch):
    variables = tuple(z3.Int(f"fixed_{i}") for i in range(2000))
    fixed = FixedBindings.of(tuple(variable == index for index, variable in enumerate(variables)))
    native = symbolic_state.Z3_substitute
    calls = []

    def substitute(ctx, term, count, sources, values):
        calls.append((count, sources, values))
        return native(ctx, term, count, sources, values)

    monkeypatch.setattr(symbolic_state, "Z3_substitute", substitute)
    for term in (variables[0] > 2, variables[-1] == 1999, z3.And(*(v >= 0 for v in variables))):
        assert fixed.substitute(term).eq(z3.substitute(term, *fixed.replacements))
    assert len(calls) == 3
    assert all(count == 2000 and sources is fixed._sources and values is fixed._values
               for count, sources, values in calls)


def test_deep_shared_dag_needs_no_python_traversal():
    value = z3.Int("deep_shared")
    helper = z3.Function("deep_helper", z3.IntSort(), z3.IntSort())
    term = value
    for _ in range(1600):
        term = helper(term)
    term = z3.And(term > value, term < value + 100)
    fixed = FixedBindings.of((value == 4,))
    assert fixed.substitute(term).eq(z3.substitute(term, *fixed.replacements))


def test_bindings_and_returned_expression_own_their_asts():
    def prepare():
        ctx = z3.Context()
        value = z3.Int("owned", ctx=ctx)
        return FixedBindings.of((value == 11,)), value + 1

    fixed, term = prepare()
    gc.collect()
    actual = fixed.substitute(term)
    del fixed, term
    gc.collect()
    assert z3.simplify(actual).as_long() == 12


def test_empty_bindings_keep_the_original_context_and_term():
    term = z3.Bool("unbound", ctx=z3.Context())
    assert FixedBindings.of(()).substitute(term) is term


def test_mixed_contexts_are_rejected_before_entering_native_substitution():
    first = z3.Int("same", ctx=z3.Context())
    second = z3.Int("same", ctx=z3.Context())
    a, b = FixedBindings.of((first == 1,)), FixedBindings.of((second == 2,))
    with pytest.raises(ValueError, match="context"):
        FixedBindings.combine((a, b))
    with pytest.raises(ValueError, match="context"):
        a.substitute(second > 0)


def test_native_substitution_still_rejects_ill_sorted_pairs():
    value = z3.BitVec("bad_sort", 8)
    # Production obtains pairs from typed equalities; invalid pairs are not
    # permitted even if an internal caller bypasses that constructor.
    fixed = FixedBindings((), ((value, z3.BitVecVal(1, 16)),))
    with pytest.raises(z3.Z3Exception):
        fixed.substitute(value == 0)
