"""Equality constructs only the reads used by its exact finite image."""

import pytest
import z3

from behave.sim.symbolic.functional_array import FunctionalArray

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('packed', [False, True])
def test_different_bases_do_not_expand_discarded_symbolic_reads(monkeypatch, private, packed):
    ctx = z3.Context() if private else z3.main_ctx()
    if packed:
        left = FunctionalArray.symbolic(3, 'left', 7, -64, 63).translate(ctx)
        right = FunctionalArray.symbolic(3, 'right', 7, -64, 63).translate(ctx)
    else:
        left = FunctionalArray.uniform(3, z3.BitVecVal(0, 7, ctx=ctx))
        right = FunctionalArray.uniform(3, z3.BitVecVal(1, 7, ctx=ctx))
    address = z3.BitVec('address', 5, ctx=ctx)
    value = z3.BitVec('value', 7, ctx=ctx)
    guard = z3.Bool('guard', ctx=ctx)
    left, right = left.store(address, value, guard), right.store(address, value, guard)
    reads = []
    original = FunctionalArray.select

    def read(array, index):
        assert z3.is_bv_value(index), 'unused symbolic suffix read'
        reads.append(index.as_signed_long())
        return original(array, index)

    with monkeypatch.context() as patch:
        patch.setattr(FunctionalArray, 'select', read)
        equal = left.equal(right, context=ctx)
    assert len(reads) == (0 if packed else 6)
    expected = z3.And(*(left.select(z3.BitVecVal(i, 4, ctx=ctx)) ==
                        right.select(z3.BitVecVal(i, 4, ctx=ctx)) for i in range(3)))
    solver = z3.Solver(ctx=ctx)
    solver.add(equal != expected)
    assert solver.check() == z3.unsat


def test_shared_base_still_checks_only_the_changed_suffix(monkeypatch):
    base = FunctionalArray.symbolic(65536, 'large_base', 7, -64, 63)
    at, value, guard = z3.BitVec('at', 19), z3.BitVec('value', 7), z3.Bool('guard')
    changed = base.store(at, value, guard)
    original, reads = FunctionalArray.select, []

    def read(array, index):
        assert index.eq(at)
        reads.append(index)
        return original(array, index)

    monkeypatch.setattr(FunctionalArray, 'select', read)
    answer = changed.equal(base)
    assert z3.is_bool(answer) and len(reads) == 2


def test_identical_histories_do_not_read_any_cells(monkeypatch):
    base = FunctionalArray.uniform(3, z3.BitVecVal(0, 8)).store(
        z3.BitVec('at', 5), z3.BitVec('value', 8), z3.Bool('guard'))

    def unused(*_args):
        raise AssertionError('identical immutable history requires no read')

    monkeypatch.setattr(FunctionalArray, 'select', unused)
    assert z3.is_true(base.equal(base))


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('length,width', [(1, 1), (1, 8), (3, 5), (16, 8), (31, 3)])
@pytest.mark.parametrize('index_width', [1, 7, 257])
def test_packed_updates_keep_original_signed_index_and_no_wrapped_alias(private, length, width, index_width):
    from behave.sim.symbolic.functional_array import _in_bounds, _Write, _packed_store

    ctx = z3.Context() if private else z3.main_ctx()
    index = z3.BitVec('packed_address', index_width, ctx=ctx)
    packed_width = length * width
    original = z3.BitVec('packed_image', packed_width, ctx=ctx)
    word = z3.BitVec('packed_word', width, ctx=ctx)
    enabled = z3.Bool('packed_enabled', ctx=ctx)
    actual = _packed_store(original, _Write(enabled, index, word), length, width)
    cells = []
    # Enumerate only the oracle's finite domain, never the implementation.
    # A cell not representable by the SIGNED input must not wrap to a valid one.
    for at in range(length):
        previous = z3.Extract((at + 1) * width - 1, at * width, original)
        cells.append(z3.If(z3.And(enabled, index == at), word, previous)
                     if at < 1 << (index_width - 1) else previous)
    expected = cells[0] if length == 1 else z3.Concat(*reversed(cells))
    solver = z3.Solver(ctx=ctx)
    solver.add(actual != expected)
    assert solver.check() == z3.unsat
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Not(_in_bounds(index, length)), actual != original)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('signed', [False, True])
@pytest.mark.parametrize('index_width', [2, 6, 129])
def test_compact_read_store_and_repeated_pack_match_word_oracle(private, signed, index_width):
    ctx = z3.Context() if private else z3.main_ctx()
    length, width = 5, 8
    low, high = (-128, 127) if signed else (0, 127)
    base = FunctionalArray.symbolic(length, 'packed_words', width, low, high).translate(ctx)
    read, write = z3.BitVecs('packed_read packed_write', index_width, ctx=ctx)
    value = z3.BitVec('packed_data', width, ctx=ctx)
    enabled = z3.Bool('packed_enabled', ctx=ctx)
    cells = [base.select(z3.BitVecVal(at, 5, ctx=ctx)) for at in range(length)]
    updated = base.store(write, value, enabled).compact()
    updated = updated.store(write, value + 1, z3.Not(enabled)).compact()
    expected = z3.BitVecVal(0, width, ctx=ctx)
    for at, old in enumerate(cells[:min(length, 1 << (index_width - 1))]):
        cell = z3.If(write == at, z3.If(enabled, value, value + 1), old)
        expected = z3.If(read == at, cell, expected)
    solver = z3.Solver(ctx=ctx)
    solver.add(updated.select(read) != expected)
    assert solver.check() == z3.unsat


