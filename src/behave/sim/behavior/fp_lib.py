"""Deterministic FP32 and BF16 value operations on integer encodings.

FP32 arithmetic uses round-to-nearest, ties-to-even, and gradual underflow.
Subnormal operands retain their values. Arithmetic NaNs return 0x7FC00000.
The API returns values only, without exception flags or selectable rounding.

BF16 conversion, saturating integer conversion, and the two-operation rsqrt
are specified separately in the public numerical API document. Sign operations
preserve NaN payload bits. Integer-only implementations can be imported as
source by the Behavior engine; this is not a host floating-point dependency.
"""

from __future__ import annotations


def _mask(w: int) -> int:
    return (1 << w) - 1


def _widen_unsigned(x, target_w: int):
    """Keep the low ``target_w`` bits of x, whatever carries the value.

    One path rather than two. A dual value and a plain int both mask, and
    asking which one this is at run time would be a test the lowered form
    cannot make: there are no Python types left in it. The two paths also
    disagreed on a value wider than the target, where masking keeps the low
    bits and a widening keeps them all.
    """
    return x & _mask(target_w)


# ── FP32 helpers ──────────────────────────────────────────────────────

FP32_EXP_BIAS = 127
FP32_EXP_MASK = 0xFF
FP32_MANT_W   = 23
FP32_MANT_MASK = (1 << FP32_MANT_W) - 1
FP32_NAN      = 0x7FC00000
FP32_INF_POS  = 0x7F800000
FP32_INF_NEG  = 0xFF800000


def _fp32_fields(u):
    """Split an FP32 bit pattern into (sign, exp, mant)."""
    sign = (u >> 31) & 1
    exp  = (u >> 23) & FP32_EXP_MASK
    mant = u & FP32_MANT_MASK
    return sign, exp, mant


def _fp32_pack(sign, exp, mant):
    return ((sign & 1) << 31) | ((exp & FP32_EXP_MASK) << 23) | (mant & FP32_MANT_MASK)


def _fp32_normalize(exp, mant):
    """Return a biased exponent and 24-bit significand for a nonzero finite input.

    A subnormal's effective exponent is 1 before normalization. The returned
    exponent may be negative; it is not an encoded exponent field yet.
    """
    if exp == 0:
        extended, shift = _normalize_leading_to_bit26(mant << 3)
        return 1 - shift, extended >> 3
    return exp, (1 << 23) | mant


def _shift_right_jam(value, distance):
    """Shift a nonnegative integer, retaining discarded nonzero bits in bit 0."""
    shifted = value >> distance
    sticky = 1 if (shifted << distance) != value else 0
    return shifted | sticky


def _fp32_round_pack(sign, exp, extended):
    """Round a significand with three low guard/round/sticky bits exactly once.

    A normal significand has its leading bit at 26. For tiny results, first
    align to the subnormal spacing (2**-149), then apply the same RNE decision.
    Rounding may promote the largest subnormal interval to the minimum normal.
    """
    e_out = exp
    significand = extended
    if e_out <= 0:
        significand = _shift_right_jam(significand, 1 - e_out)
        e_out = 1
    mant = significand >> 3
    tail = significand & 7
    if tail > 4 or (tail == 4 and (mant & 1) != 0):
        mant += 1
    if mant >= (1 << 24):
        mant >>= 1
        e_out = e_out + 1
    if e_out >= FP32_EXP_MASK:
        return (sign << 31) | (FP32_EXP_MASK << 23)
    if mant < (1 << 23):
        e_out = 0
    return _fp32_pack(sign, e_out, mant)


def fp32_is_zero(u):
    _, e, m = _fp32_fields(u)
    return (e == 0) and (m == 0)


def fp32_is_nan(u):
    _, e, m = _fp32_fields(u)
    return (e == FP32_EXP_MASK) and (m != 0)


def fp32_is_inf(u):
    _, e, m = _fp32_fields(u)
    return (e == FP32_EXP_MASK) and (m == 0)


def fp32_abs(u):
    return u & 0x7FFFFFFF


def fp32_neg(u):
    return u ^ 0x80000000


