"""Native array cuts retain the scalar semantics without per-cell history reads."""

import pytest
import z3

from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.core.refusal import Refused
from behave.sim.rtl.observation_audit import NativeHistoryBindings


@pytest.mark.parametrize('width', [1, 3, 8, 33, 1025, 4097])
@pytest.mark.parametrize('length', [1, 5])
@pytest.mark.parametrize('private', [False, True])
def test_concrete_word_chunks_preserve_exact_order_and_width(width, length, private):
    from behave.sim.symbolic.functional_array import _packed_literal

    ctx = z3.Context() if private else z3.main_ctx()
    words = [((1 << width) - 1 - index) % (1 << width) for index in range(length)]
    packed = _packed_literal(iter(words), width, ctx)
    assert packed.ctx == ctx and packed.size() == length * width
    for index, value in enumerate(words):
        cell = z3.Extract((index + 1) * width - 1, index * width, packed)
        assert z3.is_true(z3.simplify(cell == z3.BitVecVal(value, width, ctx=ctx)))


def test_concrete_word_chunks_bound_numeral_creation(monkeypatch):
    from behave.sim.symbolic.functional_array import _packed_literal

    original, widths = z3.BitVecVal, []

    def counted(value, width, **kwargs):
        widths.append(width)
        return original(value, width, **kwargs)

    monkeypatch.setattr(z3, 'BitVecVal', counted)
    result = _packed_literal((index % 256 for index in range(4096)), 8, z3.main_ctx())
    assert result.size() == 32768
    assert widths == [1024] * 32


def test_concrete_word_chunks_handle_one_very_wide_word_without_decimal_override():
    import sys
    from behave.sim.symbolic.functional_array import _packed_literal

    original = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        packed = _packed_literal(((1 << 16384) - 7,), 16384, z3.main_ctx())
        assert packed.size() == 16384
        assert z3.simplify(z3.Extract(15, 0, packed)).as_long() == 65529
        assert z3.simplify(z3.Extract(16383, 16368, packed)).as_long() == 65535
        assert sys.get_int_max_str_digits() == 640
    finally:
        sys.set_int_max_str_digits(original)


@pytest.mark.parametrize('words,width', [([], 8), ([0], 0), ([True], 8),
                                       ([-1], 8), ([256], 8), ([0], True)])
def test_concrete_word_chunks_reject_invalid_images(words, width):
    from behave.sim.symbolic.functional_array import _packed_literal

    with pytest.raises((TypeError, ValueError)):
        _packed_literal(words, width, z3.main_ctx())


def test_concrete_word_chunks_honor_cancellation_within_one_wide_word():
    from behave.sim.symbolic.functional_array import _packed_literal

    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError('cancelled')

    with pytest.raises(RuntimeError, match='cancelled'):
        _packed_literal((0,), 32768, z3.main_ctx(), check=cancel)


def _binder():
    native = object.__new__(NativeHistoryBindings)
    native.check = lambda: None
    return native


def _history(length, width, context):
    yes = z3.BoolVal(True, ctx=context)
    choose = z3.Bool('choose_array', ctx=context)
    cells = [z3.BitVec(f'cell_{i}', width, ctx=context) for i in range(length)]
    base = FunctionalArray.from_cells(cells)
    left = base.store(z3.BitVec('address', 8, ctx=context),
                      z3.BitVec('data', width, ctx=context), choose)
    right = base.store(z3.BitVecVal(length - 1, 8, ctx=context), cells[0], yes)
    return FunctionalArray.choose(choose, left, right)


@pytest.mark.parametrize('length', [1, 5])
@pytest.mark.parametrize('carrier,physical', [(1, 8), (3, 8), (8, 8), (12, 4)])
@pytest.mark.parametrize('shape', ['complete', 'partial', 'mixed-width'])
@pytest.mark.parametrize('indexed', [False, True])
def test_array_cut_is_equivalent_to_all_scalar_cuts(length, carrier, physical, shape, indexed):
    context = z3.Context()
    native = _binder()
    array = _history(length, carrier, context)
    if indexed:
        array = array.indexed()
    observations = {('a', i): (physical, (i - 1) % (1 << physical)) for i in range(length)}
    if shape == 'partial':
        observations.pop(('a', length - 1))
    elif shape == 'mixed-width':
        observations['a', length - 1] = physical + 1, 1
    indices = [z3.BitVecVal(i, 32, ctx=context) for i in range(length)]
    scalar_roots = []
    expected = [native._bind_value(array.select(index), ('a', i), observations,
                                   scalar_roots, context) for i, index in enumerate(indices)]
    packed_roots = []
    actual = native._bind_value(array, ('a',), observations, packed_roots, context)
    scalar = z3.And(z3.BoolVal(True, ctx=context), *scalar_roots)
    packed = z3.And(z3.BoolVal(True, ctx=context), *packed_roots)
    solver = z3.Solver(ctx=context)
    solver.add(z3.Xor(scalar, packed))
    assert solver.check() == z3.unsat
    solver.reset()
    # Compare even outside the equality domain: cuts must retain the same
    # carrier high bits and the same unobserved cells as the original path.
    solver.add(z3.Or(*[actual.select(index) != cell for index, cell in zip(indices, expected)]))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('carrier,physical,bits,accepted', [
    (3, 8, 0xff, True), (3, 8, 4, False), (3, 8, 0xfb, False),
    (8, 8, 0xa3, True), (12, 4, 3, True),
])
def test_packed_cut_rejects_unrepresentable_words_and_preserves_high_bits(
        carrier, physical, bits, accepted):
    native = _binder()
    original = z3.BitVec('native_complete_vector', carrier * 3)
    array = FunctionalArray.from_packed(original, 3, carrier)
    observations = {('a', i): (physical, bits) for i in range(3)}
    roots = []
    cut = native._bind_value(array, ('a',), observations, roots, original.ctx)
    solver = z3.Solver()
    solver.add(*roots)
    assert solver.check() == (z3.sat if accepted else z3.unsat)
    solver.add(z3.Or(*[cut.select(z3.BitVecVal(i, 8)) != array.select(z3.BitVecVal(i, 8))
                      for i in range(3)]))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('bits', [-1, 256, True, '0'])