def test_large_sparse_packed_offset_does_not_use_memory_width_arithmetic():
    from behave.sim.symbolic.functional_array import _packed_offset

    packed_width = 65536 * 33
    offset = _packed_offset(z3.BitVec('wide_signed_address', 257), packed_width, 33)
    terms, pending = {}, [offset]
    while pending:
        term = pending.pop()
        if term.get_id() in terms:
            continue
        terms[term.get_id()] = term
        pending.extend(term.children())
    multiplications = [term for term in terms.values() if term.decl().kind() == z3.Z3_OP_BMUL]
    assert len(multiplications) == 1 and multiplications[0].size() == packed_width.bit_length()
    assert len(terms) < 30
    assert offset.size() == packed_width


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('length,width,index_width', [(1, 1, 1), (3, 5, 2), (5, 9, 8), (8, 7, 129)])
@pytest.mark.parametrize('signed', [False, True])
def test_indexed_projection_preserves_guarded_updates_choices_and_masks(
        private, length, width, index_width, signed):
    ctx = z3.Context() if private else z3.main_ctx()
    at, where = z3.BitVecs('indexed_read indexed_write', index_width, ctx=ctx)
    word, mask = z3.BitVecs('indexed_word indexed_mask', width + 2, ctx=ctx)
    guard = z3.Bool('indexed_guard', ctx=ctx)
    packed = z3.BitVec('indexed_initial', length * width, ctx=ctx)
    base = FunctionalArray.from_packed(packed, length, width)
    updated = base.store(where, word, guard)
    joined = FunctionalArray.choose(guard, updated, base)
    values = (base, updated, joined, FunctionalArray.merge_bits(
        base, joined, FunctionalArray.uniform(length, mask), width, signed=signed))
    for value in values:
        indexed = value.indexed()
        assert indexed.length == length and indexed.element_width == value.element_width
        solver = z3.Solver(ctx=ctx)
        solver.add(indexed.select(at) != value.select(at))
        assert solver.check() == z3.unsat
        # A later write must still honor the ORIGINAL signed address width,
        # even after the previous image has become an SMT array.
        later = indexed.store(where, word, z3.Not(guard)).indexed()
        expected = value.store(where, word, z3.Not(guard))
        solver.reset()
        solver.add(later.select(at) != expected.select(at))
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('kind', ['uniform', 'memory', 'packed'])
def test_indexed_models_decode_the_original_finite_state(private, kind):
    ctx = z3.Context() if private else z3.main_ctx()
    length, width = 5, 9
    if kind == 'uniform':
        original = FunctionalArray.uniform(length, z3.BitVec('initial_word', width, ctx=ctx))
    elif kind == 'memory':
        original = FunctionalArray.memory(length, 'data', 8, width).translate(ctx)
    else:
        original = FunctionalArray.from_packed(z3.BitVec('initial_words', width * length, ctx=ctx),
                                               length, width)
    index = z3.BitVec('model_address', 16, ctx=ctx)
    data = z3.BitVec('model_data', width, ctx=ctx)
    active = z3.Bool('model_active', ctx=ctx)
    updated = original.store(index, data, active).indexed()
    solver = z3.Solver(ctx=ctx)
    solver.add(active, index == 3, data == -7)
    assert solver.check() == z3.sat
    model = solver.model()
    decode = lambda m, value: m.eval(value, model_completion=True).as_signed_long()
    expected = original.materialize(model, decode)
    expected[3] = -7
    assert updated.materialize(model, decode) == expected
    # Repacking is only a finite projection, not an equality over unobserved
    # out-of-domain cells. Both concrete and symbolic consumers remain valid.
    assert updated.compact().materialize(model, decode) == expected
    translated = updated.translate(z3.Context())
    translated_solver = z3.Solver(ctx=translated._base.value.ctx)
    translated_solver.add(*(term.translate(translated._base.value.ctx) for term in solver.assertions()))
    assert translated_solver.check() == z3.sat
    other_model = translated_solver.model()
    assert translated.materialize(other_model, decode)[3] == -7


