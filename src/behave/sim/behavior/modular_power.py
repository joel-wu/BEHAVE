"""Exact finite IR expansions of integer modular powers.

The exponent and modulus are source-fixed integers. All intermediate values use
the ordinary integer IR; reducing coefficients preserves Python's unbounded
integer semantics without materialising an exponentially large ordinary power.
"""

from ..symbolic import ir


def inverse(base, modulus, capture):
    """Return (inverse, noninvertible) for a strictly positive modulus.

    Euclid takes at most twice the modulus bit length: every two nonterminal
    remainders reduce by at least half. Finished rounds retain their state.
    Powers of two use Newton lifting, doubling the proven precision each round.
    Neither bound is a resource limit or a guessed source loop bound.
    """
    if modulus == 1:
        return ir.Lit(0), ir.Lit(False)
    base = capture(ir.Op("fmod", base, ir.Lit(modulus)))
    if modulus & (modulus - 1) == 0:
        invalid = ir.Op("eq", ir.Op("and", base, ir.Lit(1)), ir.Lit(0))
        value, precision = ir.Lit(1), 1
        bits = modulus.bit_length() - 1
        while precision < bits:
            precision = min(2 * precision, bits)
            value = capture(ir.Op(
                "fmod", ir.Op("mul", value,
                               ir.Op("sub", ir.Lit(2), ir.Op("mul", base, value))),
                ir.Lit(1 << precision)))
        return value, invalid
    old_r, remainder = ir.Lit(modulus), base
    old_t, coefficient = ir.Lit(0), ir.Lit(1)
    for _ in range(2 * modulus.bit_length()):
        active = capture(ir.Op("ne", remainder, ir.Lit(0)))
        divisor = capture(ir.Op("ite", active, remainder, ir.Lit(1)))
        quotient = capture(ir.Op("fdiv", old_r, divisor))
        next_r = capture(ir.Op("fmod", old_r, divisor))
        next_t = capture(ir.Op(
            "fmod", ir.Op("sub", old_t, ir.Op("mul", quotient, coefficient)),
            ir.Lit(modulus)))
        old_r, remainder, old_t, coefficient = (
            capture(ir.Op("ite", active, remainder, old_r)),
            capture(ir.Op("ite", active, next_r, remainder)),
            capture(ir.Op("ite", active, coefficient, old_t)),
            capture(ir.Op("ite", active, next_t, coefficient)),
        )
    return old_t, ir.Op("ne", old_r, ir.Lit(1))


def power(base, exponent, modulus, capture):
    """Nonnegative exponent, nonzero signed modulus, Python remainder sign."""
    factor = capture(ir.Op("fmod", base, ir.Lit(modulus)))
    value = ir.Lit(1 % modulus)
    while exponent:
        if exponent & 1:
            value = capture(ir.Op(
                "fmod", ir.Op("mul", value, factor), ir.Lit(modulus)))
        exponent >>= 1
        if exponent:
            factor = capture(ir.Op(
                "fmod", ir.Op("mul", factor, factor), ir.Lit(modulus)))
    return value
