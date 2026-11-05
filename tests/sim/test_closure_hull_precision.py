"""Exact local range proofs must be invariant to operand order and sharing."""

import pytest

z3 = pytest.importorskip('z3', reason='solver lane requires z3-solver')
pytestmark = pytest.mark.solver

from behave.sim.symbolic import closure_proof, ranges
from behave.sim.symbolic.closure_proof import _SyntacticClosureProof
from behave.sim.symbolic.ranges import Range


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('depth', [8, 12])
def test_repeated_sign_prefix_work_follows_dag_not_expanded_bits(private, depth):
    from behave.sim.symbolic.term_bounds import _StructuralProofWork

    context = z3.Context() if private else z3.main_ctx()
    bit, suffix = z3.BitVec('shared_sign', 1, ctx=context), z3.BitVec('suffix', 8, ctx=context)
    work = _StructuralProofWork(12 * depth, float('inf'))
    proof = _SyntacticClosureProof({}, work=work)
    prefix = bit
    for _ in range(depth):
        prefix = z3.Concat(prefix, prefix)
        assert proof._concat_prefix_hull(prefix) == Range(-1, 0)
    term = z3.Concat(prefix, suffix)
    assert proof._concat_prefix_hull(term) == Range(-256, 255)
    assert len(proof.concat_prefixes) == depth + 3
    assert 12 * depth - work.remaining <= 4 * depth + 5
    solver = z3.Solver(ctx=context)
    solver.add(z3.Or(term < -256, term > 255))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('method', ['coarse_signed_hull', 'signed_hull'])
@pytest.mark.parametrize('width', [1, 2, 4, 32])
@pytest.mark.parametrize('private', [False, True])
def test_bitwise_complement_preserves_signed_interval(method, width, private):
    context = z3.Context() if private else z3.main_ctx()
    value, alias = z3.BitVecs('complement_value complement_alias', width, ctx=context)
    sign = 1 << (width - 1)
    for low, high in [(-sign, sign - 1), (-sign, -1), (0, sign - 1), (-1, 0)]:
        proof = _SyntacticClosureProof({alias.get_id(): ~value})
        proof.assume_boundary_hull(value, Range(low, high))
        hull = getattr(proof, method)(alias)
        assert hull == Range(-high - 1, -low - 1)
        solver = z3.Solver(ctx=context)
        solver.add(value >= low, value <= high, alias == ~value,
                   z3.Or(alias < hull.lo, alias > hull.hi))
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('method', ['coarse_signed_hull', 'signed_hull'])
@pytest.mark.parametrize('width', [4, 32])
@pytest.mark.parametrize('reverse', [False, True])
def test_negative_or_preserves_sign_without_constraining_other_operand(method, width, reverse):
    value, mask = z3.BitVecs('negative_or_value negative_or_mask', width)
    proof = _SyntacticClosureProof({})
    proof.assume_boundary_hull(value, Range(-4, -1))
    term = mask | value if reverse else value | mask
    hull = getattr(proof, method)(term)
    assert hull == Range(-4, -1)
    solver = z3.Solver()
    solver.add(value >= -4, value <= -1, z3.Or(term < hull.lo, term > hull.hi))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('remainder', [False, True])
@pytest.mark.parametrize('width', [1, 2, 3])
def test_unsigned_quotient_hulls_cover_every_small_signed_interval(width, remainder):
    from behave.sim.symbolic.closure_proof import _unsigned_quotient_hull

    sign = 1 << (width - 1)
    values = range(-sign, sign)
    # Independent native truth table includes division/remainder by zero and
    # negative signed carriers whose bit patterns denote large unsigned values.
    actual = {}
    operation = z3.URem if remainder else z3.UDiv
    for a in values:
        for b in values:
            actual[a, b] = z3.simplify(operation(
                z3.BitVecVal(a, width), z3.BitVecVal(b, width))).as_signed_long()
    intervals = [Range(a, b) for a in values for b in values if a <= b]
    for left in intervals:
        for right in intervals:
            hull = _unsigned_quotient_hull(left, right, width, remainder=remainder)
            assert -sign <= hull.lo <= hull.hi < sign
            for a in range(left.lo, left.hi + 1):
                for b in range(right.lo, right.hi + 1):
                    assert hull.lo <= actual[a, b] <= hull.hi


