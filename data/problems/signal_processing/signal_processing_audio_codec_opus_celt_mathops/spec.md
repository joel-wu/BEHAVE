# signal_processing_audio_codec_opus_celt_mathops

This block implements one of six fixed-point CELT math helper functions per accepted input transaction. The selected function is identified by `in_op`, the operand is `in_x`, and the block produces exactly one 32-bit result `out_y` for each accepted input. The supported operations are: exact unsigned integer square root, reciprocal square-root approximation on normalized Q31 input, square-root approximation on nonnegative 32-bit input, cosine approximation on Q30 input, reciprocal approximation on normalized Q31 input, and reciprocal approximation on normalized Q15 input. All arithmetic is fixed-point two's-complement integer arithmetic with explicit wrap and truncation rules matching the formulas below.

## Interface

### RTL interface

```systemverilog
module signal_processing_audio_codec_opus_celt_mathops (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [2:0]  in_op,
    input  logic [31:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y
);
```

### Behavior interface

```python
class signal_processing_audio_codec_opus_celt_mathops:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_op, in_x):
        pass
```

Call `process` once per accepted input. It returns one dictionary
`{"out_y": result}`, where `result` is a Python integer in 0..4294967295
carrying the 32-bit output encoding, not a Python `float`. Zero results and
reserved operations still produce this dictionary, not `None`.

## Modules

- `signal_processing_audio_codec_opus_celt_mathops`: top-level module implementing all six operations and the 1:1 input/output transaction mapping.

## Functional Behavior

One accepted input transaction produces exactly one output transaction.

### Common integer conventions

- `in_x` and `out_y` are 32-bit raw bit patterns.
- When an operation interprets `in_x` as signed, it is two's-complement signed 32-bit.
- All returned `out_y` values are raw unsigned 32-bit encodings of the specified result.
- Helper arithmetic uses these definitions:
  - `to_signed16(v)`: truncate to 16 bits, then interpret as signed two's-complement.
  - `to_signed32(v)`: truncate to 32 bits, then interpret as signed two's-complement.
  - `add16(a,b) = to_signed16(a + b)`
  - `sub16(a,b) = to_signed16(a - b)`
  - `add32(a,b) = to_signed32(a + b)`
  - `sub32(a,b) = to_signed32(a - b)`
  - `mult16_16_q15(a,b) = to_signed16((a * b) >> 15)` using arithmetic right shift.
  - `mult32_32_q31(a,b) = to_signed32((a * b) >> 31)` using arithmetic right shift.
  - `shl16(a,s)`: if `s >= 0`, compute `to_signed16(a << s)`; if `s < 0`, compute `to_signed16(a >> (-s))` using arithmetic right shift.
  - `shl32(a,s)`: if `s >= 0`, compute `to_signed32(a << s)`; if `s < 0`, compute `to_signed32(a >> (-s))` using arithmetic right shift.
  - `shr32(a,s)`: if `s >= 0`, compute arithmetic right shift `a >> s`; if `s < 0`, compute `to_signed32(a << (-s))`.
  - `vshr32(a,s)`: identical to `shr32(a,s)`.
  - `pshr32(a,s)`: if `s > 0`, compute `to_signed32((a + (1 << (s-1))) >> s)` using arithmetic right shift; if `s <= 0`, compute `to_signed32(a << (-s))`.
- `celt_ilog2_u32(x)` is defined for unsigned 32-bit `x` as:
  - `0` when `x == 0`
  - otherwise `floor(log2(x)) + 1`

### Operation encoding

`in_op` selects the function:

- `0`: `isqrt32`
- `1`: `celt_rsqrt_norm32`
- `2`: `celt_sqrt32`
- `3`: `celt_cos_norm32`
- `4`: `celt_rcp_norm32`
- `5`: `celt_rcp_norm16`
- `6` and `7`: reserved, output zero

### `in_op == 0`: exact unsigned integer square root

Interpret `in_x` as unsigned 32-bit `_val`.

- If `_val == 0`, `out_y = 0`.
- Otherwise compute `g = floor(sqrt(_val))` exactly, using the binary-digit search:
  1. `bshift = (celt_ilog2_u32(_val) - 1) >> 1`
  2. `b = 1 << bshift`
  3. `g = 0`
  4. While `bshift >= 0`:
     - `t = (((g << 1) + b) << bshift)` computed in unbounded unsigned integer arithmetic
     - if `t <= _val`, then set `g = g + b` and `_val = _val - t`
     - `b = b >> 1`
     - `bshift = bshift - 1`
- Return `out_y = g` as unsigned 32-bit.

Worked example: for `in_x = 9`, the result is `3`.

### `in_op == 1`: reciprocal square-root approximation on normalized Q31 input

Interpret `in_x` as signed 32-bit `x`. The intended mathematical domain is Q31 with `x` in `[0.25, 1)`, but this block defines outputs for all 32-bit inputs by applying the arithmetic below exactly.

