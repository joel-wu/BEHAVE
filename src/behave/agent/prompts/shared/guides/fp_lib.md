# `fp_lib` numerical API specification

## Scope and representation

`fp_lib` provides Python functions on integer-encoded floating-point values.
FP32 denotes the IEEE 754 binary32 format. Its arithmetic operations use
roundTiesToEven and gradual underflow. Subnormal operands retain their values.
Subnormal results are rounded, not flushed to zero.

This API specifies returned values. It does not expose exception flags,
traps or a selectable rounding mode. It is not an implementation of the
entire IEEE 754 floating-point environment. BF16, saturating integer
conversion and the composed reciprocal square root are extensions specified
separately below. BF16 is not IEEE 754 binary16.

Arguments and results identified as FP32 are Python integers in `[0, 2**32 - 1]`.
Those identified as BF16 are Python integers in `[0, 2**16 - 1]`.
These integers encode bits, not the mathematical value. The functions do
not return Python floats. Classification returns `bool`, comparison returns
an ordering code, and integer conversion uses signed Python integer values.

| Format | Sign bit | Exponent bits | Fraction bits | Exponent bias | Normal precision |
| --- | --- | --- | --- | --- | --- |
| FP32 | 31 | 30:23 | 22:0 | 127 | 24 bits |
| BF16 | 15 | 14:7 | 6:0 | 127 | 8 bits |

Let `s` be the sign bit, `e` the unsigned exponent field, and `f` the
unsigned fraction field. Let `q = 23` for FP32 and `q = 7` for BF16.

- `1 <= e <= 254`: the value is `(-1)**s * (1 + f / 2**q) * 2**(e - 127)`.
- `e == 0, f != 0`: the subnormal value is `(-1)**s * f * 2**(-126 - q)`.
- `e == 0, f == 0`: positive zero when `s == 0`, negative zero when `s == 1`.
- `e == 255, f == 0`: infinity with sign `s`.
- `e == 255, f != 0`: NaN. This API uses the preferred IEEE binary encoding:
  the most significant fraction bit is set for quiet NaNs and clear for
  signaling NaNs.

The following finite values are exact. Exponents use Python's `**` notation.

| Format | Encoding | Value |
| --- | --- | --- |
| FP32 | `0x00000000` | `0.0` |
| FP32 | `0x80000000` | `-0.0` |
| FP32 | `0x00000001` | `2**-149` |
| FP32 | `0x007FFFFF` | `(1 - 2**-23) * 2**-126` |
| FP32 | `0x00800000` | `2**-126` |
| FP32 | `0x3F800000` | `1` |
| FP32 | `0x3F800001` | `1 + 2**-23` |
| FP32 | `0x7F7FFFFF` | `(2 - 2**-23) * 2**127` |
| BF16 | `0x0000` | `0.0` |
| BF16 | `0x8000` | `-0.0` |
| BF16 | `0x0001` | `2**-133` |
| BF16 | `0x007F` | `(1 - 2**-7) * 2**-126` |
| BF16 | `0x0080` | `2**-126` |
| BF16 | `0x3F80` | `1` |
| BF16 | `0x3F81` | `1 + 2**-7` |
| BF16 | `0x7F7F` | `(2 - 2**-7) * 2**127` |

## Classification, sign and comparison

| Function | Result |
| --- | --- |
| `fp32_is_zero(u)` | `True` exactly when `e == 0` and `f == 0`. |
| `fp32_is_nan(u)` | `True` exactly when `e == 255` and `f != 0`. |
| `fp32_is_inf(u)` | `True` exactly when `e == 255` and `f == 0`. |
| `fp32_abs(u)` | FP32 encoding with the sign bit cleared. All other bits are preserved. |
| `fp32_neg(u)` | FP32 encoding with the sign bit toggled. All other bits are preserved. |
| `fp32_cmp(a, b)` | `-1` if `a < b`, `0` if `a == b`, `1` if `a > b`, or `-2` if either operand is NaN. |

Comparison orders numerical values, including subnormals and infinities.
Positive and negative zero compare equal. The `-2` code means unordered,
not less than. Sign operations preserve NaN payload and signaling/quiet bits.

## FP32 arithmetic

| Function | Mathematical operation before rounding |
| --- | --- |
| `fp32_add(a, b)` | Addition. |
| `fp32_sub(a, b)` | Subtraction. |
| `fp32_mul(a, b)` | Multiplication. |
| `fp32_div(a, b)` | Division. |
| `fp32_sqrt(u)` | Nonnegative square root. |