@pytest.mark.parametrize('method', ['coarse_signed_hull', 'signed_hull'])
@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('remainder', [False, True])
@pytest.mark.parametrize('zero_possible', [False, True])
def test_unsigned_quotient_range_survives_ssa_and_preserves_zero(method, private, remainder, zero_possible):
    context = z3.Context() if private else z3.main_ctx()
    a, b, n, d = z3.BitVecs('unsigned_hull_a unsigned_hull_b unsigned_hull_n unsigned_hull_d',
                           65, ctx=context)
    dividend = a & 65535
    divisor = (b & 15) + (0 if zero_possible else 1)
    proof = _SyntacticClosureProof({n.get_id(): dividend, d.get_id(): divisor})
    term = (z3.URem if remainder else z3.UDiv)(n, d)
    hull = getattr(proof, method)(term)
    expected = (Range(0, 65535 if zero_possible else 15) if remainder else
                Range(-1 if zero_possible else 0, 65535))
    assert hull == expected
    solver = z3.Solver(ctx=context)
    solver.add(n == dividend, d == divisor, z3.Or(term < hull.lo, term > hull.hi))
    assert solver.check() == z3.unsat


def test_unsigned_quotient_bounds_allow_equivalent_downstream_width_reduction():
    from behave.sim.symbolic.term_rewrite import _ComparisonRewriter

    a, b = z3.BitVecs('unsigned_narrow_a unsigned_narrow_b', 65)
    quotient = z3.UDiv(a & 65535, (b & 15) + 1)
    factor = a & 7
    width = _ComparisonRewriter([]).multiplication_width(quotient, factor)
    assert width == 20
    narrow = z3.Extract(width - 1, 0, quotient) * z3.Extract(width - 1, 0, factor)
    solver = z3.Solver()
    solver.add(quotient * factor != z3.SignExt(65 - width, narrow))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('method', ['coarse_signed_hull', 'signed_hull'])
@pytest.mark.parametrize('width', [4, 44])
@pytest.mark.parametrize('values', ['signed', 'negative', 'positive'])
@pytest.mark.parametrize('counts', ['cross-width', 'positive-cross-width',
                                  'in-width', 'cross-sign', 'negative', 'overshift'])
@pytest.mark.parametrize('operation', ['logical-right', 'arithmetic-right', 'left'])
def test_shift_hulls_keep_in_range_and_overshift_results(method, width, values, counts, operation):
    value, count = z3.BitVecs('shift_hull_value shift_hull_count', width)
    low, high = -(1 << (width - 1)), (1 << (width - 1)) - 1
    bounds = {'signed': Range(low, high), 'negative': Range(low, -1),
              'positive': Range(0, high)}[values]
    shifts = {'cross-width': Range(0, width + 1),
              'positive-cross-width': Range(1, width + 1),
              'in-width': Range(0, width - 1), 'cross-sign': Range(-2, width + 1),
              'negative': Range(-2, -1), 'overshift': Range(width, width + 1)}[counts]
    term = {'logical-right': z3.LShR(value, count),
            'arithmetic-right': value >> count, 'left': value << count}[operation]
    proof = _SyntacticClosureProof({})
    proof.assume_boundary_hull(value, bounds)
    proof.assume_boundary_hull(count, shifts)
    hull = getattr(proof, method)(term)
    solver = z3.Solver()
    solver.add(value >= bounds.lo, value <= bounds.hi,
               count >= shifts.lo, count <= shifts.hi,
               z3.Or(term < hull.lo, term > hull.hi))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('method', ['coarse_signed_hull', 'signed_hull'])
