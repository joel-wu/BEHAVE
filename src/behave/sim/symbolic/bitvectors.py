"""Pure bit-vector constructors shared by encoding and term proofs.

No source model, proposed range, query scope or verdict participates.
"""

import z3

COMPARISONS = frozenset({
    z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT,
    z3.Z3_OP_SLT, z3.Z3_OP_SLEQ, z3.Z3_OP_SGT, z3.Z3_OP_SGEQ,
    z3.Z3_OP_ULT, z3.Z3_OP_ULEQ, z3.Z3_OP_UGT, z3.Z3_OP_UGEQ,
})


def _wire_value(value, shared=None):
    """Recognize wiring once per boundary DAG, without wrapping every edge.

    The caller's live boundary values own all node handles. This memo is local
    to that boundary, never reused after its ASTs may have been released.
    """
    from z3 import z3core as native

    known = {} if shared is None else shared.setdefault(('wires', value.ctx), {})
    ctx = value.ctx_ref()
    root = value.get_id()
    pending = [(value.as_ast(), root, None)]
    while pending:
        term, key, children = pending.pop()
        if key in known:
            continue
        if children is not None:
            known[key] = all(known[child_key] for _, child_key in children)
            continue
        ast_kind = native.Z3_get_ast_kind(ctx, term)
        if ast_kind == z3.Z3_NUMERAL_AST:
            known[key] = True
            continue
        if ast_kind != z3.Z3_APP_AST:
            known[key] = False
            continue
        count = native.Z3_get_app_num_args(ctx, term)
        kind = native.Z3_get_decl_kind(ctx, native.Z3_get_app_decl(ctx, term))
        if not count:
            known[key] = True
        elif kind in (z3.Z3_OP_EXTRACT, z3.Z3_OP_ZERO_EXT,
                      z3.Z3_OP_SIGN_EXT, z3.Z3_OP_CONCAT):
            children = tuple(native.Z3_get_app_arg(ctx, term, index) for index in range(count))
            children = tuple((child, native.Z3_get_ast_id(ctx, child)) for child in children)
            pending.append((term, key, children))
            pending.extend((child, child_key, None) for child, child_key in children
                           if child_key not in known)
        elif kind == z3.Z3_OP_ITE:
            guard, yes, no = (native.Z3_get_app_arg(ctx, term, index) for index in range(3))
            # A Boolean pin packed into its physical bit pattern. A computed
            # guard or payload remains a computation needing a named successor.
            known[key] = (native.Z3_get_ast_kind(ctx, guard) == z3.Z3_APP_AST
                and native.Z3_get_app_num_args(ctx, guard) == 0
                and all(native.Z3_get_ast_kind(ctx, arm) == z3.Z3_NUMERAL_AST
                        and native.Z3_get_sort_kind(ctx, native.Z3_get_sort(ctx, arm)) == z3.Z3_BV_SORT
                        for arm in (yes, no)))
        else:
            known[key] = False
    return known[root]


def widen(a, b):
    """Bring two terms to a common width, keeping signedness in the value."""
    wa, wb = a.size(), b.size()
    if wa == wb:
        return a, b
    if wa < wb:
        return z3.SignExt(wb - wa, a), b
    return a, z3.SignExt(wa - wb, b)


def _extend(a, width):
    """``a`` at ``width``, which is never narrower than what it holds."""
    current = a.size()
    if current >= width:
        return a
    if z3.is_bv_value(a):
        return z3.BitVecVal(a.as_signed_long(), width, ctx=a.ctx)
    return z3.SignExt(width - current, a)


def _floor_div(a, b):
    """Division as the source does it, rounding towards minus infinity.

    The bitvector operation truncates towards zero, which agrees with the
    source only where the operands share a sign. Where they do not, the two
    differ by one exactly when the remainder is not zero: -3 truncated over 2
    is -1 and floored is -2.
    """
    shift = _positive_power_of_two(b)
    if shift is not None:
        # Arithmetic shift is floor division, also for negative dividends.
        # Inspect only a literal: traversing a symbolic RHS here would repeat
        # work over increasingly large expressions during history construction.
        return a >> shift
    zero, one = (z3.BitVecVal(value, a.size(), ctx=a.ctx) for value in (0, 1))
    negative_a, negative_b = a < zero, b < zero
    magnitude = z3.If(negative_a, -a, a)
    divisor = z3.If(negative_b, -b, b)
    # For opposite signs, -ceil(m/d) = -((m-1)//d)-1 when m>0.
    # Unsigned magnitudes represent abs(min_signed) exactly in the SAME
    # carrier. No remainder, wider carrier, range premise or overflow-prone
    # m+d-1 is needed. The public primitive totalizes a zero divisor below.
    round_down = z3.And(negative_a != negative_b, a != zero)
    quotient = z3.UDiv(z3.If(round_down, magnitude - one, magnitude), divisor)
    return z3.If(round_down, -quotient - one, quotient)


def _floor_mod(a, b):
    """Floor remainder, with the divisor's sign (Z3's signed modulo).

    This is the same bit-vector value as ``a - b * _floor_div(a, b)``,
    including wraparound, without constructing a quotient and multiplier.
    The primitive wrapper below still totalizes division by zero to zero.
    """
    if _positive_power_of_two(b) is not None:
        return a & z3.BitVecVal(b.as_signed_long() - 1, a.size(), ctx=a.ctx)
    return a % b


def _positive_power_of_two(term):
    if z3.is_bv_value(term):
        value = term.as_signed_long()
        if value > 0 and value & (value - 1) == 0:
            return value.bit_length() - 1
    return None
