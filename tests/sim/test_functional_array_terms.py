"""Typed read construction retains Z3's original formulas and arena rules."""
import pytest
import z3

from behave.sim.symbolic.functional_array import _and_predicates, _choose, _same_index

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('length,width', [(1, 1), (3, 5), (32, 32), (64, 32), (160, 45)])
def test_packed_dynamic_write_keeps_validity_outside_the_shift(private, length, width):
    from behave.sim.symbolic.functional_array import _packed_store, _Write

    ctx = z3.Context() if private else z3.main_ctx()
    index = z3.BitVec('factored_write_index', 257, ctx=ctx)
    word = z3.BitVec('factored_write_word', width, ctx=ctx)
    previous = z3.BitVec('factored_write_memory', length * width, ctx=ctx)
    guard = z3.Bool('factored_write_guard', ctx=ctx)
    actual = _packed_store(previous, _Write(guard, index, word), length, width)
    nodes, pending, shifts = {}, [actual], []
    while pending:
        term = pending.pop()
        if term.get_id() in nodes:
            continue
        nodes[term.get_id()] = term
        if term.decl().kind() == z3.Z3_OP_BSHL:
            shifts.append(term)
        pending.extend(term.children())
    assert len(shifts) == 2
    assert sum(term.decl().kind() == z3.Z3_OP_ITE for term in nodes.values()) == 1
    # The only conditional is the whole update. Signed invalid addresses
    # cannot become writes through the truncated low address bits.
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Or(index < 0, index >= length), actual != previous)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('copies', [1, 2, 17, 513])
@pytest.mark.parametrize('suffix', ['none', 'independent', 'same-word', 'other-bit'])
def test_concat_sign_runs_remain_compact_and_exact(private, copies, suffix):
    from behave.sim.symbolic.functional_array import _concat_slices
    from behave.sim.symbolic.term_bounds import _SyntacticClosureProof

    context = z3.Context() if private else z3.main_ctx()
    word = z3.BitVec('concat_word', 9, ctx=context)
    bit = z3.Extract(8, 8, word)
    other = z3.BitVec('concat_other_bit', 1, ctx=context)
    tail = z3.BitVec('concat_independent_tail', 9, ctx=context)
    fields = [bit] * copies + {
        'none': [], 'independent': [tail],
        'same-word': [z3.Extract(7, 0, word)], 'other-bit': [other, tail],
    }[suffix]
    original = fields[0] if len(fields) == 1 else z3.Concat(*fields)
    compact = _concat_slices(iter(fields))
    assert compact.size() == original.size()
    solver = z3.Solver(ctx=context)
    solver.add(compact != original)
    assert solver.check() == z3.unsat
    # Range consumers must retain the repeated-sign relationship too, not
    # merely accept an equivalent formula with weaker control pruning.
    proof = _SyntacticClosureProof({})
    assert proof.coarse_signed_hull(compact) == proof.coarse_signed_hull(original)
    pending, seen = [compact], set()
    while pending:
        term = pending.pop()
        if term.get_id() not in seen:
            seen.add(term.get_id())
            pending.extend(term.children())
    assert len(seen) <= 8  # independent of the extension width


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('copies', [2, 17, 513])
def test_compact_sign_prefix_remains_compact_when_composed(private, copies):
    from behave.sim.symbolic.functional_array import _concat_slices
    from behave.sim.symbolic.term_bounds import _SyntacticClosureProof

    context = z3.Context() if private else z3.main_ctx()
    bit = z3.BitVec('composed_sign_bit', 1, ctx=context)
    tail = z3.BitVec('composed_tail', 9, ctx=context)
    prefix = _concat_slices([bit] * copies)
    composed = _concat_slices((prefix, tail))
    flat = _concat_slices([bit] * copies + [tail])
    assert z3.eq(composed, flat)
    proof = _SyntacticClosureProof({})
    assert proof.coarse_signed_hull(composed).lo == -512
    assert proof.coarse_signed_hull(composed).hi == 511
    solver = z3.Solver(ctx=context)
    solver.add(composed != z3.Concat(prefix, tail))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