def fp32_cmp(a, b):
    """Signed compare of two FP32 bit patterns. Returns -1, 0, +1.

    Uses the property that IEEE 754 magnitudes, interpreted as unsigned
    integer bit patterns, are monotonic within one sign. Cross-sign is
    decided by the sign bit.
    NaN values are not ordered; a NaN on either side returns -2 as a
    tag so callers can detect it.
    """
    if fp32_is_nan(a) or fp32_is_nan(b):
        return -2
    sa, _, _ = _fp32_fields(a)
    sb, _, _ = _fp32_fields(b)
    # ±0 compare equal
    if fp32_is_zero(a) and fp32_is_zero(b):
        return 0
    if sa != sb:
        return 1 if sa == 0 else -1
    # same sign: positive → larger bit pattern is larger;
    # negative → larger bit pattern is smaller
    if sa == 0:
        return 0 if a == b else (1 if a > b else -1)
    return 0 if a == b else (-1 if a > b else 1)


# ── FP32 multiply (RNE, handles zero / Inf / NaN / subnormal inputs) ─

def fp32_mul(a, b):
    """IEEE 754 binary32 multiplication. RNE rounding."""
    sa, ea, ma = _fp32_fields(a)
    sb, eb, mb = _fp32_fields(b)
    sout = sa ^ sb

    # NaN propagation
    if (ea == FP32_EXP_MASK and ma != 0) or (eb == FP32_EXP_MASK and mb != 0):
        return FP32_NAN
    # ±0 · anything-finite = ±0 ;  ±0 · Inf = NaN
    a_is_zero = (ea == 0 and ma == 0)
    b_is_zero = (eb == 0 and mb == 0)
    a_is_inf  = (ea == FP32_EXP_MASK and ma == 0)
    b_is_inf  = (eb == FP32_EXP_MASK and mb == 0)
    if (a_is_zero and b_is_inf) or (b_is_zero and a_is_inf):
        return FP32_NAN
    if a_is_zero or b_is_zero:
        return (sout << 31)                           # ±0
    if a_is_inf or b_is_inf:
        return (sout << 31) | (FP32_EXP_MASK << 23)    # ±Inf

    ea, sig_a = _fp32_normalize(ea, ma)
    eb, sig_b = _fp32_normalize(eb, mb)
    # 24-bit × 24-bit gives an exact 48-bit product.
    prod = _widen_unsigned(sig_a, 48) * _widen_unsigned(sig_b, 48)
    e_out = ea + eb - FP32_EXP_BIAS
    if prod >= (1 << 47):
        extended = _shift_right_jam(prod, 21)
        e_out += 1
    else:
        extended = _shift_right_jam(prod, 20)
    return _fp32_round_pack(sout, e_out, extended)


# ── FP32 divide (RNE, gradual underflow) ─────────────────────────────

def fp32_div(a, b):
    """IEEE 754 binary32 division. RNE rounding with gradual underflow."""
    sa, ea, ma = _fp32_fields(a)
    sb, eb, mb = _fp32_fields(b)
    sout = sa ^ sb

    if (ea == FP32_EXP_MASK and ma != 0) or (eb == FP32_EXP_MASK and mb != 0):
        return FP32_NAN
    a_is_zero = (ea == 0 and ma == 0)
    b_is_zero = (eb == 0 and mb == 0)
    a_is_inf  = (ea == FP32_EXP_MASK and ma == 0)
    b_is_inf  = (eb == FP32_EXP_MASK and mb == 0)
    if a_is_zero and b_is_zero:
        return FP32_NAN
    if a_is_inf and b_is_inf:
        return FP32_NAN
    if b_is_zero:
        return (sout << 31) | (FP32_EXP_MASK << 23)    # ±Inf
    if a_is_zero or b_is_inf:
        return (sout << 31)                             # ±0
    if a_is_inf:
        return (sout << 31) | (FP32_EXP_MASK << 23)    # ±Inf

    ea, sig_a = _fp32_normalize(ea, ma)
    eb, sig_b = _fp32_normalize(eb, mb)
    # The exact ratio is in [0.5, 2). Compute enough quotient bits for a
    # 24-bit significand plus guard/round/sticky after normalization.
    num = _widen_unsigned(sig_a, 56) << 27
    den = _widen_unsigned(sig_b, 56)
    # Preserve the IR's exact quotient/remainder operations, not an unrolled
    # restoring divider. A nonzero normalized significand makes den nonzero.
    q = num // den
    remainder = num % den
    extended = q | (1 if remainder != 0 else 0)
    e_out = ea - eb + FP32_EXP_BIAS
    if q >= (1 << 27):
        extended = _shift_right_jam(extended, 1)
    else:
        e_out -= 1
    return _fp32_round_pack(sout, e_out, extended)


# ── FP32 add / sub (RNE, gradual underflow) ──────────────────────────

