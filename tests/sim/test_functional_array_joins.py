"""Conditional whole-array replacement keeps a shared, exact version DAG."""

import pytest

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import functional_array as arrays
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.symbolic.template_arrays import TemplateArrays


def _initial():
    return tuple(FunctionalArray.uniform(3, z3.BitVecVal(0, 9)).store(
        z3.BitVecVal(i, 4), z3.BitVecVal(i + 1, 9), z3.BoolVal(True))
        for i in range(2))


def _swaps(count):
    left, right = _initial()
    expected = ([z3.BitVecVal(i, 9) for i in (1, 0, 0)],
                [z3.BitVecVal(i, 9) for i in (0, 2, 0)])
    for step in range(count):
        cond = z3.Bool(f"version_swap_{step}")
        left, right = (FunctionalArray.choose(cond, right, left),
                       FunctionalArray.choose(cond, left, right))
        a, b = expected
        expected = ([z3.If(cond, y, x) for x, y in zip(a, b)],
                    [z3.If(cond, x, y) for x, y in zip(a, b)])
    return left, right, expected


def test_divergent_versions_do_not_duplicate_write_history(monkeypatch):
    rows = []
    original = arrays._Write.__post_init__

    def counted(self):
        rows.append(self)
        original(self)

    monkeypatch.setattr(arrays._Write, "__post_init__", counted)
    left, right, _ = _swaps(7)
    assert len(rows) <= 2 + 4 * 7
    assert len(left.formula_terms) <= 3 * 2 + 7
    assert len(right.formula_terms) <= 3 * 2 + 7
    assert len(left.range_cases()) <= 3


def test_successive_packing_stops_at_completed_version_images(monkeypatch):
    context = z3.Context()
    left = FunctionalArray.from_packed(z3.BitVec('packed_left', 27, ctx=context), 3, 9)
    right = FunctionalArray.from_packed(z3.BitVec('packed_right', 27, ctx=context), 3, 9)
    walk = arrays._packed_versions
    visited = []

    def counted(root, *args, **kwargs):
        for version in walk(root, *args, **kwargs):
            visited.append(version)
            yield version

    monkeypatch.setattr(arrays, '_packed_versions', counted)
    for step in range(32):
        condition = z3.Bool(f'packed_swap_{step}', ctx=context)
        left, right = (FunctionalArray.choose(condition, right, left),
                       FunctionalArray.choose(condition, left, right))
        left._packed_value(5, context)
        right._packed_value(12, context)
    # Each immutable version has at most the two requested image widths, not
    # one visit per history path or an unnecessary wide canonical image.
    assert len(visited) <= 4 + 4 * 32
    cached = left._packed_value(5, context), right._packed_value(12, context)
    # Clearing only completed images restores the original whole-DAG path.
    # Exact ASTs must agree, including truncation and sign extension.
    for root in (left, right):
        for version in arrays._array_versions(root):
            version._packed.clear()
    assert cached[0].eq(left._packed_value(5, context))
    assert cached[1].eq(right._packed_value(12, context))


@pytest.mark.parametrize("count", [1, 4, 7])
def test_divergent_choices_match_dense_cells_and_compaction(count):
    left, right, expected = _swaps(count)
    read = z3.BitVec("version_read", 4)
    for array, cells in zip((left, right), expected):
        dense = z3.BitVecVal(0, 9)
        for index, value in enumerate(cells):
            dense = z3.If(read == index, value, dense)
        solver = z3.SolverFor("QF_BV")
        solver.add(z3.Or(array.select(read) != dense,
                         array.compact().select(read) != dense))
        assert solver.check() == z3.unsat


def test_shared_choice_dag_preserves_widening_stores_and_template_recipes():
    left, right, expected = _swaps(4)
    array = FunctionalArray.choose(z3.Bool("version_last"), left, right.widen(13))
    address = z3.BitVec("version_write", 4)
    enabled = z3.Bool("version_enabled")
    array = array.store(address, z3.BitVecVal(-99, 13), enabled)
    recipes = TemplateArrays()
    term = recipes.term(array)
    restored = recipes.instantiate(term, ())
    recipes.freeze()
    read = z3.BitVec("version_recipe_read", 4)
    cells = [z3.If(z3.And(enabled, address == i), z3.BitVecVal(-99, 13),
                   z3.SignExt(4, z3.If(z3.Bool("version_last"), a, b)))
             for i, (a, b) in enumerate(zip(*expected))]
    dense = z3.BitVecVal(0, 13)
    for i, value in enumerate(cells):
        dense = z3.If(read == i, value, dense)
    solver = z3.SolverFor("QF_BV")
    solver.add(z3.Or(array.select(read) != dense, restored.select(read) != dense))
    assert solver.check() == z3.unsat
    context = z3.Context()
    translated = array.translate(context)
    check = z3.SolverFor("QF_BV", ctx=context)
    check.add(translated.select(read.translate(context)) != dense.translate(context))
    assert check.check() == z3.unsat


