"""Typed monitor connectives preserve exact syntax, contexts and sort checks."""

from itertools import product

import pytest
import z3

from behave.sim.rtl.task_symbolic import Z3Algebra

pytestmark = pytest.mark.solver


@pytest.mark.parametrize("own_context", (False, True))
@pytest.mark.parametrize("operation,constructor,identity", (
    ("land", z3.And, True), ("lor", z3.Or, False)))
def test_connectives_have_identical_ast_for_all_argument_shapes(
        own_context, operation, constructor, identity):
    context = z3.Context() if own_context else z3.main_ctx()
    algebra = Z3Algebra(context)
    p, q = z3.Bools("p q", ctx=context)
    values = (True, False, p, z3.Not(q))
    for length in range(4):
        for terms in product(values, repeat=length):
            result = getattr(algebra, operation)(*terms)
            expected = constructor(*terms, z3.BoolVal(identity, ctx=context))
            assert result.ctx == context and result.eq(expected)


@pytest.mark.parametrize("operation", ("land", "lor"))
def test_connectives_keep_context_and_native_sort_rejection(operation):
    algebra = Z3Algebra()
    for value in (z3.Bool("other", ctx=z3.Context()), z3.BitVec("not_bool", 8), 1, "x"):
        with pytest.raises(z3.Z3Exception):
            getattr(algebra, operation)(value)


def test_monitor_connectives_do_not_repeat_generic_sort_coercion(monkeypatch):
    algebra = Z3Algebra()
    p, q = z3.Bools("p q")
    expected = (z3.And(p, q, True), z3.Or(p, q, False))

    def unexpected(*args):
        pytest.fail("already-typed Boolean operands re-entered generic sort coercion")

    monkeypatch.setattr(z3.z3, "_coerce_expr_list", unexpected)
    assert algebra.land(p, q).eq(expected[0])
    assert algebra.lor(p, q).eq(expected[1])
