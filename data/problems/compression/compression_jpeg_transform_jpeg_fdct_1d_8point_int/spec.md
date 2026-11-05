# JPEG FDCT 1D 8-Point Integer Transform

This block computes one 8-point one-dimensional forward DCT stage using the Loeffler-Ligtenberg-Moschytz 12-multiply integer algorithm with libjpeg-style fixed-point constants and rounding. The block accepts eight signed 16-bit samples, performs the exact row-pass arithmetic used for the 8-bit-sample integer JPEG forward DCT, and returns eight signed 16-bit transform coefficients. The output is the 1-D pass result scaled by `2^PASS1_BITS = 4` and by the algorithm's inherent `sqrt(8)` factor; no final divide-by-8 de-normalization is applied.

## Interface

### RTL interface

```systemverilog
module compression_jpeg_transform_jpeg_fdct_1d_8point_int (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_s0,
    input  logic [15:0]  in_s1,
    input  logic [15:0]  in_s2,
    input  logic [15:0]  in_s3,
    input  logic [15:0]  in_s4,
    input  logic [15:0]  in_s5,
    input  logic [15:0]  in_s6,
    input  logic [15:0]  in_s7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y0,
    output logic [15:0]  out_y1,
    output logic [15:0]  out_y2,
    output logic [15:0]  out_y3,
    output logic [15:0]  out_y4,
    output logic [15:0]  out_y5,
    output logic [15:0]  out_y6,
    output logic [15:0]  out_y7
);
```

All `in_s*` and `out_y*` ports carry 16-bit two's-complement signed integers.

### Behavior interface

```python
class CompressionJpegTransformJpegFdct1d8pointInt:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_s0, in_s1, in_s2, in_s3, in_s4, in_s5, in_s6, in_s7):
        pass
```

Call `process(...)` once per accepted input vector. Each argument is an unsigned
16-bit port encoding, interpreted as signed only for the arithmetic below.
Every call returns one dictionary with exactly the eight keys `out_y0` through
`out_y7`, each containing an unsigned 16-bit result encoding. All-zero inputs
still return all eight fields, not `None` or an empty list.

The model has no persistent transform state. Construction and `reset()` leave
it ready for an independent vector; `reset()` returns `None` and emits no record.

## Modules

- `compression_jpeg_transform_jpeg_fdct_1d_8point_int`: top-level 8-point integer 1-D FDCT block.

## Functional Behavior

Constants are fixed and shall be implemented exactly as signed integers:

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

Each input `in_s*` is interpreted as a signed 16-bit integer. Arithmetic inside the transform is mathematical signed integer arithmetic. There is no saturation. Final outputs are truncated to 16-bit two's-complement bit patterns.

The block implements exactly one 1-D row-pass transform over the eight inputs. Let:

- `x0 = s0`, `x1 = s1`, `x2 = s2`, `x3 = s3`, `x4 = s4`, `x5 = s5`, `x6 = s6`, `x7 = s7`

where each `sN` is the signed interpretation of `in_sN`.

Define the helper operations:

- `MULTIPLY(a, c) = a * c`
- `LEFT_SHIFT(a, n) = a * 2^n`
- `DESCALE(a, n)` is arithmetic right shift with rounding to nearest, ties toward positive infinity, implemented as:
  - if `n = 0`, `DESCALE(a, 0) = a`
  - if `n > 0`, `DESCALE(a, n) = (a + 2^(n-1)) >> n`
  where `>>` is signed arithmetic right shift.

For this block, `DESCALE` is only used with `n = CONST_BITS - PASS1_BITS = 11`.

The transform is computed in this exact order.

1. Butterfly pre-add/subtract:
   - `tmp0 = x0 + x7`
   - `tmp7 = x0 - x7`
   - `tmp1 = x1 + x6`
   - `tmp6 = x1 - x6`
   - `tmp2 = x2 + x5`
   - `tmp5 = x2 - x5`
   - `tmp3 = x3 + x4`
   - `tmp4 = x3 - x4`

2. Even part:
   - `tmp10 = tmp0 + tmp3`
   - `tmp13 = tmp0 - tmp3`
   - `tmp11 = tmp1 + tmp2`
   - `tmp12 = tmp1 - tmp2`
   - `y0 = LEFT_SHIFT(tmp10 + tmp11, PASS1_BITS)`
   - `y4 = LEFT_SHIFT(tmp10 - tmp11, PASS1_BITS)`
   - `z1e = MULTIPLY(tmp12 + tmp13, FIX_0_541196100)`
   - `y2 = DESCALE(z1e + MULTIPLY(tmp13,  FIX_0_765366865), CONST_BITS - PASS1_BITS)`
   - `y6 = DESCALE(z1e + MULTIPLY(tmp12, -FIX_1_847759065), CONST_BITS - PASS1_BITS)`

3. Odd part:
   - `z1 = tmp4 + tmp7`
   - `z2 = tmp5 + tmp6`
   - `z3 = tmp4 + tmp6`
   - `z4 = tmp5 + tmp7`
   - `z5 = MULTIPLY(z3 + z4, FIX_1_175875602)`

   Then:
   - `tmp4m = MULTIPLY(tmp4, FIX_0_298631336)`
   - `tmp5m = MULTIPLY(tmp5, FIX_2_053119869)`
   - `tmp6m = MULTIPLY(tmp6, FIX_3_072711026)`
   - `tmp7m = MULTIPLY(tmp7, FIX_1_501321110)`
   - `z1m = MULTIPLY(z1, -FIX_0_899976223)`
   - `z2m = MULTIPLY(z2, -FIX_2_562915447)`
   - `z3m = MULTIPLY(z3, -FIX_1_961570560) + z5`
   - `z4m = MULTIPLY(z4, -FIX_0_390180644) + z5`

   Finally:
   - `y7 = DESCALE(tmp4m + z1m + z3m, CONST_BITS - PASS1_BITS)`
   - `y5 = DESCALE(tmp5m + z2m + z4m, CONST_BITS - PASS1_BITS)`
   - `y3 = DESCALE(tmp6m + z2m + z3m, CONST_BITS - PASS1_BITS)`
   - `y1 = DESCALE(tmp7m + z1m + z4m, CONST_BITS - PASS1_BITS)`

4. Output mapping:
   - `out_y0 = y0`
   - `out_y1 = y1`
   - `out_y2 = y2`
   - `out_y3 = y3`
   - `out_y4 = y4`
   - `out_y5 = y5`
   - `out_y6 = y6`
   - `out_y7 = y7`

Each `out_y*` port presents the low 16 bits of the corresponding signed result in two's-complement form. Equivalently, after computing each signed integer `yN`, the output bit pattern is `yN mod 2^16`.

Worked `DESCALE` examples for `n = 11`:
- `DESCALE(2048, 11) = (2048 + 1024) >> 11 = 1`
- `DESCALE(-2048, 11) = (-2048 + 1024) >> 11 = -1`
- `DESCALE(-1025, 11) = (-1025 + 1024) >> 11 = -1`

## Reset Behavior

Reset is synchronous active-high `rst`.

The block has no architectural state affecting the transform result. After reset, the block is idle with:
- `in_ready = 1`
- `out_valid = 0`
- `out_y0 = 0`
- `out_y1 = 0`
- `out_y2 = 0`
- `out_y3 = 0`
- `out_y4 = 0`
- `out_y5 = 0`
- `out_y6 = 0`
- `out_y7 = 0`

If the RTL uses internal output holding registers for handshake decoupling, those registers shall reset to zero on all `out_y*` bits, and any internal valid/busy state shall reset to idle / not holding output.