def _normalize_leading_to_bit26(x):
    """Left-shift a 27-bit non-zero value until its leading 1 is at bit 26.

    Implemented as a log-depth if-ladder (max depth 5) so the concolic
    explorer sees a bounded number of branches rather than a data-
    dependent loop.  Returns (x_shifted, shift_count).
    """
    n = 0
    if (x >> 13) == 0:
        x <<= 13; n += 13
    if (x >> 20) == 0:
        x <<= 7;  n += 7
    if (x >> 23) == 0:
        x <<= 4;  n += 4
    if (x >> 25) == 0:
        x <<= 2;  n += 2
    if (x >> 26) == 0:
        x <<= 1;  n += 1
    return x, n


def fp32_add(a, b):
    """IEEE 754 binary32 addition. RNE rounding with gradual underflow."""
    sa, ea, ma = _fp32_fields(a)
    sb, eb, mb = _fp32_fields(b)

    # NaN propagation
    if (ea == FP32_EXP_MASK and ma != 0) or (eb == FP32_EXP_MASK and mb != 0):
        return FP32_NAN

    a_is_inf = (ea == FP32_EXP_MASK and ma == 0)
    b_is_inf = (eb == FP32_EXP_MASK and mb == 0)
    if a_is_inf and b_is_inf:
        # Inf - Inf is NaN; Inf + Inf (same sign) stays Inf.
        if sa == sb:
            return (sa << 31) | (FP32_EXP_MASK << 23)
        return FP32_NAN
    if a_is_inf:
        return (sa << 31) | (FP32_EXP_MASK << 23)
    if b_is_inf:
        return (sb << 31) | (FP32_EXP_MASK << 23)

    a_zero = (ea == 0 and ma == 0)
    b_zero = (eb == 0 and mb == 0)
    if a_zero and b_zero:
        # ±0 + ±0 : -0 iff both operands are -0 (round-to-nearest default).
        if sa == 1 and sb == 1:
            return 1 << 31
        return 0
    if a_zero:
        return _fp32_pack(sb, eb, mb)
    if b_zero:
        return _fp32_pack(sa, ea, ma)

    # Both inputs are nonzero finite. Pick the larger-magnitude operand
    # as "big"; sign of result is sign-of-big (for same sign that's
    # either; for different signs, the bigger magnitude dominates).
    mag_a = a & 0x7FFFFFFF
    mag_b = b & 0x7FFFFFFF
    if mag_a >= mag_b:
        sign_big, e_big, m_big = sa, ea, ma
        sign_sml, e_sml, m_sml = sb, eb, mb
    else:
        sign_big, e_big, m_big = sb, eb, mb
        sign_sml, e_sml, m_sml = sa, ea, ma

    e_big, sig_big = _fp32_normalize(e_big, m_big)
    e_sml, sig_sml = _fp32_normalize(e_sml, m_sml)
    # Three low bits carry guard/round/sticky through alignment and addition.
    ext_big = sig_big << 3
    ext_sml = sig_sml << 3
    sml_aligned = _shift_right_jam(ext_sml, e_big - e_sml)

    # Combine the two aligned extended significands.
    if sign_big == sign_sml:
        # Same sign → add magnitudes; result is at most 28 bits.
        sum_ext = ext_big + sml_aligned
        if (sum_ext >> 27) & 1:
            # Overflow into bit 27 → shift down, fold dropped bit into sticky.
            dropped = sum_ext & 1
            sum_ext = (sum_ext >> 1) | dropped
            e_out   = e_big + 1
        else:
            e_out   = e_big
    else:
        # Different signs → subtract magnitudes.  |big| >= |sml|.
        diff_ext = ext_big - sml_aligned
        if diff_ext == 0:
            # Exact cancellation collapses to +0 under default rounding.
            return 0
        # Normalize the leading 1 back up to bit 26.
        sum_ext, shift_amt = _normalize_leading_to_bit26(diff_ext)
        e_out = e_big - shift_amt

    return _fp32_round_pack(sign_big, e_out, sum_ext)


def fp32_sub(a, b):
    """IEEE 754 binary32 subtraction. RNE rounding with gradual underflow."""
    return fp32_add(a, fp32_neg(b))


# ── FP32 square root (RNE, including subnormal input) ────────────────