Each operation rounds its exact result once to binary32 using
roundTiesToEven: select the nearest representable value, choosing the one
with an even least significant significand bit at a midpoint. This rule
also applies at the zero/subnormal and subnormal/normal boundaries.
Overflow under this rounding mode returns signed infinity.

NaN operands and invalid arithmetic return the quiet-NaN encoding
`0x7FC00000`. This fixed sign and payload are API choices, not a unique NaN
encoding mandated by IEEE 754. Both signaling and quiet NaNs produce this
value. No exception status is returned.

Special values follow these rules after checking for NaNs:

- Addition of opposite infinities, subtraction of equal infinities,
  zero times infinity, zero divided by zero, and infinity divided by
  infinity return NaN.
- Multiplication and division use the exclusive-or of operand signs for
  any non-NaN result, including zero and infinity. Finite nonzero divided
  by zero returns signed infinity. Finite divided by infinity returns signed zero.
- Exact cancellation in addition returns positive zero. Adding two
  negative zeros returns negative zero. Subtraction has the same sign
  rules as addition with the second operand's sign toggled.
- Square root preserves signed zero and positive infinity. Every negative
  nonzero input, including a negative subnormal or negative infinity,
  returns NaN.

`fp32_rsqrt(u)` is defined as
`fp32_div(0x3F800000, fp32_sqrt(u))`. The intermediate square root is rounded
to FP32 before division. It is not a single correctly rounded operation
on the exact mathematical reciprocal square root.

## Format and integer conversions

`bf16_to_fp32(u)` returns `u << 16`. This is an exact embedding for finite
BF16 values, including subnormals and signed zeros. It preserves the encoded
sign, exponent and fraction for infinities and NaNs. In particular, it does
not quiet signaling NaNs. It is a bit-format embedding, not an IEEE 754
computational conversion with exception handling.

`fp32_to_bf16(u)` rounds finite FP32 values directly to BF16 using
roundTiesToEven with gradual underflow. Signed zero and infinity retain their
sign. NaNs map to `0x7FC0` for a positive sign and `0xFFC0` for a negative
sign. Finite rounding can produce a subnormal, zero or infinity.

For `fp32_to_int32_sat(u)`, finite values are truncated toward zero and
then clamped to `[-2147483648, 2147483647]`, equivalently `[-2**31, 2**31 - 1]`.
Positive infinity returns `2147483647`, negative infinity returns
`-2147483648`, and NaN returns `0`. Both zeros and all FP32 subnormals return
`0`. This saturation/NaN policy is an API extension, not IEEE 754's full
float-to-integer exception semantics.

`int32_to_fp32(i)` accepts a signed Python integer. It first clamps `i` to
`[-2147483648, 2147483647]`, then converts that integer to binary32 using
roundTiesToEven. For example, clamping `2147483648` produces `2147483647`,
whose FP32 rounding is `2147483648`, encoded as `0x4F000000`.

## Conformance examples

Functions are imported from `fp_lib`, for example
`from fp_lib import fp32_add, fp32_cmp`. Inputs and outputs below are integer
encodings unless the function returns a boolean, ordering code or signed integer.