@pytest.mark.parametrize('simplified', [False, True])
def test_guarded_counter_keeps_its_floor_through_shared_updates(method, simplified):
    raw = z3.BitVec('guarded_counter_raw', 8)
    value = z3.If(raw == 0, z3.BitVecVal(1, 32), z3.ZeroExt(24, raw))
    for index in range(26):
        value = z3.If(z3.And(z3.Bool(f'decrement_{index}'), value > 1), value - 1, value)
    if simplified:
        value = z3.simplify(value)
    hull = getattr(_SyntacticClosureProof({}), method)(value)
    assert hull == Range(1, 255)
    solver = z3.Solver()
    solver.add(z3.Or(value < hull.lo, value > hull.hi))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('method', ['coarse_signed_hull', 'signed_hull'])
@pytest.mark.parametrize('shape', ['nested-and', 'negated-or'])
def test_guarded_floor_is_independent_of_boolean_grouping(method, shape):
    raw = z3.BitVec('nested_guard_counter', 8)
    value = z3.If(raw == 0, z3.BitVecVal(1, 32), z3.ZeroExt(24, raw))
    for index in range(26):
        left, right = z3.Bools(f'nested_left_{index} nested_right_{index}')
        guard = (z3.And(left, z3.And(right, value > 1)) if shape == 'nested-and'
                 else z3.Not(z3.Or(left, z3.Or(right, value <= 1))))
        value = z3.If(guard, value - 1, value)
    hull = getattr(_SyntacticClosureProof({}), method)(value)
    assert hull == Range(1, 255)
    solver = z3.Solver()
    solver.add(z3.Or(value < hull.lo, value > hull.hi))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('unsigned', [False, True])
@pytest.mark.parametrize('extension', ['none', 'zero', 'sign'])
@pytest.mark.parametrize('offset', [-2, 0, 2])
def test_guarded_affine_ranges_include_all_modular_values(unsigned, extension, offset):
    import operator

    ctx = z3.Context()
    raw = z3.BitVec('guarded_small_raw', 5, ctx=ctx)
    value = {'none': raw, 'zero': z3.ZeroExt(2, raw), 'sign': z3.SignExt(2, raw)}[extension]
    width = value.size()
    operations = [operator.eq, operator.ne, z3.ULT if unsigned else operator.lt,
                  z3.ULE if unsigned else operator.le, z3.UGT if unsigned else operator.gt,
                  z3.UGE if unsigned else operator.ge]
    for limit in [-16, -1, 0, 1, 15]:
        for compare in operations:
            condition = compare(raw, z3.BitVecVal(limit, 5, ctx=ctx))
            for negative in [False, True]:
                guard = z3.Not(condition) if negative else condition
                term = z3.If(guard, value + offset, z3.BitVecVal(3, width, ctx=ctx))
                hull = _SyntacticClosureProof({}).coarse_signed_hull(term)
                for bits in range(32):
                    actual = z3.simplify(z3.substitute(term, (raw, z3.BitVecVal(bits, 5, ctx=ctx)))).as_signed_long()
                    assert hull.lo <= actual <= hull.hi, (limit, compare, negative, bits, hull, actual)


def test_guarded_counter_precision_survives_the_actual_proof_abstraction():
    from behave.sim.rtl.source_domains import _transition_proof_roots

    raw = z3.BitVec('guarded_cut_raw', 8)
    value = z3.If(raw == 0, z3.BitVecVal(1, 32), z3.ZeroExt(24, raw))
    for index in range(26):
        value = z3.If(z3.And(z3.Bool(f'cut_decrement_{index}'), value > 1), value - 1, value)
    bounds = []
    impossible, possible = _transition_proof_roots((value < 1, value == 1),
        bounds=bounds, data_selectors=True, check=lambda: None)
    solver = z3.Solver()
    solver.add(*bounds)
    assert solver.check(impossible) == z3.unsat
    assert solver.check(possible) == z3.sat


def test_guard_refinement_does_not_assume_an_independent_boolean_arm():
    raw = z3.BitVec('guard_independent_raw', 8)
    value, enable = z3.ZeroExt(24, raw), z3.Bool('guard_independent_enable')
    for term in [z3.If(z3.Or(enable, value > 1), value - 1, value),
                 z3.If(z3.And(enable, value > 1), value, value - 1)]:
        hull = _SyntacticClosureProof({}).coarse_signed_hull(term)
        assert hull.lo == -1
        solver = z3.Solver()
        solver.add(z3.Or(term < hull.lo, term > hull.hi))
        assert solver.check() == z3.unsat