def _isqrt_fixed(target, num_pairs):
    """Floor of integer sqrt of `target`, processing 2 bits per iteration MSB-first.

    `num_pairs` is the iteration count; `target` must fit in `2 * num_pairs` bits.
    Returns (s, r) such that s*s + r == target and (s+1)^2 > target.
    Fixed iteration count keeps the concolic explorer's branch budget bounded.
    """
    s = 0
    r = 0
    for i in range(num_pairs - 1, -1, -1):
        r = (r << 2) | ((target >> (2 * i)) & 0b11)
        c = (s << 2) | 1               # trial digit: (2*s + 1) at the right scale
        if r >= c:
            r -= c
            s = (s << 1) | 1
        else:
            s <<= 1
    return s, r


def fp32_sqrt(u):
    """RNE binary32 square root. Preserve signed zero, canonicalize NaNs.

    All negative nonzero operands, including subnormals, return NaN.
    A positive subnormal has a nonzero, normal square root.
    """
    sign, exp, mant = _fp32_fields(u)

    # NaN propagation
    if exp == FP32_EXP_MASK and mant != 0:
        return FP32_NAN
    # ±0 (sign preserved)
    if exp == 0 and mant == 0:
        return u
    # Negative finite or -Inf
    if sign == 1:
        return FP32_NAN
    # +Inf
    if exp == FP32_EXP_MASK:
        return FP32_INF_POS

    # Normalize every positive finite input, including subnormals.
    exp, m24 = _fp32_normalize(exp, mant)
    e = exp - FP32_EXP_BIAS

    # Make (e - 23) even so the half-exponent is an integer.  When odd, shift
    # m24 left by one (m24 ∈ [2^24, 2^25)) and decrement e.
    if (e - 23) & 1:
        m24 <<= 1
        e -= 1

    # Compute integer sqrt of (m24 << 28) for 27-bit precision in s.
    # Worst-case target width: 25 + 28 = 53 bits → 27 iterations cover it.
    target = m24 << 28
    s, rem = _isqrt_fixed(target, 27)

    # s's leading bit position depends on whether m24 was 24-bit or 25-bit:
    #   m24 ∈ [2^23, 2^24)  →  target ∈ [2^51, 2^52)  →  s ∈ [2^25, 2^26)  (lead=25)
    #   m24 ∈ [2^24, 2^25)  →  target ∈ [2^52, 2^53)  →  s ∈ [2^26, 2^26.5) (lead=26)
    # The +1 carry case (s == 2^27) is handled by the rounding overflow path.
    if s >= (1 << 26):
        leading = 26
        shift = 3
    else:
        leading = 25
        shift = 2

    mant_implicit = (s >> shift) & ((1 << 24) - 1)
    guard = (s >> (shift - 1)) & 1
    low_mask = (1 << (shift - 1)) - 1
    sticky = 1 if ((s & low_mask) != 0 or rem != 0) else 0

    # RNE: round up if guard AND (sticky OR LSB odd)
    round_up = guard & (sticky | (mant_implicit & 1))
    mant_implicit += round_up
    if mant_implicit == (1 << 24):
        mant_implicit = 1 << 23
        leading += 1

    # value = m24 * 2^(e - 23); sqrt(value) = s * 2^((e - 51) / 2)
    # mant_implicit ≈ s / 2^(leading - 23), so:
    #   sqrt(value) = mant_implicit * 2^(leading + (e - 51)/2 - 23)
    # Hence the unbiased output exponent is (leading + (e - 51) // 2).
    e_out = leading + ((e - 51) >> 1)
    exp_out = e_out + FP32_EXP_BIAS

    # sqrt of every positive finite binary32 value is normal and finite.
    return _fp32_pack(0, exp_out, mant_implicit & FP32_MANT_MASK)


# ── FP32 reciprocal square root ────────────────────────────────────────
# Defined as the composition fp32_div(fp32(1.0), fp32_sqrt(u)).  Each call
# is RNE bit-exact, so the composition is fully deterministic; spec text
# that asks for fp32_rsqrt(x) means exactly this two-step composition,
# which RTL implementations must match (they may fuse the steps for
# performance as long as the final bit pattern is identical).

FP32_ONE = 0x3F800000  # +1.0


def fp32_rsqrt(u):
    """1/sqrt(u) via fp32_div(fp32(1.0), fp32_sqrt(u)).

    Edge cases inherit from sqrt + div:
      rsqrt(NaN)               -> NaN
      rsqrt(+0)                -> +Inf
      rsqrt(-0)                -> -Inf  (sqrt preserves sign, then 1/-0 = -Inf)
      rsqrt(+Inf)              -> +0
      rsqrt(-Inf)              -> NaN   (sqrt(-Inf) = NaN propagates)
      rsqrt(negative finite)   -> NaN
      rsqrt(positive subnormal)-> positive finite result
    """
    return fp32_div(FP32_ONE, fp32_sqrt(u))