def test_bad_array_observation_keeps_exact_refusal_path(bits):
    native = _binder()
    array = FunctionalArray.uniform(3, z3.BitVecVal(0, 9))
    observations = {('a', i): (8, bits if i == 1 else 0) for i in range(3)}
    with pytest.raises(Refused) as caught:
        native._bind_value(array, ('a',), observations, [], z3.main_ctx())
    assert caught.value.refusal.kind == 'rtl-source-native-value-unreadable'
    assert caught.value.refusal.detail['field'] == ('a', 1)


def test_full_array_binding_does_not_select_each_cell(monkeypatch):
    array = _history(32, 9, z3.main_ctx())
    observations = {('a', i): (8, i) for i in range(array.length)}

    def forbidden(*args):
        raise AssertionError('a complete native cut must compose the array DAG once')

    monkeypatch.setattr(FunctionalArray, 'select', forbidden)
    roots = []
    cut = _binder()._bind_value(array, ('a',), observations, roots, z3.main_ctx())
    assert len(roots) == 1 and cut.writes == ()


@pytest.mark.parametrize('carrier,physical', [(8, 8), (9, 8), (3, 8)])
def test_large_native_vector_does_not_require_unbounded_decimal_conversion(carrier, physical):
    import sys

    limit = sys.get_int_max_str_digits()
    length = 2048
    array = FunctionalArray.uniform(length, z3.BitVecVal(-1, carrier))
    observations = {('a', i): (physical, (1 << physical) - 1) for i in range(length)}
    roots = []
    cut = _binder()._bind_value(array, ('a',), observations, roots, z3.main_ctx())
    assert z3.is_true(z3.simplify(z3.And(*roots)))
    for i in (0, length // 2, length - 1):
        assert z3.simplify(cut.select(z3.BitVecVal(i, 32))).as_signed_long() == -1
    assert sys.get_int_max_str_digits() == limit


def test_array_binding_honors_construction_cancellation():
    native = _binder()
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        if checks == 3:
            raise RuntimeError('cancelled')

    native.check = cancel
    array = FunctionalArray.uniform(32, z3.BitVecVal(0, 8))
    roots = []
    with pytest.raises(RuntimeError, match='cancelled'):
        native._bind_value(array, ('a',), {('a', i): (8, 0) for i in range(32)},
                           roots, z3.main_ctx())
    assert not roots


@pytest.mark.parametrize('length,width', [(0, 8), (2, 3), (True, 8), (1, False)])
def test_packed_array_requires_exact_shape(length, width):
    with pytest.raises(ValueError):
        FunctionalArray.from_packed(z3.BitVecVal(0, 8), length, width)


def _projection(kind, check=lambda: None, extra=None):
    from types import SimpleNamespace
    from behave.sim.rtl.observation_audit import _PhysicalStateProjection
    fields = {'a': SimpleNamespace(observation=('top', 'cells'), port=None)}
    kinds = {'a': kind}
    if extra:
        fields.update(extra[0])
        kinds.update(extra[1])
    return _PhysicalStateProjection(SimpleNamespace(fields=fields, kinds=kinds), check=check)


@pytest.mark.parametrize('carrier,width', [(2, 8), (8, 8), (12, 4)])
@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('partial', [False, True])
def test_packed_observation_is_exactly_every_original_leaf(carrier, width, private, nested, partial):
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_values import ArrayType
    from behave.sim.rtl.symbolic_state import physical_bits

    ctx = z3.Context() if private else z3.main_ctx()
    leaf = ArrayType(2, 0, IntegralType(width, True))
    kind = ArrayType(4, 5, leaf) if nested else leaf
    projection = _projection(kind)
    arrays = [_history(3, carrier, ctx) for _ in range(2 if nested else 1)]
    state = {'a': arrays if nested else arrays[0]}
    observations, expected = {}, []
    for outer, array in enumerate(arrays):
        for index in range(3):
            if partial and index == 1:
                continue
            name = 'top.cells' + (f'[{outer + 4}]' if nested else '') + f'[{2-index}]'
            value = (outer + index - 1) % (1 << width)
            observations[name] = value
            expected.append(physical_bits(array.select(z3.BitVecVal(index, 32, ctx=ctx)), width) != value)
    got = {}
    projection.compare(state, observations, 'state',
        lambda term, value, name: got.update({name: term != value}), got, ctx)
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Xor(z3.Or(*expected), z3.Or(*got.values())))
    assert solver.check() == z3.unsat
    assert len(got) == (len(expected) if partial else len(arrays))