def test_guard_refinement_follows_aliases_but_declines_boolean_cycles():
    raw, alias = z3.BitVecs('guard_alias_raw guard_alias', 8)
    guard = z3.Bool('guard_alias_condition')
    proof = _SyntacticClosureProof({alias.get_id(): raw, guard.get_id(): raw > 1})
    proof.assume_boundary_hull(raw, Range(1, 120))
    value = z3.If(guard, alias - 1, alias)
    assert proof.coarse_signed_hull(value) == Range(1, 119)
    solver = z3.Solver()
    solver.add(alias == raw, guard == (raw > 1), raw >= 1, raw <= 120,
               z3.Or(value < 1, value > 119))
    assert solver.check() == z3.unsat
    cyclic = _SyntacticClosureProof({guard.get_id(): z3.Not(guard)})
    assert cyclic._direct_guard_ranges(guard, True) == {}


def test_shared_guard_cost_is_linear_in_comparisons_and_arms(monkeypatch):
    from behave.sim.symbolic import term_bounds

    original = term_bounds._no_wrap_hull
    measured = []
    for count in [32, 256]:
        value = z3.BitVec(f'shared_guard_{count}', 32)
        guard = z3.And(*(value >= index for index in range(count)))
        arms = [z3.If(guard, value + index, value) for index in range(1, count + 1)]
        calls = []
        def record(*args):
            calls.append(1)
            return original(*args)
        proof = _SyntacticClosureProof({})
        proof.assume_boundary_hull(value, Range(0, 65535))
        with monkeypatch.context() as patch:
            patch.setattr(term_bounds, '_no_wrap_hull', record)
            for arm in arms:
                proof.coarse_signed_hull(arm)
        measured.append(len(calls))
    assert measured[0] > 0
    assert measured[1] <= measured[0] * 8


@pytest.mark.parametrize('separate_subjects', [False, True])
@pytest.mark.parametrize('negated', [False, True])
def test_growing_guard_prefixes_reuse_facts_without_quadratic_scans(separate_subjects, negated):
    class Work:
        calls = 0

        def charge(self):
            self.calls += 1

    measured = []
    for count in [32, 256]:
        context = z3.Context()
        work = Work()
        proof = _SyntacticClosureProof({}, work=work)
        guard = z3.BoolVal(not negated, ctx=context)
        for index in range(count):
            value = z3.BitVec(f'prefix_subject_{index if separate_subjects else 0}', 32, ctx=context)
            test = value >= index
            guard = z3.Or(guard, z3.Not(test)) if negated else z3.And(guard, test)
            # Real lowering names guards, then appends the next branch to that
            # name. Later definitions must not invalidate earlier syntax work.
            alias = z3.Bool(f'prefix_guard_{index}', ctx=context)
            proof.definitions[alias.get_id()] = guard
            guard = alias
            result = proof._direct_guard_ranges(guard, not negated, (value.get_id(),))
            assert result[value.get_id()] == (Range(index, (1 << 31) - 1),)
        measured.append(work.calls)
    assert measured[0] > 0
    assert measured[1] <= measured[0] * 9


@pytest.mark.parametrize('private', [False, True])
def test_projected_guard_ranges_preserve_all_values_and_boolean_polarities(private):
    context = z3.Context() if private else z3.main_ctx()
    a, b = z3.BitVecs('projected_guard_a projected_guard_b', 4, ctx=context)
    enable = z3.Bool('projected_guard_enable', ctx=context)
    prefix = z3.And(z3.UGE(a, 3), a != 5, b < 4)
    guards = [prefix, z3.Or(enable, prefix), z3.Not(prefix),
              z3.And(prefix, z3.Or(a < 0, b == 2)),
              z3.And(prefix, z3.Not(z3.Or(a == 7, b < 0))),
              z3.And(a == 5, a != 5)]
    proof = _SyntacticClosureProof({})
    for guard in guards:
        for truth in [False, True]:
            for value in [a, b]:
                facts = proof._direct_guard_ranges(guard, truth, (value.get_id(),))
                if value.get_id() not in facts:
                    continue
                allowed = z3.Or(*(z3.And(value >= part.lo, value <= part.hi)
                                  for part in facts[value.get_id()]))
                solver = z3.Solver(ctx=context)
                solver.add(guard if truth else z3.Not(guard), z3.Not(allowed))
                assert solver.check() == z3.unsat


