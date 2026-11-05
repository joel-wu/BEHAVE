"""Every local reduction is justified by the unchanged complete premises."""

import pytest

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver

from behave.sim.rtl.symbolic_state import assertion_conjunction


@pytest.mark.parametrize("own_context", [False, True])
def test_literal_classification_keeps_the_exact_supported_scalar_kinds(own_context):
    from behave.sim.rtl.fixed_target import _literal

    ctx = z3.Context() if own_context else z3.main_ctx()
    literals = (z3.BoolVal(False, ctx), z3.BoolVal(True, ctx), z3.IntVal(-3, ctx),
                z3.BitVecVal(255, 8, ctx))
    symbolic = (z3.Bool("literal_bool", ctx), z3.Int("literal_int", ctx),
                z3.BitVec("literal_bits", 8, ctx), z3.RealVal("1/2", ctx),
                z3.StringVal("literal_text", ctx), z3.IntVal(1, ctx) + 1)
    assert all(_literal(term) for term in literals)
    assert not any(_literal(term) for term in symbolic)


@pytest.mark.parametrize("own_context", [False, True])
def test_fast_conjunction_retains_exact_ordered_assertions(own_context):
    ctx = z3.Context() if own_context else z3.main_ctx()
    a, b = z3.Ints("conjunction_a conjunction_b", ctx=ctx)
    terms = (a == b, a == 7, b == 9, z3.ForAll([b], b + 1 > b))
    assert assertion_conjunction(iter(terms)).eq(z3.And(*terms))
    assert assertion_conjunction(()).eq(z3.And())
    with pytest.raises(ValueError, match='Boolean assertions must share one Z3 context'):
        assertion_conjunction((a,))