def test_scalar_argument_normalization_shares_overlapping_roots(monkeypatch):
    import weakref

    from z3 import z3core as native
    from z3.z3 import _to_expr_ref
    from behave.sim.core.construction_checkpoint import finish_construction

    context = z3.Context()
    word = z3.BitVecSort(16, ctx=context)
    parameters = [z3.Var(index, word) for index in range(32)]
    fields = (sum(parameters), parameters[0] == 0,
              z3.If(parameters[1] == 0, parameters[0], parameters[-1]))
    image = z3.Function('_step_fields', *(field.sort() for field in fields),
                        z3.BoolSort(ctx=context))(*fields)
    prepared = TemplateArrays().prepare(image)
    shared = z3.BitVec('argument_shared', 16, ctx=context)
    roots = []
    for index in range(32):
        shared = z3.If(shared > index, shared + index + 1, shared ^ (index + 7))
        roots.append(shared)
    simplify, calls = native.Z3_simplify, []
    application, push = z3.FuncDeclRef.__call__, native.Z3_ast_vector_push
    unretained = {}

    def watched_application(function, *args):
        value = application(function, *args)
        unretained[value.as_ast().value] = weakref.ref(value)
        return value

    def retain_alive(ctx, vector, term):
        pending = unretained.pop(term.value, None)
        if pending is not None:
            # Reading .as_ast() from a temporary must not release its sole
            # Python owner before the vector acquires the native reference.
            assert pending() is not None
        return push(ctx, vector, term)

    def measured(ctx, term):
        value = _to_expr_ref(term, context)
        if z3.is_app(value) and value.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            calls.append(value.num_args())
        return simplify(ctx, term)

    monkeypatch.setattr(native, 'Z3_simplify', measured)
    monkeypatch.setattr(z3.FuncDeclRef, '__call__', watched_application)
    monkeypatch.setattr(native, 'Z3_ast_vector_push', retain_alive)
    got = finish_construction(prepared.instantiate_steps(roots))
    assert calls == [32]
    expected = z3.substitute_vars(image, *roots)
    solver = z3.Solver(ctx=context)
    solver.add(z3.Or(*(a != b for a, b in zip(got, expected.children(), strict=True))))
    assert solver.check() == z3.unsat
    # A later invocation has different actual values and owns a fresh cache.
    ground = [z3.ZeroExt(8, z3.BitVecVal(index, 8, ctx=context)) for index in range(32)]
    restored = finish_construction(prepared.instantiate_steps(ground))
    expected = z3.simplify(z3.substitute_vars(image, *ground))
    assert all(z3.is_true(z3.simplify(a == b))
               for a, b in zip(restored, expected.children(), strict=True))


def test_choice_range_sources_keep_their_path_conditions():
    left = FunctionalArray.uniform(2, z3.BitVecVal(-8, 6)).store(
        z3.BitVecVal(0, 3), z3.BitVecVal(5, 6), z3.BoolVal(True))
    right = FunctionalArray.uniform(2, z3.BitVecVal(3, 6)).store(
        z3.BitVecVal(1, 3), z3.BitVecVal(17, 6), z3.BoolVal(True))
    cond = z3.Bool("version_range_condition")
    array = FunctionalArray.choose(cond, left, right)
    for guard, term, intrinsic in array.range_cases():
        assert intrinsic is None
        value = term.as_signed_long()
        solver = z3.Solver()
        solver.add(guard != (cond if value in (-8, 5) else z3.Not(cond)))
        assert solver.check() == z3.unsat
    solver = z3.Solver()
    solver.add(cond)
    assert solver.check() == z3.sat
    assert array.materialize(solver.model(), lambda m, t: m.eval(t).as_signed_long()) == [5, -8]


@pytest.mark.parametrize("operation", ["select", "recipe", "pack", "translate", "decode"])
def test_deep_version_dag_does_not_use_the_python_call_stack(operation):
    array, _, _ = _swaps(512)
    if operation == "select":
        assert array.select(z3.BitVecVal(0, 4)).size() == 9
    elif operation == "recipe":
        recipes = TemplateArrays()
        restored = recipes.instantiate(recipes.term(array), ())
        assert restored.length == array.length
    elif operation == "pack":
        assert array.compact().element_width == 9
    elif operation == "translate":
        context = z3.Context()
        translated = array.translate(context)
        assert all(term.ctx == context for term in translated.formula_terms)
    else:
        solver = z3.Solver()
        solver.add(*(z3.Not(z3.Bool(f"version_swap_{index}")) for index in range(512)))
        assert solver.check() == z3.sat
        assert array.materialize(solver.model(), lambda m, t: m.eval(t).as_signed_long()) == [1, 0, 0]