def test_interrupted_guard_index_does_not_publish_an_unfinished_parent():
    from behave.sim.symbolic.term_bounds import _StructuralProofLimit, _StructuralProofWork

    a, b = z3.BitVecs('guard_interrupt_a guard_interrupt_b', 8)
    guard = z3.And(a > 3, b < 7)
    proof = _SyntacticClosureProof({}, work=_StructuralProofWork(2, float('inf')))
    with pytest.raises(_StructuralProofLimit):
        proof._direct_guard_ranges(guard, True)
    assert (guard.get_id(), True) not in proof.guard_nodes
    proof.work = None
    facts = proof._direct_guard_ranges(guard, True)
    assert facts[a.get_id()] == (Range(4, 127),)
    assert facts[b.get_id()] == (Range(-128, -1), Range(0, 6))


@pytest.mark.parametrize('method', ['coarse_signed_hull', 'signed_hull'])
@pytest.mark.parametrize('bounds', [(-6, 6), (-8, 7), (0, 3), (-13, 6), (4, 12)])
@pytest.mark.parametrize('offset', [0, 2])
def test_truncation_preserves_only_proved_nonwrapping_signed_hulls(method, bounds, offset):
    raw = z3.BitVec('truncation_raw', 8)
    term = z3.Extract(offset + 3, offset, raw)
    proof = _SyntacticClosureProof({})
    proof.assume_boundary_hull(raw, Range(*bounds))
    hull = getattr(proof, method)(term)
    assert hull == (Range(*bounds) if offset == 0 and -8 <= bounds[0] <= bounds[1] <= 7
                    else Range(-8, 7))
    solver = z3.Solver()
    solver.add(raw >= bounds[0], raw <= bounds[1],
               z3.Or(term < hull.lo, term > hull.hi))
    assert solver.check() == z3.unsat


def test_narrow_arithmetic_retains_intrinsic_operand_bounds_through_ssa():
    from behave.sim.symbolic import encoder, ir

    enc = encoder._Encoder(None, {'x': Range(-255, 255)}, 'narrow_hull')
    enc.scalar_width = 65
    a, b = z3.BitVecs('narrow_hull_a narrow_hull_b', 8)
    delta = enc.bind(z3.ZeroExt(57, a) - z3.ZeroExt(57, b))
    square = enc.expr(ir.Op('mul', ir.Var('x'), ir.Var('x')), {'x': delta})
    proof = _SyntacticClosureProof({item.lhs.get_id(): item.rhs
                                  for item in enc.definitions})
    # No input or frontend range premises. Reusing a smaller exact circuit
    # must not turn its operands into unrelated full-width signed values.
    assert proof.coarse_signed_hull(square) == Range(-65025, 65025)


@pytest.mark.parametrize('method', ['coarse_signed_hull', 'signed_hull'])
@pytest.mark.parametrize('simplified', [False, True])
def test_zero_extension_keeps_lookup_range(method, simplified):
    selector = z3.BitVec('lookup_selector', 6)
    lookup = z3.BitVecVal(1 << 29, 32)
    for index in range(32):
        lookup = z3.If(selector == index,
                       z3.BitVecVal((1 << 30) - index * (1 << 23), 32), lookup)
    word = z3.ZeroExt(32, lookup)
    if simplified:
        word = z3.simplify(word)
    hull = getattr(_SyntacticClosureProof({}), method)(word)
    assert hull == Range(1 << 29, 1 << 30)
    solver = z3.Solver()
    solver.add(z3.Or(word < hull.lo, word > hull.hi))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('prefix', [0, 1, 3])