- If `x <= 0`, `out_y = 0`.
- Otherwise compute `celt_rsqrt_norm32(x)` in Q29:
  1. Form `x16 = shr32(x, 15)`. This is the Q16 approximation input for the 16-bit stage.
  2. Compute `r0 = celt_rsqrt_norm(x16)` where `celt_rsqrt_norm()` takes Q16 input and returns Q14:
     - `n = to_signed16(x16 - 32768)`
     - `r = add16(23557, mult16_16_q15(n, add16(-13490, mult16_16_q15(n, 6713))))`
     - `r2 = mult16_16_q15(r, r)`
     - `y = shl16(sub16(add16(mult16_16_q15(r2, n), r2), 16384), 1)`
     - `r0 = add16(r, mult16_16_q15(r, mult16_16_q15(y, sub16(mult16_16_q15(y, 12288), 16384))))`
  3. Promote to Q29: `r_q29 = shl32(r0, 15)`
  4. Newton refinement:
     - `tmp = mult32_32_q31(r_q29, r_q29)`
     - `tmp = mult32_32_q31(1073741824, tmp)`
     - `tmp = mult32_32_q31(x, tmp)`
     - `result = shl32(mult32_32_q31(r_q29, sub32(201326592, tmp)), 4)`
- Return `out_y = result`.

`1073741824` is the Q31 encoding of `0.5`. `201326592` is the Q27 encoding of `1.5`.

### `in_op == 2`: square-root approximation

Interpret `in_x` as signed 32-bit `x`.

- If `x <= 0`, `out_y = 0`.
- Else if `x >= 1073741824`, `out_y = 2147483647`.
- Otherwise compute:
  1. `k = (celt_ilog2_u32(x as unsigned) - 1) >> 1`, i.e. `floor(log2(x)/2)`
  2. `x_frac = vshr32(x, 2*(k - 14) - 1)`
  3. `x_frac = mult32_32_q31(celt_rsqrt_norm32(x_frac), x_frac)`
  4. If `k < 12`, `result = pshr32(x_frac, 12 - k)`, else `result = shl32(x_frac, k - 12)`
- Return `out_y = result`.

This operation uses the `celt_rsqrt_norm32` definition from `in_op == 1` as an internal subroutine.
The output has 16 more fractional bits than the square root's input-scale
half: for an integer input it represents `sqrt(x) * 2^16`. The saturation
threshold is applied as specified above.

### `in_op == 3`: cosine approximation on Q30 input

Interpret `in_x` as signed 32-bit `x`, intended as Q30 in `[-1, 1]`. The block defines outputs for all 32-bit inputs by the arithmetic below.

Constants:
- `A0 = 134217720` in Q27
- `A1 = -662336704` in Q29
- `A2 = 544710848` in Q31
- `A3 = -178761936` in Q33
- `A4 = 29487206` in Q35

Computation:
- Let `ax = x` if `x >= 0`, else `-x`.
- If `ax == 1073741824`, return `0`. This makes the result exactly zero at `x = ±1.0` in Q30.
- Otherwise:
  1. `x_sq_q29 = mult32_32_q31(x, x)`
  2. `tmp = add32(A3, mult32_32_q31(x_sq_q29, A4))`
  3. `tmp = add32(A2, mult32_32_q31(x_sq_q29, tmp))`
  4. `tmp = add32(A1, mult32_32_q31(x_sq_q29, tmp))`
  5. `result = shl32(add32(A0, mult32_32_q31(x_sq_q29, tmp)), 4)`
- Return `out_y = result`.

Worked anchors:
- `in_x = 0` returns `2147483520`, which is the Q31 approximation of `1.0`.
- `in_x = 1073741824` or `in_x = -1073741824` returns `0`.

### `in_op == 4`: reciprocal approximation on normalized Q31 input

Interpret `in_x` as signed 32-bit `x`.

- If `x < 1073741824`, `out_y = 0`.
- Otherwise compute `celt_rcp_norm32(x)`:
  1. Form a normalized Q15 input to the 16-bit reciprocal stage:
     - `x16 = to_signed16(shr32(x, 15) - 32768)`
  2. Compute `r16 = celt_rcp_norm16(x16)` where `celt_rcp_norm16()` is defined below for `in_op == 5`.
  3. Promote to Q30: `r_q30 = shl32(r16, 16)`
  4. Newton refinement:
     - `tmp = mult32_32_q31(r_q30, x)`
     - `tmp = add32(tmp, -1073741824)`
     - `tmp = mult32_32_q31(tmp, r_q30)`
     - `result = sub32(r_q30, add32(shl32(tmp, 1), 1))`
- Return `out_y = result`.

`1073741824` is the Q30 encoding of `1.0`.

### `in_op == 5`: reciprocal approximation on normalized Q15 input

Interpret the low 16 bits of `in_x` as signed 16-bit `x16`. Bits `[31:16]` of `in_x` are ignored.

Compute `celt_rcp_norm16(x16)` and sign-extend the 16-bit result to 32 bits:

1. `r = add16(30840, mult16_16_q15(-15420, x16))`
2. First Newton step:
   - `t = add16(mult16_16_q15(r, x16), add16(r, -32768))`
   - `r = sub16(r, mult16_16_q15(r, t))`
3. Second Newton step with the extra subtract-one term:
   - `t = add16(mult16_16_q15(r, x16), add16(r, -32768))`
   - `r = sub16(r, add16(1, mult16_16_q15(r, t)))`

Return `out_y = sign_extend_16_to_32(r)` encoded as unsigned 32-bit.

This operation is defined for all 16-bit input patterns.

### `in_op == 6` or `in_op == 7`

Return `out_y = 0`.

## Reset Behavior

- Reset is synchronous active-high.
- On reset:
  - `out_y = 0`
  - the module contains no architectural computation state beyond the output register
  - after reset, the block is ready to accept a new input transaction and no output transaction is pending
