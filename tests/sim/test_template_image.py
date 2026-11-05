"""Reusable image structure must not retain a step's values or another arena."""

import gc
import weakref

import pytest
import z3

from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.symbolic.template_arrays import TemplateArrays
from behave.sim.core.construction_checkpoint import finish_construction

from .test_step_template_lifetime import _array_template

pytestmark = pytest.mark.solver


def _image(*terms):
    return z3.Function('_step_fields', *(term.sort() for term in terms),
                       z3.BoolSort(ctx=terms[0].ctx))(*terms)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('length,width', [(3, 5), (16, 8), (160, 45)])
def test_array_unpack_repack_does_not_accumulate_identity_history(private, length, width):
    context = z3.Context() if private else z3.main_ctx()
    index_width = length.bit_length() + 1
    parameter = z3.Var(0, z3.ArraySort(z3.BitVecSort(index_width, ctx=context),
                                     z3.BitVecSort(width, ctx=context)))
    cells = [z3.Select(parameter, z3.BitVecVal(i, index_width, ctx=context))
             for i in range(length - 1, -1, -1)]
    # A prepared copy still has array reads; the packed base is only known
    # during instantiation. No whole symbolic datapath rewrite is needed.
    plan = TemplateArrays().prepare(_image(z3.Concat(*cells)))
    before = z3.BitVec('unchanged_memory', length * width, ctx=context)
    value = before
    for _ in range(12):
        array = FunctionalArray.from_packed(value, length, width)
        value, = finish_construction(plan.instantiate_steps((array,)))
        assert value.eq(before)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('layout', ['full', 'partial', 'gap', 'overlap', 'reverse', 'different'])
def test_slice_join_is_exact_for_arbitrary_values_and_nonadjacent_blocks(private, layout):
    from behave.sim.symbolic.functional_array import _concat_slices

    context = z3.Context() if private else z3.main_ctx()
    base, other = z3.BitVecs('slice_base slice_other', 16, ctx=context)
    a, b = {'full': ((15, 8), (7, 0)), 'partial': ((12, 8), (7, 3)),
            'gap': ((15, 10), (7, 0)), 'overlap': ((15, 7), (8, 0)),
            'reverse': ((7, 0), (15, 8)), 'different': ((15, 8), (7, 0))}[layout]
    parts = z3.Extract(*a, base), z3.Extract(*b, other if layout == 'different' else base)
    actual = _concat_slices(parts)
    solver = z3.Solver(ctx=context)
    solver.add(actual != z3.Concat(*parts))
    assert solver.check() == z3.unsat
    if layout == 'full':
        assert actual.eq(base)
    elif layout == 'partial':
        assert actual.eq(z3.Extract(12, 3, base))
    else:
        assert actual.eq(z3.Concat(*parts))


def _fixture(context):
    recipes = TemplateArrays()
    address, data, enabled = z3.Var(0, z3.BitVecSort(4)), z3.Var(1, z3.BitVecSort(8)), z3.Var(2, z3.BoolSort())
    array = FunctionalArray.uniform(4, z3.BitVecVal(0, 8)).store(address, data, enabled)
    array_term = recipes.term(array)
    selected = z3.Select(array_term, z3.BitVecVal(3, 4))
    image = _image(selected, array_term, z3.And(enabled, selected == 7))
    if image.ctx != context:
        image = image.translate(context)
    return recipes.prepare(image)


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('operation', ('and', 'or', 'choose', 'store'))
def test_reused_lazy_cones_match_fresh_interpretation_after_guard_and_value_changes(private, operation):
    context = z3.Context() if private else z3.main_ctx()
    recipes = TemplateArrays()
    gate = z3.Var(0, z3.BoolSort())
    value, other = (z3.Var(i, z3.BitVecSort(8)) for i in (1, 2))
    before = FunctionalArray.uniform(4, other)
    array = (FunctionalArray.choose(gate, FunctionalArray.uniform(4, value), before)
             if operation == 'choose' else before.store(z3.BitVecVal(1, 4), value, gate))
    read = z3.Select(recipes.term(array), z3.BitVecVal(1, 4))
    result = {'and': z3.And(gate, read == value), 'or': z3.Or(gate, read == value),
              'choose': read == other, 'store': read == value}[operation]
    image = _image(result, read, value + other)
    if private:
        image = image.translate(context)
    plan = recipes.prepare(image)
    symbolic_gate = z3.Bool('changing_gate', ctx=context)
    symbolic_data = z3.BitVec('changing_data', 8, ctx=context)
    for enabled, data, initial in ((False, 7, 3), (False, 9, 3), (True, 9, 3),
                                   (True, 9, 5), (symbolic_gate, symbolic_data, 5),
                                   (True, symbolic_data, 5), (False, 7, 11)):
        args = (z3.BoolVal(enabled, ctx=context) if type(enabled) is bool else enabled,
                z3.BitVecVal(data, 8, ctx=context) if type(data) is int else data,
                z3.BitVecVal(initial, 8, ctx=context))
        actual = finish_construction(plan.instantiate_steps(args))
        expected = finish_construction(recipes.prepare(image).instantiate_steps(args))
        solver = z3.Solver(ctx=context)
        solver.add(z3.Or(*(a != b for a, b in zip(actual, expected, strict=True))))
        assert solver.check() == z3.unsat