| Call | Required result |
| --- | --- |
| `fp32_is_zero(0x80000000)` | `True` |
| `fp32_is_zero(0x00000001)` | `False` |
| `fp32_is_nan(0xFF800001)` | `True` |
| `fp32_is_inf(0xFF800000)` | `True` |
| `fp32_abs(0xFF812345)` | `0x7F812345` |
| `fp32_neg(0x7F812345)` | `0xFF812345` |
| `fp32_cmp(0x00000001, 0x00000000)` | `1` |
| `fp32_cmp(0x80000001, 0x00000000)` | `-1` |
| `fp32_cmp(0x80000000, 0x00000000)` | `0` |
| `fp32_cmp(0xC0000000, 0xBF800000)` | `-1` |
| `fp32_cmp(0x40000000, 0x3F800000)` | `1` |
| `fp32_cmp(0x7FC00000, 0x00000000)` | `-2` |
| `fp32_add(0x3F800000, 0x33800000)` | `0x3F800000` |
| `fp32_add(0x3F800001, 0x33800000)` | `0x3F800002` |
| `fp32_add(0x80000000, 0x80000000)` | `0x80000000` |
| `fp32_add(0x00000001, 0x00000001)` | `0x00000002` |
| `fp32_sub(0x3F800000, 0x3F800000)` | `0x00000000` |
| `fp32_sub(0x00800001, 0x00800000)` | `0x00000001` |
| `fp32_mul(0x3FC00000, 0x40000000)` | `0x40400000` |
| `fp32_mul(0x00800000, 0x3F7FFFFF)` | `0x00800000` |
| `fp32_mul(0x80800000, 0x3F7FFFFF)` | `0x80800000` |
| `fp32_mul(0x00800000, 0x3F000000)` | `0x00400000` |
| `fp32_mul(0x80800000, 0x3F000000)` | `0x80400000` |
| `fp32_mul(0x00000001, 0x3F000000)` | `0x00000000` |
| `fp32_mul(0x00000003, 0x3F000000)` | `0x00000002` |
| `fp32_mul(0x7F7FFFFF, 0x40000000)` | `0x7F800000` |
| `fp32_mul(0x00000000, 0x7F800000)` | `0x7FC00000` |
| `fp32_div(0x3F800000, 0x40400000)` | `0x3EAAAAAB` |
| `fp32_div(0x3F800000, 0x80000000)` | `0xFF800000` |
| `fp32_div(0x00000000, 0x00000000)` | `0x7FC00000` |
| `fp32_div(0x00FFFFFF, 0x40000000)` | `0x00800000` |
| `fp32_div(0x80FFFFFF, 0x40000000)` | `0x80800000` |
| `fp32_div(0x00800000, 0x40000000)` | `0x00400000` |
| `fp32_sqrt(0x40800000)` | `0x40000000` |
| `fp32_sqrt(0x80000000)` | `0x80000000` |
| `fp32_sqrt(0x00000002)` | `0x1A800000` |
| `fp32_sqrt(0x80000001)` | `0x7FC00000` |
| `fp32_sqrt(0xBF800000)` | `0x7FC00000` |
| `fp32_sqrt(0x7F800000)` | `0x7F800000` |
| `fp32_rsqrt(0x40800000)` | `0x3F000000` |
| `fp32_rsqrt(0x00000000)` | `0x7F800000` |
| `fp32_rsqrt(0x80000000)` | `0xFF800000` |
| `fp32_rsqrt(0x80000001)` | `0x7FC00000` |
| `fp32_rsqrt(0x7F800000)` | `0x00000000` |
| `bf16_to_fp32(0x3F80)` | `0x3F800000` |
| `bf16_to_fp32(0x0001)` | `0x00010000` |
| `bf16_to_fp32(0x7F81)` | `0x7F810000` |
| `fp32_to_bf16(0x3F808000)` | `0x3F80` |
| `fp32_to_bf16(0x3F818000)` | `0x3F82` |
| `fp32_to_bf16(0x00010000)` | `0x0001` |
| `fp32_to_bf16(0x00008000)` | `0x0000` |
| `fp32_to_bf16(0x00018000)` | `0x0002` |
| `fp32_to_bf16(0x007F8000)` | `0x0080` |
| `fp32_to_bf16(0xFF800001)` | `0xFFC0` |
| `fp32_to_bf16(0x7F7FFFFF)` | `0x7F80` |
| `fp32_to_int32_sat(0x40700000)` | `3` |
| `fp32_to_int32_sat(0xC0700000)` | `-3` |
| `fp32_to_int32_sat(0x7FC00001)` | `0` |
| `fp32_to_int32_sat(0x7F800000)` | `2147483647` |
| `fp32_to_int32_sat(0xFF800000)` | `-2147483648` |
| `int32_to_fp32(-1)` | `0xBF800000` |
| `int32_to_fp32(16777217)` | `0x4B800000` |
| `int32_to_fp32(16777219)` | `0x4B800002` |
| `int32_to_fp32(2147483648)` | `0x4F000000` |

## References

The binary32 format and arithmetic terminology follow
[IEEE Std 754-2019](https://standards.ieee.org/ieee/754/6210/).
BF16 uses the sign/exponent/fraction layout shown in
[Intel's floating-point format reference](https://www.intel.com/content/www/us/en/developer/articles/technical/floating-point-reference-sheet-for-intel-architecture.html).
The BF16 conversion and integer saturation policies in this document are
explicit API definitions, not a claim of conformance to a BF16 arithmetic standard.
