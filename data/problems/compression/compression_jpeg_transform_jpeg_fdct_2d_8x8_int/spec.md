# JPEG Forward DCT 2D 8x8 Integer Transform

This block computes the 2-D forward discrete cosine transform of one 8x8 signed sample block using the Loeffler-Ligtenberg-Moschytz 8-point integer algorithm applied as an 8-point 1-D transform on each row followed by an 8-point 1-D transform on each column. The arithmetic is fixed-point integer arithmetic with `CONST_BITS = 13`, `PASS1_BITS = 2`, the exact pre-scaled cosine constants listed below, and the same rounding rule at every descale step. The output coefficients remain scaled by an overall factor of 8 relative to an orthonormal DCT.

## Interface

### RTL interface

```systemverilog
module compression_jpeg_transform_jpeg_fdct_2d_8x8_int (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [11:0]  in_s0,
    input  logic [11:0]  in_s1,
    input  logic [11:0]  in_s2,
    input  logic [11:0]  in_s3,
    input  logic [11:0]  in_s4,
    input  logic [11:0]  in_s5,
    input  logic [11:0]  in_s6,
    input  logic [11:0]  in_s7,
    input  logic [11:0]  in_s8,
    input  logic [11:0]  in_s9,
    input  logic [11:0]  in_s10,
    input  logic [11:0]  in_s11,
    input  logic [11:0]  in_s12,
    input  logic [11:0]  in_s13,
    input  logic [11:0]  in_s14,
    input  logic [11:0]  in_s15,
    input  logic [11:0]  in_s16,
    input  logic [11:0]  in_s17,
    input  logic [11:0]  in_s18,
    input  logic [11:0]  in_s19,
    input  logic [11:0]  in_s20,
    input  logic [11:0]  in_s21,
    input  logic [11:0]  in_s22,
    input  logic [11:0]  in_s23,
    input  logic [11:0]  in_s24,
    input  logic [11:0]  in_s25,
    input  logic [11:0]  in_s26,
    input  logic [11:0]  in_s27,
    input  logic [11:0]  in_s28,
    input  logic [11:0]  in_s29,
    input  logic [11:0]  in_s30,
    input  logic [11:0]  in_s31,
    input  logic [11:0]  in_s32,
    input  logic [11:0]  in_s33,
    input  logic [11:0]  in_s34,
    input  logic [11:0]  in_s35,
    input  logic [11:0]  in_s36,
    input  logic [11:0]  in_s37,
    input  logic [11:0]  in_s38,
    input  logic [11:0]  in_s39,
    input  logic [11:0]  in_s40,
    input  logic [11:0]  in_s41,
    input  logic [11:0]  in_s42,
    input  logic [11:0]  in_s43,
    input  logic [11:0]  in_s44,
    input  logic [11:0]  in_s45,
    input  logic [11:0]  in_s46,
    input  logic [11:0]  in_s47,
    input  logic [11:0]  in_s48,
    input  logic [11:0]  in_s49,
    input  logic [11:0]  in_s50,
    input  logic [11:0]  in_s51,
    input  logic [11:0]  in_s52,
    input  logic [11:0]  in_s53,
    input  logic [11:0]  in_s54,
    input  logic [11:0]  in_s55,
    input  logic [11:0]  in_s56,
    input  logic [11:0]  in_s57,
    input  logic [11:0]  in_s58,
    input  logic [11:0]  in_s59,
    input  logic [11:0]  in_s60,
    input  logic [11:0]  in_s61,
    input  logic [11:0]  in_s62,
    input  logic [11:0]  in_s63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_d0,
    output logic [15:0]  out_d1,
    output logic [15:0]  out_d2,
    output logic [15:0]  out_d3,
    output logic [15:0]  out_d4,
    output logic [15:0]  out_d5,
    output logic [15:0]  out_d6,
    output logic [15:0]  out_d7,
    output logic [15:0]  out_d8,
    output logic [15:0]  out_d9,
    output logic [15:0]  out_d10,
    output logic [15:0]  out_d11,
    output logic [15:0]  out_d12,
    output logic [15:0]  out_d13,
    output logic [15:0]  out_d14,
    output logic [15:0]  out_d15,
    output logic [15:0]  out_d16,
    output logic [15:0]  out_d17,
    output logic [15:0]  out_d18,
    output logic [15:0]  out_d19,
    output logic [15:0]  out_d20,
    output logic [15:0]  out_d21,
    output logic [15:0]  out_d22,
    output logic [15:0]  out_d23,
    output logic [15:0]  out_d24,
    output logic [15:0]  out_d25,
    output logic [15:0]  out_d26,
    output logic [15:0]  out_d27,
    output logic [15:0]  out_d28,
    output logic [15:0]  out_d29,
    output logic [15:0]  out_d30,
    output logic [15:0]  out_d31,
    output logic [15:0]  out_d32,
    output logic [15:0]  out_d33,
    output logic [15:0]  out_d34,
    output logic [15:0]  out_d35,
    output logic [15:0]  out_d36,
    output logic [15:0]  out_d37,
    output logic [15:0]  out_d38,
    output logic [15:0]  out_d39,
    output logic [15:0]  out_d40,
    output logic [15:0]  out_d41,
    output logic [15:0]  out_d42,
    output logic [15:0]  out_d43,
    output logic [15:0]  out_d44,
    output logic [15:0]  out_d45,
    output logic [15:0]  out_d46,
    output logic [15:0]  out_d47,
    output logic [15:0]  out_d48,
    output logic [15:0]  out_d49,
    output logic [15:0]  out_d50,
    output logic [15:0]  out_d51,
    output logic [15:0]  out_d52,
    output logic [15:0]  out_d53,
    output logic [15:0]  out_d54,
    output logic [15:0]  out_d55,
    output logic [15:0]  out_d56,
    output logic [15:0]  out_d57,
    output logic [15:0]  out_d58,
    output logic [15:0]  out_d59,
    output logic [15:0]  out_d60,
    output logic [15:0]  out_d61,
    output logic [15:0]  out_d62,
    output logic [15:0]  out_d63
);
```

