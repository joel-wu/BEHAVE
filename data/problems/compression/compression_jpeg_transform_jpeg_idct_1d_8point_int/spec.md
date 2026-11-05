# JPEG 1-D 8-Point Integer IDCT

This block performs one one-dimensional 8-point inverse discrete cosine transform used by the JPEG integer IDCT path. Each accepted input transaction provides eight signed DCT coefficients and eight unsigned dequantization factors for a single 1-D lane. The block first dequantizes each coefficient by integer multiplication, then applies the fixed Loeffler/Ligtenberg/Moschytz 8-point integer IDCT kernel used by the JPEG islow implementation. The computation uses 13-bit scaled constants, a first-pass output scaling of 2 bits, and rounded arithmetic right shifts exactly as specified below. One input transaction produces one output transaction containing the eight transformed signed samples of the first IDCT pass.

## Interface

### RTL interface

```systemverilog
module compression_jpeg_transform_jpeg_idct_1d_8point_int (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_coef0,
    input  logic [15:0]  in_coef1,
    input  logic [15:0]  in_coef2,
    input  logic [15:0]  in_coef3,
    input  logic [15:0]  in_coef4,
    input  logic [15:0]  in_coef5,
    input  logic [15:0]  in_coef6,
    input  logic [15:0]  in_coef7,
    input  logic [15:0]  in_quant0,
    input  logic [15:0]  in_quant1,
    input  logic [15:0]  in_quant2,
    input  logic [15:0]  in_quant3,
    input  logic [15:0]  in_quant4,
    input  logic [15:0]  in_quant5,
    input  logic [15:0]  in_quant6,
    input  logic [15:0]  in_quant7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_s0,
    output logic [15:0]  out_s1,
    output logic [15:0]  out_s2,
    output logic [15:0]  out_s3,
    output logic [15:0]  out_s4,
    output logic [15:0]  out_s5,
    output logic [15:0]  out_s6,
    output logic [15:0]  out_s7
);
```

Input coefficient ports `in_coef*` carry signed 16-bit two's-complement values. Input quantization ports `in_quant*` carry unsigned 16-bit values. Output ports `out_s*` carry signed 16-bit two's-complement values.

### Behavior interface

```python
class CompressionJpegTransformJpegIdct1d8pointInt:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_coef0, in_coef1, in_coef2, in_coef3,
        in_coef4, in_coef5, in_coef6, in_coef7,
        in_quant0, in_quant1, in_quant2, in_quant3,
        in_quant4, in_quant5, in_quant6, in_quant7
    ): ...
```

Each `process(...)` call consumes one accepted set of coefficients and
quantizers and returns one dictionary with exactly the keys `out_s0`,
`out_s1`, `out_s2`, `out_s3`, `out_s4`, `out_s5`, `out_s6`, and `out_s7`.
Arguments and results are unsigned 16-bit port bit patterns; coefficients and
outputs have the signed interpretation stated above, while quantizers are
unsigned. DC-only and all-zero inputs also return all eight fields, not an
empty record, `None`, or a list.

No algorithmic state persists between calls. `reset()` returns `None` and emits
no output record. Calls represent accepted transactions, not clock cycles or
cycles spent waiting for output consumption.

## Modules

- `compression_jpeg_transform_jpeg_idct_1d_8point_int`: top-level 1-D 8-point integer IDCT block.

## Functional Behavior

Constants:

- `CONST_BITS = 13`
- `PASS1_BITS = 2`

Scaled integer constants:

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

Arithmetic definitions:

- Interpret each `in_coefk` as signed 16-bit.
- Interpret each `in_quantk` as unsigned 16-bit.
- Dequantization is `dqk = coefk * quantk` in signed integer arithmetic.
- `LEFT_SHIFT(x, n) = x * 2^n`.
- `DESCALE(x, n)` is rounded arithmetic right shift:
  - if `n = 0`, `DESCALE(x, 0) = x`
  - if `n > 0`, `DESCALE(x, n) = (x + 2^(n-1)) >> n`
  where `>>` is arithmetic right shift on a signed integer.
- All additions, subtractions, and multiplications are mathematically exact integer operations. No intermediate truncation is applied before the final conversion of each output to 16-bit two's-complement.