def test_cancelled_value_reuse_keeps_the_last_complete_image_and_owns_its_result():
    plan = _fixture(z3.main_ctx())
    args = (z3.BitVecVal(3, 4), z3.BitVecVal(7, 8), z3.BoolVal(True))
    first = finish_construction(plan.instantiate_steps(args))
    first[0] = z3.BitVecVal(99, 8)  # The caller's result list is not cache storage.
    unfinished = plan.instantiate_steps((args[0], z3.BitVecVal(9, 8), args[2]))
    for _ in range(3):
        next(unfinished)
    unfinished.close()
    selected, _, hit = finish_construction(plan.instantiate_steps(args))
    assert z3.simplify(selected).as_long() == 7 and z3.is_true(z3.simplify(hit))


@pytest.mark.parametrize('private_context', (False, True))
def test_image_reuses_structure_but_recomputes_every_step(private_context):
    context = z3.Context() if private_context else z3.main_ctx()
    plan = _fixture(context)
    values = ((3, 7, True), (2, 9, True), (3, 7, False), (3, 9, True))
    for repetition in range(3):
        structure = dict(plan.nodes)
        for address, data, enabled in values:
            args = (z3.BitVecVal(address, 4, ctx=context), z3.BitVecVal(data, 8, ctx=context),
                    z3.BoolVal(enabled, ctx=context))
            selected, array, hit = finish_construction(plan.instantiate_steps(args))
            expected = data if enabled and address == 3 else 0
            assert z3.simplify(selected).as_long() == expected
            assert z3.simplify(array.select(z3.BitVecVal(3, 4, ctx=context))).as_long() == expected
            assert z3.is_true(z3.simplify(hit)) == (enabled and expected == 7)
        if repetition:
            assert structure.keys() == plan.nodes.keys()
            assert all(plan.nodes[key] is node for key, node in structure.items())


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('normalize', [False, True])
def test_complete_array_observation_shares_history_across_literal_reads(monkeypatch, private, normalize):
    context = z3.Context() if private else z3.main_ctx()
    length, width = 7, 5
    parameter = z3.Var(0, z3.ArraySort(z3.BitVecSort(5, ctx=context), z3.BitVecSort(width, ctx=context)))
    image = _image(*(z3.Select(parameter, z3.BitVecVal(i, 5, ctx=context)) for i in range(length)))
    plan = TemplateArrays().prepare(z3.simplify(image) if normalize else image)
    # One instance decodes the immutable read group. No task/state invariant
    # is inferred; the following two instances have unrelated array values.
    finish_construction(plan.instantiate_steps((FunctionalArray.uniform(length,
        z3.BitVecVal(0, width, ctx=context)),)))
    for epoch in range(2):
        base = FunctionalArray.symbolic(length, f'observed{epoch}', width, -16, 15).translate(context)
        changed = base.store(z3.BitVec(f'write{epoch}', 5, ctx=context),
            z3.BitVec(f'data{epoch}', width, ctx=context), z3.Bool(f'guard{epoch}', ctx=context))
        array = FunctionalArray.choose(z3.Bool(f'branch{epoch}', ctx=context), changed, base)
        expected = [array.select(z3.BitVecVal(i, 5, ctx=context)) for i in range(length)]
        array._selections.clear()

        def no_individual_history(*_args, **_kwargs):
            raise AssertionError('a complete template read repeated the cell history')

        with monkeypatch.context() as patch:
            patch.setattr(FunctionalArray, '_read_plan', no_individual_history)
            actual = finish_construction(plan.instantiate_steps((array,)))
        solver = z3.Solver(ctx=context)
        solver.add(z3.Or(*(a != b for a, b in zip(actual, expected, strict=True))))
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('indices', [(0, 2), (0, 1, 7), (0, 0, 1)])
def test_incomplete_literal_observation_does_not_force_whole_array_packing(monkeypatch, indices):
    parameter = z3.Var(0, z3.ArraySort(z3.BitVecSort(5), z3.BitVecSort(8)))
    plan = TemplateArrays().prepare(_image(*(z3.Select(parameter, z3.BitVecVal(i, 5)) for i in indices)))
    array = FunctionalArray.uniform(3, z3.BitVecVal(42, 8))

    def no_pack(*_args):
        raise AssertionError('a sparse read became a whole finite-array observation')

    monkeypatch.setattr(FunctionalArray, '_packed_value_steps', no_pack)
    for _ in range(2):
        result = finish_construction(plan.instantiate_steps((array,)))
        assert [z3.simplify(value).as_long() for value in result] == [42 if i < 3 else 0 for i in indices]