def test_concat_ranges_cover_every_signed_suffix_interval(prefix):
    """Exhaustive independent values include sign crossings and negative prefixes."""
    suffix = z3.BitVec('bounded_suffix', 4)
    word = z3.Concat(z3.BitVecVal(prefix, 2), suffix)
    for low in range(-8, 8):
        for high in range(low, 8):
            proof = _SyntacticClosureProof({})
            proof.assume_boundary_hull(suffix, Range(low, high))
            hull = proof.coarse_signed_hull(word)
            values = [(prefix << 4) | (x & 15) for x in range(low, high + 1)]
            values = [x - 64 if x >= 32 else x for x in values]
            assert hull == Range(min(values), max(values))


def test_packed_fields_keep_narrow_ranges_without_assuming_correlation():
    a, b = z3.BitVecs('packed_a packed_b', 4)
    proof = _SyntacticClosureProof({})
    proof.assume_boundary_hull(a, Range(2, 3))
    proof.assume_boundary_hull(b, Range(-3, -1))
    word = z3.Concat(z3.BitVecVal(0, 3), a, b)
    hull = proof.coarse_signed_hull(word)
    assert hull == Range(45, 63)
    solver = z3.Solver()
    solver.add(a >= 2, a <= 3, b >= -3, b <= -1,
               z3.Or(word < hull.lo, word > hull.hi))
    assert solver.check() == z3.unsat


def test_repeated_symbolic_concat_prefix_retains_signed_range():
    sign = z3.BitVec('concat_sign', 1)
    tail = z3.BitVec('concat_tail', 5)
    term = z3.Concat(sign, sign, sign, tail)
    proof = _SyntacticClosureProof({})
    hull = proof.coarse_signed_hull(term)
    assert hull == Range(-32, 31)
    solver = z3.Solver()
    solver.add(z3.Or(term < hull.lo, term > hull.hi))
    assert solver.check() == z3.unsat
    # Similar-looking but distinct high bits cannot strengthen the range.
    other = z3.BitVec('concat_other', 1)
    mixed = z3.Concat(sign, other, sign, tail)
    assert proof.coarse_signed_hull(mixed) == Range(-128, 127)
    repeated = z3.Concat(sign, sign, sign)
    assert proof.coarse_signed_hull(repeated) == Range(-1, 0)


@pytest.mark.parametrize('unsigned', [False, True])
@pytest.mark.parametrize('minimum', [False, True])
@pytest.mark.parametrize('negated', [False, True])
def test_ordered_select_uses_comparison_without_assuming_an_arm(unsigned, minimum, negated):
    ctx = z3.Context()
    raw = z3.BitVec('clamp_input', 8, ctx=ctx)
    value = z3.ZeroExt(1, raw) if unsigned else z3.SignExt(1, raw)
    limit = z3.BitVecVal(17, 9, ctx=ctx)
    guard = z3.ULT(value, limit) if unsigned else value < limit
    yes, no = (value, limit) if minimum else (limit, value)
    term = z3.If(z3.Not(guard), no, yes) if negated else z3.If(guard, yes, no)
    proof = _SyntacticClosureProof({})
    hull = proof.coarse_signed_hull(term)
    assert hull == (Range(0 if unsigned else -128, 17) if minimum else
                    Range(17, 255 if unsigned else 127))
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Or(term < hull.lo, term > hull.hi))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('minimum', [False, True])
def test_unsigned_clamp_can_bound_an_unrestricted_signed_carrier(minimum):
    value = z3.BitVec('unsigned_clamp_input', 8)
    limit = z3.BitVecVal(17 if minimum else 240, 8)
    term = z3.If(z3.ULT(value, limit), value if minimum else limit,
                 limit if minimum else value)
    hull = _SyntacticClosureProof({}).coarse_signed_hull(term)
    assert hull == (Range(0, 17) if minimum else Range(-16, -1))
    solver = z3.Solver()
    solver.add(z3.Or(term < hull.lo, term > hull.hi))
    assert solver.check() == z3.unsat


