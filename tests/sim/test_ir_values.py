"""Dense and compact carriers implement one recursive Behavior IR value algebra."""

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import typecheck as types
from behave.sim.behavior.audit import _assert_value
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.symbolic.values import (
    copy_value,
    materialize_value,
    value_equal,
    value_matches_type,
    zero_value,
)
from behave.sim.symbolic.uniform_array import UniformArray


def test_recursive_compact_values_compare_extensionally():
    left = UniformArray.uniform(3, UniformArray.uniform(2, 7))
    right = UniformArray.uniform(3, UniformArray.uniform(2, 7))
    dense = [[7, 7], [7, 7], [7, 7]]

    assert left is not right and left.default is not right.default
    assert value_equal(left, right)
    assert value_equal(left, dense)
    assert value_equal(dense, right)
    assert left.extensionally_equal(right)
    assert value_equal(left, dense)


def test_equality_ignores_sparse_write_history_but_not_scalar_type():
    long_history = UniformArray.uniform(4, 0).store(2, 3).store(2, 9)
    short_history = UniformArray.uniform(4, 0).store(2, 9)

    assert value_equal(long_history, short_history)
    assert not value_equal(short_history, short_history.store(1, 1))
    assert not value_equal(True, 1)


def test_zero_preserves_recursive_shape_and_boolean_leaves():
    value = UniformArray.uniform(
        2, UniformArray.uniform(3, UniformArray.uniform(2, True)))
    kind = types.Array(2, types.Array(3, types.Array(2, types.BOOLEAN)))

    zero = zero_value(value)

    assert type(zero) is UniformArray
    assert type(zero.default) is UniformArray
    assert type(zero.default.default) is UniformArray
    assert value_matches_type(zero, kind)
    assert materialize_value(zero) == [
        [[False, False], [False, False], [False, False]],
        [[False, False], [False, False], [False, False]],
    ]
    assert value.zeroed().materialize() == materialize_value(zero)


def test_copy_preserves_value_semantics_without_expanding_compact_arrays():
    compact = UniformArray.uniform(1 << 16, [1, 2])
    dense = [[compact, [3, 4]]]

    copied = copy_value(dense)

    assert copied is not dense and copied[0] is not dense[0]
    assert copied[0][0] is compact
    copied[0][1][0] = 99
    assert dense[0][1] == [3, 4]


def test_symbolic_boundary_accepts_the_same_nested_compact_value():
    concrete = UniformArray.uniform(3, UniformArray.uniform(2, 7))
    symbolic = [
        FunctionalArray.symbolic(2, f"nested_row_{row}", 4, 0, 7)
        for row in range(3)
    ]
    solver = z3.Solver()

    _assert_value(solver, symbolic, concrete)

    assert solver.check() == z3.sat
    model = solver.model()
    assert [[model.eval(row.select(z3.BitVecVal(index, 2))).as_long()
             for index in range(2)]
            for row in symbolic] == [[7, 7], [7, 7], [7, 7]]