def test_concat_compaction_keeps_mixed_bits_and_adjacent_slices(private):
    from behave.sim.symbolic.functional_array import _concat_slices

    context = z3.Context() if private else z3.main_ctx()
    first, second = z3.BitVecs('concat_first concat_second', 1, ctx=context)
    word = z3.BitVec('concat_sliced_word', 8, ctx=context)
    fields = [first, second, first, z3.Extract(7, 4, word), z3.Extract(3, 0, word)]
    compact = _concat_slices(fields)
    assert z3.eq(compact, z3.Concat(first, second, first, word))
    solver = z3.Solver(ctx=context)
    solver.add(compact != z3.Concat(*fields))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('length,width,index_width', [
    (1, 1, 1), (1, 7, 8), (2, 7, 1), (3, 5, 2), (5, 9, 7),
    (8, 1, 5), (16, 7, 3), (17, 1, 9), (32, 9, 8), (3, 65, 65),
])
def test_packed_selection_matches_every_finite_cell(private, length, width, index_width):
    from behave.sim.symbolic.functional_array import FunctionalArray

    context = z3.Context() if private else z3.main_ctx()
    value = z3.BitVec('packed_words', length * width, ctx=context)
    index = z3.BitVec('packed_address', index_width, ctx=context)
    expected = z3.BitVecVal(0, width, ctx=context)
    # The address is signed. Cells not representable by this address must not
    # alias negative values when their literal is converted to a bit vector.
    for cell in range(min(length, 1 << (index_width - 1))):
        expected = z3.If(index == cell,
            z3.Extract((cell + 1) * width - 1, cell * width, value), expected)
    array = FunctionalArray.from_packed(value, length, width)
    solver = z3.Solver(ctx=context)
    solver.add(array.select(index) != expected)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('length,width', [(1, 1), (3, 7), (64, 32), (65, 33), (160, 45)])
def test_packed_word_selection_does_not_shift_the_whole_memory(length, width):
    from behave.sim.symbolic.functional_array import _packed_select

    packed = z3.BitVec('word_select_image', length * width)
    index = z3.BitVec('word_select_address', 257)
    result = _packed_select(packed, index, width)
    pending, seen, selected_bits = [result], set(), 0
    while pending:
        term = pending.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        kind = term.decl().kind()
        assert kind not in (z3.Z3_OP_BLSHR, z3.Z3_OP_BSHL, z3.Z3_OP_BASHR)
        if kind == z3.Z3_OP_ITE:
            selected_bits += term.size()
        pending.extend(term.children())
    assert selected_bits <= (1 << (length - 1).bit_length()) * width


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('length,width,index_width', [
    (1, 1, 1), (1, 7, 8), (2, 7, 1), (3, 5, 2), (5, 9, 7),
    (8, 1, 5), (16, 7, 3), (17, 1, 9), (32, 9, 8), (3, 65, 65),
])
def test_packed_writeback_preserves_every_cell_guard_and_invalid_address(private, length, width, index_width):
    from behave.sim.symbolic.functional_array import _Write, _packed_store

    context = z3.Context() if private else z3.main_ctx()
    original = z3.BitVec('write_image', length * width, ctx=context)
    index = z3.BitVec('write_address', index_width, ctx=context)
    word = z3.BitVec('written_word', width + 3, ctx=context)
    guard = z3.Bool('write_guard', ctx=context)
    cells = []
    for cell in range(length):
        previous = z3.Extract((cell + 1) * width - 1, cell * width, original)
        cells.append(z3.If(z3.And(guard, index == cell), z3.Extract(width - 1, 0, word), previous)
                     if cell < 1 << (index_width - 1) else previous)
    expected = cells[0] if length == 1 else z3.Concat(*reversed(cells))
    updated = _packed_store(original, _Write(guard, index, word), length, width)
    solver = z3.Solver(ctx=context)
    solver.add(updated != expected)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('length,width,index_width', [(5, 7, 8), (16, 9, 5), (3, 65, 2)])
def test_packed_store_chain_matches_component_updates(private, length, width, index_width):
    from behave.sim.symbolic.functional_array import FunctionalArray, _Write, _packed_store

    context = z3.Context() if private else z3.main_ctx()
    packed = z3.BitVec('chain_image', length * width, ctx=context)
    cells = [z3.Extract((i+1)*width-1, i*width, packed) for i in range(length)]
    for step, word_width in enumerate((width, width + 3, max(1, width - 2))):
        index = z3.BitVec(f'chain_address_{step}', index_width, ctx=context)
        word = z3.BitVec(f'chain_value_{step}', word_width, ctx=context)
        guard = z3.Bool(f'chain_guard_{step}', ctx=context)
        packed = _packed_store(packed, _Write(guard, index, word), length, width)
        resized = (word if word_width == width else
                   z3.Extract(width - 1, 0, word) if word_width > width else
                   z3.SignExt(width - word_width, word))
        cells = [z3.If(z3.And(guard, index == i), resized, previous)
                 if i < 1 << (index_width - 1) else previous
                 for i, previous in enumerate(cells)]
    address = z3.BitVec('chain_read', index_width, ctx=context)
    expected = z3.BitVecVal(0, width, ctx=context)
    for i in range(min(length, 1 << (index_width - 1))):
        expected = z3.If(address == i, cells[i], expected)
    solver = z3.Solver(ctx=context)
    solver.add(FunctionalArray.from_packed(packed, length, width).select(address) != expected)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('width', [1, 7, 65])
