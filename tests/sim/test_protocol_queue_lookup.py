"""Queue value-function reuse preserves every symbolic head, not seed samples."""

import pytest
import z3

from behave.sim import api
from behave.sim.rtl.protocol_relation import _QueuePayload

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('length', [0, 1, 7, 100])
@pytest.mark.parametrize('explicit_context', [False, True])
@pytest.mark.parametrize('compact', [False, True])
def test_shared_queue_lookup_equals_expanded_mux_for_all_heads(length, explicit_context, compact):
    context = z3.Context() if explicit_context else None
    endpoint = api.Endpoint('input', api.Direction.IN,
        [api.Field('flag', api.FiniteType(1), 'flag'),
         api.Field('value', api.FiniteType(12, signed=True), 'value')],
        api.ready_valid('in', 'valid', 'ready'), clock='clk', kind=api.Kind.TRANSACTION)
    rows = tuple((z3.Bool(f'flag_{i}', ctx=context), z3.BitVec(f'value_{i}', 12, ctx=context))
                 for i in range(length))
    head, present = z3.Int('head', ctx=context), z3.Bool('present', ctx=context)
    lookup = _QueuePayload(endpoint, rows, context, compact=compact)
    actual = lookup.at(head, present)
    expected = []
    for column, zero in enumerate(lookup.zero):
        value = zero
        for index, row in reversed(tuple(enumerate(rows))):
            value = z3.If(head == index, row[column], value)
        expected.append(z3.If(present, value, zero))
    solver = z3.Solver(ctx=context)
    solver.add(*lookup.constraints, z3.Or(*(left != right for left, right in zip(actual, expected))))
    assert solver.check() == z3.unsat
    # The selector has no range assumption: negative/exhausted heads are zero,
    # and no fresh placeholder remains in the instantiated formula.
    solver = z3.Solver(ctx=context)
    solver.add(z3.Or(head < 0, head >= length), z3.Or(
        *(value != zero for value, zero in zip(actual, lookup.zero))))
    assert solver.check() == z3.unsat
    for term in actual:
        pending, seen = [term], set()
        while pending:
            node = pending.pop()
            if node.get_id() in seen:
                continue
            seen.add(node.get_id())
            assert not node.eq(lookup.index)
            pending.extend(node.children())


@pytest.mark.parametrize('explicit_context', [False, True])
def test_compact_lookup_preserves_distinct_queue_valuations(explicit_context):
    context = z3.Context() if explicit_context else None
    endpoint = api.Endpoint('input', api.Direction.IN,
        [api.Field('value', api.FiniteType(8), 'value')],
        api.ready_valid('in', 'valid', 'ready'), clock='clk', kind=api.Kind.TRANSACTION)
    a, b = z3.BitVecs('a b', 8, ctx=context)
    left = _QueuePayload(endpoint, ((a,),), context, compact=True)
    right = _QueuePayload(endpoint, ((b,),), context, compact=True)
    solver = z3.Solver(ctx=context)
    solver.add(*left.constraints, *right.constraints, a == 23, b == 91)
    assert solver.check() == z3.sat  # The equivalence miter must not be vacuous.
    model = solver.model()
    head, present = z3.IntVal(0, ctx=context), z3.BoolVal(True, ctx=context)
    assert model.eval(left.at(head, present)[0]).as_long() == 23
    assert model.eval(right.at(head, present)[0]).as_long() == 91


@pytest.mark.parametrize('length', [0, 1, 7, 100, 128])
@pytest.mark.parametrize('explicit_context', [False, True])
def test_finite_index_table_equals_integer_lookup(length, explicit_context):
    context = z3.Context() if explicit_context else None
    endpoint = api.Endpoint('input', api.Direction.IN,
        [api.Field('value', api.FiniteType(12), 'value')],
        api.ready_valid('in', 'valid', 'ready'), clock='clk', kind=api.Kind.TRANSACTION)
    rows = tuple((z3.BitVec(f'value_{i}', 12, ctx=context),) for i in range(length))
    head, present = z3.BitVec('head', 7, ctx=context), z3.Bool('present', ctx=context)
    finite = _QueuePayload(endpoint, rows, context, compact=True, index_sort=head.sort())
    integer = _QueuePayload(endpoint, rows, context, compact=True)
    solver = z3.Solver(ctx=context)
    solver.add(*finite.constraints, *integer.constraints)
    assert solver.check() == z3.sat
    # Exhaust the finite head carrier; queue values and presence stay symbolic.
    # This proves all heads without asking an array/Int/BV bridge to discover
    # the same case split. No small-head-only testing or sampled payloads.
    for index in range(1 << head.size()):
        literal = z3.BitVecVal(index, head.size(), ctx=context)
        solver.push()
        solver.add(finite.at(literal, present)[0] !=
                   integer.at(z3.IntVal(index, ctx=context), present)[0])
        assert solver.check() == z3.unsat
        solver.pop()
    # The lookup has no private array interpretation to complete or constrain.
    assert finite.definitions == finite.constraints == ()