All `in_s*` ports are signed 12-bit two's-complement integers. All `out_d*` ports are signed 16-bit two's-complement integers.

### Behavior interface

```python
class CompressionJpegTransformJpegFdct2d8x8Int:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_s0, in_s1, in_s2, in_s3, in_s4, in_s5, in_s6, in_s7,
        in_s8, in_s9, in_s10, in_s11, in_s12, in_s13, in_s14, in_s15,
        in_s16, in_s17, in_s18, in_s19, in_s20, in_s21, in_s22, in_s23,
        in_s24, in_s25, in_s26, in_s27, in_s28, in_s29, in_s30, in_s31,
        in_s32, in_s33, in_s34, in_s35, in_s36, in_s37, in_s38, in_s39,
        in_s40, in_s41, in_s42, in_s43, in_s44, in_s45, in_s46, in_s47,
        in_s48, in_s49, in_s50, in_s51, in_s52, in_s53, in_s54, in_s55,
        in_s56, in_s57, in_s58, in_s59, in_s60, in_s61, in_s62, in_s63
    ):
        pass
```

Call `process(...)` once per accepted block. Arguments are Python integers
encoding the signed 12-bit samples. Every call returns one dictionary with
exactly the 64 keys `out_d0` through `out_d63`, each containing a Python integer
encoding the corresponding 16-bit coefficient. The field order follows the
row-major mapping below. An all-zero block still returns all 64 fields, not
`None`, a scalar, or a list of coefficient records. Calls do not represent
pipeline cycles or stalls. `reset()` returns `None`; no computation state is
shared between blocks.

## Modules

- `compression_jpeg_transform_jpeg_fdct_2d_8x8_int`: top-level block that accepts one signed 8x8 sample block and returns one signed 8x8 coefficient block.
- `fdct_1d_8`: optional helper implementing one 8-point Loeffler integer forward DCT stage for either a row or a column, with the shift amount parameterized by pass.
- `descale_round`: optional helper implementing signed rounding right shift `DESCALE(x, n)`.

## Functional Behavior

The block is a 1:1 transform. One accepted input transaction contains 64 signed samples arranged in raster order:

- row 0: `in_s0`..`in_s7`
- row 1: `in_s8`..`in_s15`
- row 2: `in_s16`..`in_s23`
- row 3: `in_s24`..`in_s31`
- row 4: `in_s32`..`in_s39`
- row 5: `in_s40`..`in_s47`
- row 6: `in_s48`..`in_s55`
- row 7: `in_s56`..`in_s63`

The 64 outputs `out_d0`..`out_d63` are returned in the same raster order after applying the two-pass 8x8 forward DCT described below.