@pytest.mark.parametrize('private', (False, True))
@pytest.mark.parametrize('operation', ('and', 'or', 'choose'))
def test_structural_decoding_keeps_inactive_array_branches_lazy(private, operation):
    recipes = TemplateArrays()
    gate, data = z3.Var(0, z3.BoolSort()), z3.Var(1, z3.BitVecSort(8))
    array = recipes.term(FunctionalArray.uniform(4, data))
    read = z3.Select(array, z3.BitVecVal(2, 4)) == 7
    expression = (z3.And(gate, read) if operation == 'and' else
                  z3.Or(gate, read) if operation == 'or' else z3.If(gate, read, False))
    context = z3.Context() if private else z3.main_ctx()
    image = _image(expression)
    if private:
        image, read = image.translate(context), read.translate(context)
    plan = recipes.prepare(image)
    inactive = operation == 'or'
    def arguments(enabled, value):
        return z3.BoolVal(enabled, ctx=context), z3.BitVecVal(value, 8, ctx=context)
    result, = finish_construction(plan.instantiate_steps(arguments(inactive, 7)))
    assert z3.is_true(result) if inactive else z3.is_false(result)
    assert read.get_id() not in plan.nodes
    result, = finish_construction(plan.instantiate_steps(arguments(not inactive, 7)))
    assert z3.is_true(z3.simplify(result))
    assert read.get_id() in plan.nodes
    result, = finish_construction(plan.instantiate_steps(arguments(not inactive, 9)))
    assert z3.is_false(z3.simplify(result))


def test_cancelled_instantiation_does_not_publish_partial_step_values():
    plan = _fixture(z3.main_ctx())
    args = (z3.BitVecVal(3, 4), z3.BitVecVal(7, 8), z3.BoolVal(True))
    unfinished = plan.instantiate_steps(args)
    for _ in range(8):
        next(unfinished)
    assert plan.nodes
    unfinished.close()
    selected, _, hit = finish_construction(plan.instantiate_steps(
        (z3.BitVecVal(3, 4), z3.BitVecVal(9, 8), z3.BoolVal(True))))
    assert z3.simplify(selected).as_long() == 9
    assert z3.is_false(z3.simplify(hit))


@pytest.mark.parametrize('operation', ('translate', 'project', 'specialize'))
def test_transition_view_owns_its_own_image_decoding(operation):
    template = _array_template(False)
    template.encode()
    original = template._array_image
    if operation == 'translate':
        view = template.translate(z3.Context())
    elif operation == 'project':
        view = template.project({'hit': ('target',)}, outputs=())
    else:
        view = template.specialize_inputs({'a': 1})
    assert view._array_image is None
    view.encode()
    assert view._array_image is not original
    assert view._array_image.image is view._image
    assert template._array_image is original


def test_private_image_and_runtime_arguments_release_without_cyclic_gc():
    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        template = _array_template(False)
        template.encode()

        def run():
            context = z3.Context()
            view = template.translate(context)
            first = view.encode()
            view.encode(first.state_post)
            return weakref.ref(context), weakref.ref(view._array_image)

        for _ in range(3):
            context, image = run()
            assert context() is None
            assert image() is None
    finally:
        if enabled:
            gc.enable()
        gc.collect()