@pytest.mark.parametrize('widths', [(1,), (1, 1, 1), (3, 1, 9, 65)])
@pytest.mark.parametrize('length', [0, 1, 7, 32])
@pytest.mark.parametrize('explicit_context', [False, True])
def test_balanced_lookup_has_no_array_theory_and_keeps_every_field(widths, length, explicit_context):
    context = z3.Context() if explicit_context else None
    endpoint = api.Endpoint('input', api.Direction.IN,
        [api.Field(f'f{i}', api.FiniteType(width, signed=width > 1), f'f{i}')
         for i, width in enumerate(widths)],
        api.ready_valid('in', 'valid', 'ready'), clock='clk', kind=api.Kind.TRANSACTION)
    rows = tuple(tuple(z3.Bool(f'f{i}_{row}', ctx=context) if width == 1 else
                       z3.BitVec(f'f{i}_{row}', width, ctx=context)
                       for i, width in enumerate(widths)) for row in range(length))
    head, present = z3.BitVec('head', 5, ctx=context), z3.Bool('present', ctx=context)
    finite = _QueuePayload(endpoint, rows, context, compact=True, index_sort=head.sort())
    linear = _QueuePayload(endpoint, rows, context, index_sort=head.sort())
    actual, expected = finite.at(head, present), linear.at(head, present)
    solver = z3.Solver(ctx=context)
    solver.add(z3.Or(*(left != right for left, right in zip(actual, expected, strict=True))))
    assert solver.check() == z3.unsat
    pending, seen = list(actual), set()
    while pending:
        term = pending.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        assert not z3.is_array(term) and not z3.is_select(term)
        pending.extend(term.children())
    assert finite.constraints == finite.definitions == ()


def test_balanced_lookup_depth_is_logarithmic_and_empty_payloads_stay_empty():
    from types import SimpleNamespace

    endpoint = SimpleNamespace(fields=[SimpleNamespace(type=api.FiniteType(8))])
    for length in (1, 2, 7, 100, 257):
        rows = tuple((z3.BitVec(f'cell_{length}_{i}', 8),) for i in range(length))
        lookup = _QueuePayload(endpoint, rows, None, compact=True)
        pending, max_depth = [(lookup.word, 0)], 0
        while pending:
            term, depth = pending.pop()
            depth += int(z3.is_app_of(term, z3.Z3_OP_ITE))
            max_depth = max(max_depth, depth)
            pending.extend((child, depth) for child in term.children())
        assert max_depth <= 1 + (length - 1).bit_length()
    empty = _QueuePayload(SimpleNamespace(fields=[]), ((), ()), None, compact=True)
    assert empty.at(z3.Int('empty_head'), z3.Bool('empty_present')) == ()