def test_flattened_sign_extensions_do_not_invent_full_width_products():
    a, b = z3.BitVecs('small_product_a small_product_b', 8)
    left, right = (z3.simplify(z3.SignExt(24, x)) for x in (a, b))
    proof = _SyntacticClosureProof({})
    value = left * right
    hull = proof.coarse_signed_hull(value)
    assert hull is not None and -65536 <= hull.lo <= -16256
    assert 16384 <= hull.hi <= 65536
    solver = z3.Solver()
    solver.add(z3.Or(value < hull.lo, value > hull.hi))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('op', ['add', 'mul', 'or', 'xor'])
def test_flattened_associative_hull_includes_every_operand(op):
    import operator

    function = {'add': operator.add, 'mul': operator.mul,
                'or': operator.or_, 'xor': operator.xor, 'and': operator.and_}[op]
    a, b, c = z3.BitVecs('nary_hull_a nary_hull_b nary_hull_c', 32)
    term = z3.simplify(function(function(a, b), c))
    assert term.num_args() >= 3
    proof = _SyntacticClosureProof({})
    for value, maximum in ((a, 1), (b, 1), (c, 31)):
        proof.assume_boundary_hull(value, Range(0, maximum))
    hull = proof.coarse_signed_hull(term)
    solver = z3.Solver()
    solver.add(a >= 0, a <= 1, b >= 0, b <= 1, c >= 0, c <= 31,
               z3.Or(term < hull.lo, term > hull.hi))
    assert solver.check() == z3.unsat
    assert hull.hi <= {'add': 33, 'mul': 31, 'or': 31, 'xor': 31, 'and': 1}[op]


@pytest.mark.parametrize('method', ['coarse_signed_hull', 'signed_hull'])
@pytest.mark.parametrize('literal', [0, 1, -1])
def test_boundary_certificate_cannot_widen_an_interned_literal(method, literal):
    value = z3.BitVecVal(literal, 271)
    proof = _SyntacticClosureProof({})
    proof.use_certified_hull(value, Range(-256, 256))
    assert getattr(proof, method)(value) == Range(literal, literal)
    guarded = z3.If(z3.Bool('active'), z3.BitVecVal(17, 271), value)
    assert getattr(proof, method)(guarded) == Range(min(literal, 17), 17)


@pytest.mark.parametrize('method', ['coarse_signed_hull', 'signed_hull'])
@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('literal_mask', [False, True])
def test_and_intersects_all_known_bounds(method, reverse, literal_mask):
    data = z3.BitVec('wide_data', 271)
    mask = (z3.BitVecVal(127, 271) if literal_mask else
            z3.BitVec('dynamic_mask', 271))
    proof = _SyntacticClosureProof({})
    proof.assume_boundary_hull(data, Range(0, 1 << 256))
    if not literal_mask:
        proof.assume_boundary_hull(mask, Range(0, 127))
    expression = mask & data if reverse else data & mask
    assert getattr(proof, method)(expression) == Range(0, 127)


@pytest.mark.parametrize('method', ['coarse_signed_hull', 'signed_hull'])
def test_variable_low_bit_mask_after_guarded_shift(method):
    """A streaming residue needs its mask proof, not the packed value's bound."""
    width = 271
    data, count = z3.BitVecs('residue_data residue_count', width)
    zero, one = z3.BitVecVal(0, width), z3.BitVecVal(1, width)
    proof = _SyntacticClosureProof({})
    proof.assume_boundary_hull(data, Range(0, 1 << 256))
    proof.assume_boundary_hull(count, Range(0, 7))
    proof.use_certified_hull(zero, Range(0, 256))
    mask = z3.If(count < zero, zero, one << count) - one
    result = z3.If(z3.Bool('residue_active'), data & mask, zero)
    hull = getattr(proof, method)(result)
    assert hull == Range(0, 127)
    solver = z3.Solver()
    solver.set(timeout=10_000)
    solver.add(data >= 0, data <= (1 << 256), count >= 0, count <= 7,
               z3.Or(result < hull.lo, result > hull.hi))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('left', [(-16, -1), (-8, 7), (0, 15), (2, 5)])