@pytest.mark.parametrize('private_context', (False, True))
@pytest.mark.parametrize('width', (3, 8, 17))
def test_native_scalar_regions_keep_symbolic_bitvector_semantics(private_context, width):
    context = z3.Context() if private_context else z3.main_ctx()
    kind = z3.BitVecSort(width, ctx=context)
    left, right = z3.Var(0, kind), z3.Var(3, kind)
    guard = z3.Var(2, z3.BoolSort(ctx=context))
    expressions = (left + right, left / right, left % right, left >> right,
                   z3.If(guard, left, right) * right, (left < right) == guard)
    image = _image(*expressions)
    plan = TemplateArrays().prepare(image)
    a, b = z3.BitVecs('a b', width, ctx=context)
    gate = z3.Bool('gate', ctx=context)
    for args in ((a, a, gate, b), (b, a, z3.BoolVal(False, ctx=context), a),
                 (z3.BitVecVal(-1, width, ctx=context), a, gate, z3.BitVecVal(0, width, ctx=context))):
        actual = finish_construction(plan.instantiate_steps(args))
        expected = z3.substitute_vars(image, *args).children()
        solver = z3.Solver(ctx=context)
        solver.add(z3.Or(*(x != y for x, y in zip(actual, expected, strict=True))))
        assert solver.check() == z3.unsat


def test_native_scalar_regions_do_not_substitute_finite_array_parameters():
    recipes = TemplateArrays()
    array = z3.Var(0, z3.ArraySort(z3.BitVecSort(4), z3.BitVecSort(8)))
    value = z3.Var(1, z3.BitVecSort(8))
    image = _image(z3.Select(array, z3.BitVecVal(2, 4)) + value, value * 7)
    plan = recipes.prepare(image)
    for stored, argument in ((3, 9), (11, 2)):
        finite = FunctionalArray.uniform(4, z3.BitVecVal(stored, 8))
        actual = finish_construction(plan.instantiate_steps((finite, z3.BitVecVal(argument, 8))))
        assert [z3.simplify(term).as_long() for term in actual] == [stored + argument, argument * 7]


def test_native_scalar_region_discovery_can_be_cancelled_and_resumed():
    value = z3.Var(0, z3.BitVecSort(16))
    expression = value
    for number in range(64):
        expression = (expression + number) ^ (value * (number + 1))
    plan = TemplateArrays().prepare(_image(expression))
    first = plan.instantiate_steps((z3.BitVecVal(5, 16),))
    for _ in range(12):
        next(first)
    first.close()
    for number in (9, 17):
        argument = z3.BitVecVal(number, 16)
        actual, = finish_construction(plan.instantiate_steps((argument,)))
        assert z3.eq(actual, z3.simplify(z3.substitute_vars(expression, argument)))


def test_literal_arguments_do_not_make_independent_symbols_ground(monkeypatch):
    from z3 import z3core as native

    parameter = z3.Var(0, z3.BitVecSort(16))
    free = z3.BitVec('independent_template_symbol', 16)
    expression = (free * free + parameter) / (free | 1)
    plan = TemplateArrays().prepare(_image(expression, expression + 1))
    argument = z3.BitVecVal(7, 16)
    def no_symbolic_rewrite(ctx, term):
        raise AssertionError('literal parameters do not close an independent symbolic cone')
    with monkeypatch.context() as patch:
        patch.setattr(native, 'Z3_simplify', no_symbolic_rewrite)
        actual = finish_construction(plan.instantiate_steps((argument,)))
    expected = [z3.substitute_vars(root, argument) for root in (expression, expression + 1)]
    solver = z3.Solver()
    solver.add(z3.Or(*(a != b for a, b in zip(actual, expected, strict=True))))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('cancel', (False, True))
def test_interleaved_instances_do_not_mix_literal_bindings(cancel):
    parameter = z3.Var(0, z3.BitVecSort(16))
    expression = (parameter + 3) * (parameter + 5)
    plan = TemplateArrays().prepare(_image(expression))
    def run(value):
        return finish_construction(plan.instantiate_steps((z3.BitVecVal(value, 16),)))[0].as_long()
    assert run(2) == 35
    pending = plan.instantiate_steps((z3.BitVecVal(7, 16),))
    next(pending)
    assert run(11) == 224
    if cancel:
        pending.close()
    else:
        assert finish_construction(pending)[0].as_long() == 120
    assert run(11) == 224
    assert run(7) == 120