# ── FP32 ↔ INT32 conversion ───────────────────────────────────────────
# Project-defined conversion: truncate finite values toward zero, saturate
# out-of-range values, and map NaN to zero. No exception flags are returned.
INT32_MAX = 0x7FFFFFFF
INT32_MIN = -0x80000000  # = -2147483648, two's-complement 0x80000000


def fp32_to_int32_sat(u):
    """Saturating truncation of an FP32 bit pattern to a signed int32.

    NaN -> 0; ±Inf and overflow -> INT32_MAX / INT32_MIN.
    Finite values truncate toward zero, so subnormals also become zero.
    Returns a Python int in the
    closed range [-2^31, 2^31 - 1].
    """
    sign, exp, mant = _fp32_fields(u)
    if exp == FP32_EXP_MASK:
        if mant != 0:
            return 0                       # NaN
        return INT32_MIN if sign else INT32_MAX
    if exp == 0:
        return 0                            # zero or subnormal has magnitude less than 1
    e = exp - FP32_EXP_BIAS
    if e < 0:
        return 0                            # |x| < 1 truncates to 0
    full_mant = (1 << FP32_MANT_W) | mant   # implicit-1 mantissa
    if e >= 23:
        mag = full_mant << (e - 23)
    else:
        mag = full_mant >> (23 - e)
    if sign:
        # Negative: representable down to -(2^31)
        return INT32_MIN if mag > (1 << 31) else -mag
    # Positive: representable up to 2^31 - 1
    return min(mag, INT32_MAX)


def int32_to_fp32(i):
    """Convert a signed int32 (Python int in [-2^31, 2^31-1]) to FP32, RNE.

    Inputs outside the int32 range are clamped to that range before rounding.
    """
    if i > INT32_MAX:
        i = INT32_MAX
    elif i < INT32_MIN:
        i = INT32_MIN
    if i == 0:
        return 0
    sign = 1 if i < 0 else 0
    mag = -i if i < 0 else i                # 1 .. 2^31
    # Find leading-1 position k so that 2^k <= mag < 2^(k+1).
    k = mag.bit_length() - 1                # 0..31
    exp_unbiased = k
    exp_field = exp_unbiased + FP32_EXP_BIAS  # 127 + k, with k <= 31
    if k <= 23:
        mant_full = mag << (23 - k)
        mant = mant_full & FP32_MANT_MASK
        return (sign << 31) | (exp_field << 23) | mant
    # k > 23: need to round.  Discard `k - 23` low bits with RNE on the
    # discarded tail.
    shift = k - 23
    discarded = mag & ((1 << shift) - 1)
    half = 1 << (shift - 1)
    mant_pre = (mag >> shift) & FP32_MANT_MASK
    if discarded > half:
        round_up = 1
    elif discarded < half:
        round_up = 0
    else:
        round_up = mant_pre & 1            # tie-to-even
    mant_round = mant_pre + round_up
    exp_round = exp_field
    if mant_round > FP32_MANT_MASK:
        mant_round &= FP32_MANT_MASK
        exp_round += 1                      # carry into exponent
    return (sign << 31) | (exp_round << 23) | mant_round


# ── BF16 ↔ FP32 ────────────────────────────────────────────────────────
# BF16 is the top 16 bits of FP32.

def bf16_to_fp32(u):
    """Zero-extend a BF16 bit pattern into the top 16 bits of an FP32."""
    return (u & 0xFFFF) << 16


def fp32_to_bf16(u):
    """Round FP32 to BF16 using RNE, retaining subnormal values.
    NaNs become 0x7FC0 or 0xFFC0 according to their sign.
    """
    sign = (u >> 31) & 1
    exp  = (u >> 23) & 0xFF
    mant = u & FP32_MANT_MASK

    if exp == 0xFF:
        if mant != 0:
            return (sign << 15) | 0x7FC0
        return (sign << 15) | 0x7F80

    top  = (u >> 16) & 0xFFFF
    low16 = u & 0xFFFF
    # RNE on a 16-bit tail: if tail > 0x8000 → round up; tail < 0x8000 → down;
    # tail == 0x8000 → round to even.
    if low16 < 0x8000:
        result = top
    elif low16 > 0x8000:
        result = top + 1
    else:  # exactly 0x8000 — round to even
        result = top + (top & 1)
    # Finite overflow rounds to infinity with the original sign.
    return result & 0xFFFF