@pytest.mark.parametrize('right', [(-16, -1), (-8, 7), (0, 15), (1, 3)])
def test_and_hull_is_sound_for_signed_and_unsigned_operands(left, right):
    """Independent exact queries include negative masks and sign-bit cases."""
    a, b = z3.BitVecs('signed_a signed_b', 5)
    proof = _SyntacticClosureProof({})
    proof.assume_boundary_hull(a, Range(*left))
    proof.assume_boundary_hull(b, Range(*right))
    value = a & b
    hull = proof.coarse_signed_hull(value)
    assert hull == proof.coarse_signed_hull(b & a)
    solver = z3.Solver()
    solver.set(timeout=10_000)
    solver.add(a >= left[0], a <= left[1], b >= right[0], b <= right[1],
               z3.Or(value < hull.lo, value > hull.hi))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("kind,relation,unsigned", [
    (z3.Z3_OP_EQ, "eq", False), (z3.Z3_OP_DISTINCT, "ne", False),
    (z3.Z3_OP_SLT, "lt", False), (z3.Z3_OP_SLEQ, "le", False),
    (z3.Z3_OP_SGT, "gt", False), (z3.Z3_OP_SGEQ, "ge", False),
    (z3.Z3_OP_ULT, "lt", True), (z3.Z3_OP_ULEQ, "le", True),
    (z3.Z3_OP_UGT, "gt", True), (z3.Z3_OP_UGEQ, "ge", True),
])
def test_closure_hull_comparisons_match_every_four_bit_interval_pair(kind, relation, unsigned):
    """Independent finite evaluation, not the source abstract transformer."""
    import operator

    compare = getattr(operator, relation)
    hulls = [ranges.Range(lo, hi) for lo in range(-8, 8) for hi in range(lo, 8)]
    for left in hulls:
        for right in hulls:
            actual = closure_proof._compare_hulls(kind, left, right)
            if unsigned and (left.lo < 0 or right.lo < 0):
                assert actual is None
                continue
            outcomes = {compare(x, y)
                        for x in range(left.lo, left.hi + 1)
                        for y in range(right.lo, right.hi + 1)}
            expected = next(iter(outcomes)) if len(outcomes) == 1 else None
            assert actual is expected, (kind, left, right, actual, expected)


@pytest.mark.parametrize("left,right", [(None, None), (None, ranges.Range(1, 2)),
                                       (ranges.Range(-2, -1), None)])
def test_closure_hull_comparison_without_operand_evidence_is_unknown(left, right):
    assert closure_proof._compare_hulls(z3.Z3_OP_EQ, left, right) is None
    assert closure_proof._compare_hulls(z3.Z3_OP_SLT, left, right) is None


def test_closure_comparison_callers_keep_distinct_literal_evidence():
    term = z3.BitVecVal(-2, 4) < z3.BitVecVal(1, 4)
    proof = closure_proof._SyntacticClosureProof({})
    assert proof._comparison_from_hulls(term, {}) is True
    # The Boolean finisher consumes already-visited operands; it must not
    # silently gain the other caller's direct literal interpretation.
    assert proof._finish_boolean(term) is None
    proof.hulls[term.arg(0).get_id()] = ranges.Range(-2, -2)
    proof.hulls[term.arg(1).get_id()] = ranges.Range(1, 1)
    assert proof._finish_boolean(term) is True


@pytest.mark.parametrize("answer", [False, True])
def test_closure_known_boolean_precedes_hull_comparison(monkeypatch, answer):
    left, right = z3.BitVecs("known_left known_right", 4)
    term = left < right
    proof = closure_proof._SyntacticClosureProof({})
    proof.booleans[term.get_id()] = answer
    monkeypatch.setattr(closure_proof, "_compare_hulls", lambda *args: pytest.fail(
        "a previously established Boolean must not reopen its operand proof"))
    assert proof._finish_boolean(term) is answer


@pytest.mark.parametrize("term", [z3.Int("not_a_bv") < 2,
                                  z3.Not(z3.Bool("not_binary")),
                                  z3.BitVec("not_a_comparison", 4) + 1])
def test_closure_hull_comparison_keeps_its_term_admission_boundary(term):
    assert closure_proof._SyntacticClosureProof._comparison_from_hulls(term, {}) is None