@pytest.mark.parametrize('private', [False, True])
def test_indexed_conditional_full_reset_and_finite_equality(private):
    ctx = z3.Context() if private else z3.main_ctx()
    array = FunctionalArray.from_packed(z3.BitVec('reset_initial', 24, ctx=ctx), 3, 8)
    reset = z3.Bool('reset_words', ctx=ctx)
    fill = z3.BitVec('reset_value', 8, ctx=ctx)
    for at in range(3):
        array = array.store(z3.BitVecVal(at, 4, ctx=ctx), fill, reset)
    indexed = array.indexed()
    expected = FunctionalArray.choose(reset, FunctionalArray.uniform(3, fill),
        FunctionalArray.from_packed(z3.BitVec('reset_initial', 24, ctx=ctx), 3, 8))
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Not(indexed.equal(expected, context=ctx)))
    assert solver.check() == z3.unsat
    solver.reset()
    solver.add(z3.Xor(indexed.equal(FunctionalArray.uniform(3, fill), context=ctx),
                     array.equal(FunctionalArray.uniform(3, fill), context=ctx)))
    assert solver.check() == z3.unsat


def test_indexed_projection_shares_images_without_packing_or_leaking_template_parameters(monkeypatch):
    from behave.sim.symbolic import functional_array as arrays

    def forbidden(*_args, **_kwargs):
        raise AssertionError('an indexed projection must not pack a successor')

    monkeypatch.setattr(FunctionalArray, '_packed_value_steps', forbidden)
    base = FunctionalArray.uniform(1024, z3.BitVecVal(0, 33))
    index, word = z3.BitVec('sparse_index', 17), z3.BitVec('sparse_word', 33)
    changed = base.store(index, word, z3.BoolVal(True))
    first = changed.indexed()
    assert first.indexed() is first
    assert first._base.value.eq(changed.indexed()._base.value)
    assert type(first._base) is arrays._IndexedBase
    assert not isinstance(first._base, arrays._ParameterBase)
    with pytest.raises(TypeError, match='template array must be instantiated'):
        FunctionalArray.parameter(3, 'unbound_template', 8).indexed()


def test_indexed_projection_cancels_without_publishing_partial_image():
    base = FunctionalArray.symbolic(1024, 'cancel_array', 8, -128, 127)
    steps = base.indexed_steps()
    for _ in range(8):
        next(steps)
    steps.close()
    assert not base._indexed


@pytest.mark.parametrize('prepared', [False, True])
def test_indexed_image_uses_public_exact_solver_and_retains_decodable_model(prepared):
    from behave.sim.core.query import SolverAnswer
    from behave.sim.symbolic.exact_solve import SolverPreparation, z3_check_finite

    ctx = z3.Context()
    at = z3.BitVec('exact_index', 8, ctx=ctx)
    data = z3.BitVec('exact_data', 9, ctx=ctx)
    mask = z3.BitVec('exact_mask', 9, ctx=ctx)
    base = FunctionalArray.uniform(5, z3.BitVecVal(0, 9, ctx=ctx))
    changed = base.store(at, data, z3.BoolVal(True, ctx=ctx))
    image = FunctionalArray.merge_bits(base, changed, FunctionalArray.uniform(5, mask),
                                      8, signed=False).indexed()
    roots = (at == 3, data == 127, mask == 15)
    target = image.select(at) == 15
    preparation = SolverPreparation(roots) if prepared else None
    try:
        result = z3_check_finite((*roots, target), 10_000, _owned_context=ctx,
                                _prefer_finite=False, _preparation=preparation)
        assert result.answer is SolverAnswer.SAT
        assert all(z3.is_true(result.model.eval(root, model_completion=True)) for root in (*roots, target))
        contradicted = z3_check_finite((*roots, z3.Not(target)), 10_000, _owned_context=ctx,
                                      _prefer_finite=False, _preparation=preparation)
        assert contradicted.answer is SolverAnswer.UNSAT
    finally:
        if preparation is not None:
            preparation.clear()
    assert image.materialize(result.model,
        lambda model, term: model.eval(term, model_completion=True).as_signed_long()) == [0, 0, 0, 15, 0]