@pytest.mark.parametrize('length', [7, 100, 128, 257])
@pytest.mark.parametrize('explicit_context', [False, True])
def test_finite_lookup_shares_single_bit_selectors_instead_of_thresholds(length, explicit_context):
    from types import SimpleNamespace

    context = z3.Context() if explicit_context else None
    endpoint = SimpleNamespace(fields=[SimpleNamespace(type=api.FiniteType(8))])
    rows = tuple((z3.BitVec(f'cell_{i}', 8, ctx=context),) for i in range(length))
    head = z3.BitVec('head', length.bit_length() + 2, ctx=context)
    present = z3.Bool('present', ctx=context)
    lookup = _QueuePayload(endpoint, rows, context, compact=True, index_sort=head.sort())
    linear = _QueuePayload(endpoint, rows, context, index_sort=head.sort())
    for upper in (None, length // 2):
        actual, = lookup.at(head, present, head_upper=upper)
        expected, = linear.at(head, present)
        solver = z3.Solver(ctx=context)
        if upper is not None:
            solver.add(z3.ULE(head, upper))
        solver.add(actual != expected)
        assert solver.check() == z3.unsat
        pending, seen, selectors = [actual], set(), set()
        while pending:
            term = pending.pop()
            if term.get_id() in seen:
                continue
            seen.add(term.get_id())
            assert not z3.is_app_of(term, z3.Z3_OP_ULT)
            if z3.is_app_of(term, z3.Z3_OP_EXTRACT) and term.arg(0).eq(head):
                assert term.size() == 1
                selectors.add(term.get_id())
            pending.extend(term.children())
        assert len(selectors) <= (length - 1).bit_length()


@pytest.mark.parametrize('slots', [0, 1, 7, 8, 100, 303])
@pytest.mark.parametrize('initial', [0, 1, 7, 127])
def test_finite_count_width_covers_every_successor_without_wrap(initial, slots):
    from behave.sim.rtl.protocol_relation import _FiniteIndexAlgebra

    count = _FiniteIndexAlgebra(initial + slots, None)
    # Even unconditional acceptance at every step stays within the carrier.
    assert z3.simplify(count.integer(initial) + count.integer(slots)).as_long() == initial + slots


@pytest.mark.parametrize('length', [0, 1, 7, 100])
@pytest.mark.parametrize('finite', [False, True])
@pytest.mark.parametrize('explicit_context', [False, True])
def test_constructor_bounded_lookup_preserves_all_possible_heads(length, finite, explicit_context):
    context = z3.Context() if explicit_context else None
    endpoint = api.Endpoint('input', api.Direction.IN,
        [api.Field('flag', api.FiniteType(1), 'flag'),
         api.Field('word', api.FiniteType(65, signed=True), 'word')],
        api.ready_valid('in', 'valid', 'ready'), clock='clk', kind=api.Kind.TRANSACTION)
    rows = tuple((z3.Bool(f'flag_{i}', ctx=context), z3.BitVec(f'word_{i}', 65, ctx=context))
                 for i in range(length))
    head = z3.BitVec('head', 8, ctx=context) if finite else z3.Int('head', ctx=context)
    present = z3.Bool('present', ctx=context)
    lookup = _QueuePayload(endpoint, rows, context, compact=True, index_sort=head.sort())
    expected = lookup.at(head, present)
    for upper in sorted({0, 1, 5, length - 1, length + 1}):
        if upper < 0:
            continue
        actual = lookup.at(head, present, head_upper=upper)
        solver = z3.Solver(ctx=context)
        solver.add(z3.ULE(head, upper) if finite else z3.And(head >= 0, head <= upper))
        solver.add(z3.Or(*(a != b for a, b in zip(actual, expected, strict=True))))
        assert solver.check() == z3.unsat
    # No queue cell is removed from the request and no restriction is added.
    assert lookup.rows == rows
    assert lookup.constraints == lookup.definitions == ()


@pytest.mark.parametrize('initial', [0, 3])
@pytest.mark.parametrize('explicit_context', [False, True])
def test_bounded_lookup_follows_arbitrary_acceptances_without_assuming_progress(initial, explicit_context):
    """Prove the caller invariant from equations, not assumed cursor bounds."""
    from behave.sim.rtl.protocol_relation import _FiniteIndexAlgebra

    context = z3.Context() if explicit_context else None
    endpoint = api.Endpoint('input', api.Direction.IN,
        [api.Field('value', api.FiniteType(8), 'value')],
        api.ready_valid('in', 'valid', 'ready'), clock='clk', kind=api.Kind.TRANSACTION)
    length, slots = 17, 12
    rows = tuple((z3.BitVec(f'cell_{i}', 8, ctx=context),) for i in range(length))
    count = _FiniteIndexAlgebra(length + slots, context)
    head = count.integer(initial)
    lookup = _QueuePayload(endpoint, rows, context, compact=True, index_sort=head.sort())
    solver = z3.Solver(ctx=context)
    for slot in range(slots):
        present = z3.Bool(f'present_{slot}', ctx=context)
        actual = lookup.at(head, present, head_upper=initial + slot)[0]
        expected = lookup.at(head, present)[0]
        solver.push()
        solver.add(z3.Or(z3.UGT(head, initial + slot), actual != expected))
        assert solver.check() == z3.unsat
        solver.pop()
        accepted = z3.Bool(f'accepted_{slot}', ctx=context)
        successor = z3.BitVec(f'head_{slot}', count.width, ctx=context)
        solver.add(successor == z3.If(accepted, head + 1, head))
        head = successor