def test_typed_read_operations_keep_identical_ast(private, width):
    context = z3.Context() if private else z3.main_ctx()
    p, q = z3.Bools('typed_p typed_q', ctx=context)
    a, b = z3.BitVecs('typed_a typed_b', width, ctx=context)
    assert z3.eq(_choose(p, a, b), z3.If(p, a, b))
    assert z3.eq(_and_predicates(p, q), z3.And(p, q))
    assert z3.eq(_same_index(a, b), a == b)
    literal = z3.BitVecVal(-1, width, ctx=context)
    # Z3Py dispatches the numeral subclass first, including when it is on the
    # right. Preserve the equality's original orientation as well as meaning.
    assert z3.eq(_same_index(a, literal), a == literal)
    assert z3.eq(_same_index(literal, a), literal == a)
    wider = z3.BitVec('typed_wider', width + 3, ctx=context)
    assert z3.eq(_same_index(a, wider), z3.SignExt(3, a) == wider)
    assert z3.eq(_same_index(wider, a), wider == z3.SignExt(3, a))
    assert _choose(p, a, a) is a
    assert _choose(z3.BoolVal(True, ctx=context), a, b) is a
    assert _choose(z3.BoolVal(False, ctx=context), a, b) is b
    assert z3.is_true(_same_index(z3.BitVecVal(-1, width, ctx=context),
                                z3.BitVecVal(-1, width + 3, ctx=context)))


def test_native_read_construction_rejects_foreign_arenas():
    context = z3.Context()
    local_p, foreign_p = z3.Bool('p'), z3.Bool('p', ctx=context)
    local_b, foreign_b = z3.BitVec('b', 7), z3.BitVec('b', 7, ctx=context)
    for call in (lambda: _choose(local_p, local_b, foreign_b),
                 lambda: _and_predicates(local_p, foreign_p),
                 lambda: _same_index(local_b, foreign_b)):
        with pytest.raises(z3.Z3Exception, match='context mismatch'):
            call()


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('widths', [(1, 1), (7, 7), (1, 7), (7, 1), (32, 65), (65, 32)])
def test_index_alias_keeps_original_ast_across_signed_widths(private, widths):
    context = z3.Context() if private else z3.main_ctx()

    def terms(width, label):
        symbol = z3.BitVec(label, width, ctx=context)
        numeral = z3.BitVecVal(-1, width, ctx=context)
        return (symbol, numeral, z3.BitVecVal(0, width, ctx=context),
                symbol + 1)

    for left in terms(widths[0], 'alias_left'):
        for right in terms(widths[1], 'alias_right'):
            if z3.eq(left, right):
                expected = z3.BoolVal(True, ctx=context)
            elif z3.is_bv_value(left) and z3.is_bv_value(right):
                expected = z3.BoolVal(
                    left.as_signed_long() == right.as_signed_long(), ctx=context)
            else:
                size = max(left.size(), right.size())
                wide_left = left if left.size() == size else z3.SignExt(size - left.size(), left)
                wide_right = right if right.size() == size else z3.SignExt(size - right.size(), right)
                expected = wide_left == wide_right
            assert z3.eq(_same_index(left, right), expected)

    # Check reflected dispatch by wrapper type, not AST kind. Do not feed two
    # manually rewrapped numerals to the old constant shortcut: a BitVecRef
    # intentionally lacks the NumRef's as_signed_long method.
    symbol = z3.BitVec('wrapped_alias', widths[0], ctx=context)
    numeral = z3.BitVecVal(-1, widths[0], ctx=context)
    wrapped = z3.BitVecRef(numeral.as_ast(), context)
    assert z3.eq(_same_index(symbol, wrapped), symbol == wrapped)
    assert z3.eq(_same_index(wrapped, symbol), wrapped == symbol)