def test_complete_observation_does_not_select_each_array_cell(monkeypatch):
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_values import ArrayType
    ctx = z3.Context()
    projection = _projection(ArrayType(0, 2047, IntegralType(8, False)))
    array = FunctionalArray.uniform(2048, z3.BitVecVal(-1, 9, ctx=ctx))
    def forbidden(*args):
        pytest.fail('complete physical arrays must be compared as packed vectors')
    monkeypatch.setattr(FunctionalArray, 'select', forbidden)
    differences = {}
    observed = {f'top.cells[{i}]': 255 for i in range(2048)}
    projection.compare({'a': array}, observed, 'state', forbidden, differences, ctx)
    assert len(differences) == 1
    assert z3.is_false(z3.simplify(next(iter(differences.values()))))


def test_native_array_comparison_does_not_eagerly_simplify_its_shared_circuit(monkeypatch):
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_values import ArrayType

    ctx = z3.Context()
    projection = _projection(ArrayType(0, 127, IntegralType(8, False)))
    packed = z3.BitVec('observed_array_image', 128 * 8, ctx=ctx)
    array = FunctionalArray.from_packed(packed, 128, 8)
    observed = {f'top.cells[{i}]': i for i in range(128)}

    def forbidden(*args, **kwargs):
        pytest.fail('native projection must not eagerly normalize a whole array circuit')

    monkeypatch.setattr(z3, 'simplify', forbidden)
    differences = {}
    projection.compare({'a': array}, observed, 'state', forbidden, differences, ctx)
    assert len(differences) == 1
    defect = next(iter(differences.values()))
    # Z3Py may put a concrete numeral first via reflected comparison.
    # The shared symbolic image itself must remain an untouched operand.
    assert any(operand.eq(packed) for operand in defect.children())
    solver = z3.Solver(ctx=ctx)
    solver.add(defect, packed == sum(i << (8 * i) for i in range(128)))
    assert solver.check() == z3.unsat


def test_array_comparison_keys_cannot_shadow_a_real_native_leaf():
    from types import SimpleNamespace
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_values import ArrayType
    kind = IntegralType(8, False)
    projection = _projection(ArrayType(0, 1, kind), extra=(
        {'b': SimpleNamespace(observation='array:top.cells', port=None)}, {'b': kind}))
    state = {'a': FunctionalArray.uniform(2, z3.BitVecVal(0, 8)), 'b': z3.BitVecVal(1, 8)}
    observed = {'top.cells[0]': 0, 'top.cells[1]': 0, 'array:top.cells': 0}
    differences = {}
    projection.compare(state, observed, 'state',
        lambda term, value, name: differences.update({name: term != value}), differences, z3.main_ctx())
    assert len(differences) == 2
    assert z3.is_true(z3.simplify(z3.Or(*differences.values())))


@pytest.mark.parametrize('bad', [-1, 256, True, '0'])
def test_packed_observation_rejects_unreadable_physical_bits(bad):
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_values import ArrayType
    projection = _projection(ArrayType(0, 1, IntegralType(8, False)))
    with pytest.raises(Refused, match='physical width'):
        projection.compare({'a': FunctionalArray.uniform(2, z3.BitVecVal(0, 8))},
            {'top.cells[0]': 0, 'top.cells[1]': bad}, 'state', None, {}, z3.main_ctx())


def test_packed_observation_checks_cancellation_and_unknown_fields():
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_values import ArrayType
    projection = _projection(ArrayType(0, 1, IntegralType(8, False)))
    state = {'a': FunctionalArray.uniform(2, z3.BitVecVal(0, 8))}
    with pytest.raises(Refused) as caught:
        projection.compare(state, {'unknown': 0}, 'state', lambda *args: None, {}, z3.main_ctx())
    assert caught.value.refusal.kind == 'rtl-source-native-storage-unbound'
    def cancel():
        raise InterruptedError('cancelled observation')
    projection.check = cancel
    with pytest.raises(InterruptedError, match='cancelled observation'):
        projection.compare(state, {'top.cells[0]': 0, 'top.cells[1]': 0}, 'state', None, {}, z3.main_ctx())