Input values are interpreted as signed 12-bit two's-complement integers. Output values are signed 16-bit two's-complement integers. No saturation is applied internally or at the output; results are the exact integer results of the specified algorithm, then represented modulo `2^16` on the output ports.

Constants are fixed and exact:

- `CONST_BITS = 13`
- `PASS1_BITS = 2`
- `FIX_0_298631336 = 2446`
- `FIX_0_390180644 = 3196`
- `FIX_0_541196100 = 4433`
- `FIX_0_765366865 = 6270`
- `FIX_0_899976223 = 7373`
- `FIX_1_175875602 = 9633`
- `FIX_1_501321110 = 12299`
- `FIX_1_847759065 = 15137`
- `FIX_1_961570560 = 16069`
- `FIX_2_053119869 = 16819`
- `FIX_2_562915447 = 20995`
- `FIX_3_072711026 = 25172`

`MULTIPLY(a, c)` is ordinary signed integer multiplication.

`LEFT_SHIFT(x, n)` is arithmetic left shift by the non-negative constant `n`.

`DESCALE(x, n)` is signed rounded right shift by the non-negative constant `n`:
- add rounding bias `1 << (n - 1)` to `x`
- arithmetic right shift the sum by `n`
- equivalently, `DESCALE(x, n) = (x + (1 << (n - 1))) >> n`
- this is the exact contract for both positive and negative `x`

The transform uses an internal 8x8 integer array `data[0..63]`. On input acceptance, `data[i]` is initialized from `in_s<i>` as a signed integer.

### Pass 1: row transform

For each row `r` from 0 to 7, define:

- `x0 = data[8*r + 0]`
- `x1 = data[8*r + 1]`
- `x2 = data[8*r + 2]`
- `x3 = data[8*r + 3]`
- `x4 = data[8*r + 4]`
- `x5 = data[8*r + 5]`
- `x6 = data[8*r + 6]`
- `x7 = data[8*r + 7]`

Compute:

- `tmp0 = x0 + x7`
- `tmp7 = x0 - x7`
- `tmp1 = x1 + x6`
- `tmp6 = x1 - x6`
- `tmp2 = x2 + x5`
- `tmp5 = x2 - x5`
- `tmp3 = x3 + x4`
- `tmp4 = x3 - x4`

Even part:

- `tmp10 = tmp0 + tmp3`
- `tmp13 = tmp0 - tmp3`
- `tmp11 = tmp1 + tmp2`
- `tmp12 = tmp1 - tmp2`

Write row outputs:

- `data[8*r + 0] = LEFT_SHIFT(tmp10 + tmp11, PASS1_BITS)`
- `data[8*r + 4] = LEFT_SHIFT(tmp10 - tmp11, PASS1_BITS)`

Then:

- `z1 = MULTIPLY(tmp12 + tmp13, FIX_0_541196100)`
- `data[8*r + 2] = DESCALE(z1 + MULTIPLY(tmp13, FIX_0_765366865), CONST_BITS - PASS1_BITS)`
- `data[8*r + 6] = DESCALE(z1 + MULTIPLY(tmp12, -FIX_1_847759065), CONST_BITS - PASS1_BITS)`

Odd part:

- `z1 = tmp4 + tmp7`
- `z2 = tmp5 + tmp6`
- `z3 = tmp4 + tmp6`
- `z4 = tmp5 + tmp7`
- `z5 = MULTIPLY(z3 + z4, FIX_1_175875602)`

Then replace:

- `tmp4 = MULTIPLY(tmp4, FIX_0_298631336)`
- `tmp5 = MULTIPLY(tmp5, FIX_2_053119869)`
- `tmp6 = MULTIPLY(tmp6, FIX_3_072711026)`
- `tmp7 = MULTIPLY(tmp7, FIX_1_501321110)`
- `z1 = MULTIPLY(z1, -FIX_0_899976223)`
- `z2 = MULTIPLY(z2, -FIX_2_562915447)`
- `z3 = MULTIPLY(z3, -FIX_1_961570560)`
- `z4 = MULTIPLY(z4, -FIX_0_390180644)`

Then:

- `z3 = z3 + z5`
- `z4 = z4 + z5`

Write row outputs:

- `data[8*r + 7] = DESCALE(tmp4 + z1 + z3, CONST_BITS - PASS1_BITS)`
- `data[8*r + 5] = DESCALE(tmp5 + z2 + z4, CONST_BITS - PASS1_BITS)`
- `data[8*r + 3] = DESCALE(tmp6 + z2 + z3, CONST_BITS - PASS1_BITS)`
- `data[8*r + 1] = DESCALE(tmp7 + z1 + z4, CONST_BITS - PASS1_BITS)`

After pass 1, `data[]` contains row-transformed values scaled by `2^PASS1_BITS` in addition to the algorithm’s inherent 1-D scaling.

### Pass 2: column transform

For each column `c` from 0 to 7, define:

- `x0 = data[8*0 + c]`
- `x1 = data[8*1 + c]`
- `x2 = data[8*2 + c]`
- `x3 = data[8*3 + c]`
- `x4 = data[8*4 + c]`
- `x5 = data[8*5 + c]`
- `x6 = data[8*6 + c]`
- `x7 = data[8*7 + c]`

Compute:

- `tmp0 = x0 + x7`
- `tmp7 = x0 - x7`
- `tmp1 = x1 + x6`
- `tmp6 = x1 - x6`
- `tmp2 = x2 + x5`
- `tmp5 = x2 - x5`
- `tmp3 = x3 + x4`
- `tmp4 = x3 - x4`

Even part:

- `tmp10 = tmp0 + tmp3`
- `tmp13 = tmp0 - tmp3`
- `tmp11 = tmp1 + tmp2`
- `tmp12 = tmp1 - tmp2`

Write column outputs:

- `data[8*0 + c] = DESCALE(tmp10 + tmp11, PASS1_BITS)`
- `data[8*4 + c] = DESCALE(tmp10 - tmp11, PASS1_BITS)`

Then:

- `z1 = MULTIPLY(tmp12 + tmp13, FIX_0_541196100)`
- `data[8*2 + c] = DESCALE(z1 + MULTIPLY(tmp13, FIX_0_765366865), CONST_BITS + PASS1_BITS)`
- `data[8*6 + c] = DESCALE(z1 + MULTIPLY(tmp12, -FIX_1_847759065), CONST_BITS + PASS1_BITS)`

Odd part:

- `z1 = tmp4 + tmp7`
- `z2 = tmp5 + tmp6`
- `z3 = tmp4 + tmp6`
- `z4 = tmp5 + tmp7`
- `z5 = MULTIPLY(z3 + z4, FIX_1_175875602)`

Then replace:

- `tmp4 = MULTIPLY(tmp4, FIX_0_298631336)`
- `tmp5 = MULTIPLY(tmp5, FIX_2_053119869)`
- `tmp6 = MULTIPLY(tmp6, FIX_3_072711026)`
- `tmp7 = MULTIPLY(tmp7, FIX_1_501321110)`
- `z1 = MULTIPLY(z1, -FIX_0_899976223)`
- `z2 = MULTIPLY(z2, -FIX_2_562915447)`
- `z3 = MULTIPLY(z3, -FIX_1_961570560)`
- `z4 = MULTIPLY(z4, -FIX_0_390180644)`

Then:

- `z3 = z3 + z5`
- `z4 = z4 + z5`

Write column outputs:

- `data[8*7 + c] = DESCALE(tmp4 + z1 + z3, CONST_BITS + PASS1_BITS)`
- `data[8*5 + c] = DESCALE(tmp5 + z2 + z4, CONST_BITS + PASS1_BITS)`
- `data[8*3 + c] = DESCALE(tmp6 + z2 + z3, CONST_BITS + PASS1_BITS)`
- `data[8*1 + c] = DESCALE(tmp7 + z1 + z4, CONST_BITS + PASS1_BITS)`

After pass 2, the `data[]` values are the final outputs. The pass-1 extra scaling has been removed, and the remaining overall factor-of-8 DCT scaling is retained. No additional division by 8, normalization, quantization, or level shift is performed in this block.

Finally, outputs are assigned:

- `out_d0 = data[0]`, `out_d1 = data[1]`, ..., `out_d63 = data[63]`

Each output is driven on a 16-bit port as two's-complement modulo `2^16`.

## Reset Behavior

Reset is synchronous active-high `rst`. On a reset cycle, the block returns to the idle state with no buffered transaction pending.

Architectural state after reset:
- no stored input block
- no stored output block
- no in-flight computation state

Output interface state after reset:
- `out_valid = 0`
- all `out_d0`..`out_d63 = 0`

Input interface state after reset:
- `in_ready = 1`