The 1-D transform consumes the eight dequantized inputs `dq0`..`dq7` and computes outputs `s0`..`s7` as follows.

Special case, all AC terms zero:

- If `coef1 = 0`, `coef2 = 0`, `coef3 = 0`, `coef4 = 0`, `coef5 = 0`, `coef6 = 0`, and `coef7 = 0`, then:
  - `dcval = LEFT_SHIFT(dq0, PASS1_BITS)`
  - `s0 = s1 = s2 = s3 = s4 = s5 = s6 = s7 = dcval`

Otherwise compute the full kernel below.

Even part:

- `z2 = dq2`
- `z3 = dq6`
- `z1 = (z2 + z3) * FIX_0_541196100`
- `tmp2 = z1 + z3 * (-FIX_1_847759065)`
- `tmp3 = z1 + z2 * FIX_0_765366865`

- `z2 = dq0`
- `z3 = dq4`
- `tmp0 = LEFT_SHIFT(z2 + z3, CONST_BITS)`
- `tmp1 = LEFT_SHIFT(z2 - z3, CONST_BITS)`

- `tmp10 = tmp0 + tmp3`
- `tmp13 = tmp0 - tmp3`
- `tmp11 = tmp1 + tmp2`
- `tmp12 = tmp1 - tmp2`

Odd part:

- `tmp0 = dq7`
- `tmp1 = dq5`
- `tmp2 = dq3`
- `tmp3 = dq1`

- `z1 = tmp0 + tmp3`
- `z2 = tmp1 + tmp2`
- `z3 = tmp0 + tmp2`
- `z4 = tmp1 + tmp3`
- `z5 = (z3 + z4) * FIX_1_175875602`

- `tmp0 = tmp0 * FIX_0_298631336`
- `tmp1 = tmp1 * FIX_2_053119869`
- `tmp2 = tmp2 * FIX_3_072711026`
- `tmp3 = tmp3 * FIX_1_501321110`
- `z1 = z1 * (-FIX_0_899976223)`
- `z2 = z2 * (-FIX_2_562915447)`
- `z3 = z3 * (-FIX_1_961570560)`
- `z4 = z4 * (-FIX_0_390180644)`

- `z3 = z3 + z5`
- `z4 = z4 + z5`

- `tmp0 = tmp0 + z1 + z3`
- `tmp1 = tmp1 + z2 + z4`
- `tmp2 = tmp2 + z2 + z3`
- `tmp3 = tmp3 + z1 + z4`

Final output stage:

- `s0 = DESCALE(tmp10 + tmp3, CONST_BITS - PASS1_BITS)`
- `s7 = DESCALE(tmp10 - tmp3, CONST_BITS - PASS1_BITS)`
- `s1 = DESCALE(tmp11 + tmp2, CONST_BITS - PASS1_BITS)`
- `s6 = DESCALE(tmp11 - tmp2, CONST_BITS - PASS1_BITS)`
- `s2 = DESCALE(tmp12 + tmp1, CONST_BITS - PASS1_BITS)`
- `s5 = DESCALE(tmp12 - tmp1, CONST_BITS - PASS1_BITS)`
- `s3 = DESCALE(tmp13 + tmp0, CONST_BITS - PASS1_BITS)`
- `s4 = DESCALE(tmp13 - tmp0, CONST_BITS - PASS1_BITS)`

Output encoding:

- Each `sk` is converted to a 16-bit two's-complement output bit pattern on `out_sk`.
- If `sk` is outside the signed 16-bit range, the output wraps modulo `2^16`.

This block implements only the first-pass 1-D kernel. It does not perform the second row pass, final 8-bit sample range limiting, or clipping to `[0,255]`.

## Reset Behavior

Reset is synchronous active-high `rst`.

There is no architectural state. After reset, the block is idle and may accept a new input transaction immediately. Since the block is stateless, reset does not alter future input/output mappings.

While no output transaction is being presented, `out_valid` is low. Output data values are not architecturally significant when `out_valid` is low. `in_ready` is high whenever the implementation is able to accept a transaction; a combinational 1:1 implementation may keep `in_ready` high continuously.
