# Compression JPEG Transform JPEG IDCT 2D 8x8 Int

This block performs the 8x8 integer inverse discrete cosine transform used by the JPEG slow-but-accurate integer path for 8-bit samples. One accepted input transaction contains one 8x8 block of signed quantized DCT coefficients and one 8x8 block of unsigned quantization multipliers. The block first dequantizes each coefficient, then applies the Loeffler/Ligtenberg/Moschytz 8-point inverse DCT in two 1-D passes: all 8 columns followed by all 8 rows. Arithmetic is fixed-point integer arithmetic with `CONST_BITS = 13` and `PASS1_BITS = 2`. The result is level-shifted by +128 and clamped to the 8-bit sample range [0, 255]. The input/output mapping is exactly 1:1: one accepted input transaction produces exactly one output transaction containing 64 pixels.

## Interface

### RTL interface

```systemverilog
module compression_jpeg_transform_jpeg_idct_2d_8x8_int (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_coef_0,
    input  logic [15:0]  in_coef_1,
    input  logic [15:0]  in_coef_2,
    input  logic [15:0]  in_coef_3,
    input  logic [15:0]  in_coef_4,
    input  logic [15:0]  in_coef_5,
    input  logic [15:0]  in_coef_6,
    input  logic [15:0]  in_coef_7,
    input  logic [15:0]  in_coef_8,
    input  logic [15:0]  in_coef_9,
    input  logic [15:0]  in_coef_10,
    input  logic [15:0]  in_coef_11,
    input  logic [15:0]  in_coef_12,
    input  logic [15:0]  in_coef_13,
    input  logic [15:0]  in_coef_14,
    input  logic [15:0]  in_coef_15,
    input  logic [15:0]  in_coef_16,
    input  logic [15:0]  in_coef_17,
    input  logic [15:0]  in_coef_18,
    input  logic [15:0]  in_coef_19,
    input  logic [15:0]  in_coef_20,
    input  logic [15:0]  in_coef_21,
    input  logic [15:0]  in_coef_22,
    input  logic [15:0]  in_coef_23,
    input  logic [15:0]  in_coef_24,
    input  logic [15:0]  in_coef_25,
    input  logic [15:0]  in_coef_26,
    input  logic [15:0]  in_coef_27,
    input  logic [15:0]  in_coef_28,
    input  logic [15:0]  in_coef_29,
    input  logic [15:0]  in_coef_30,
    input  logic [15:0]  in_coef_31,
    input  logic [15:0]  in_coef_32,
    input  logic [15:0]  in_coef_33,
    input  logic [15:0]  in_coef_34,
    input  logic [15:0]  in_coef_35,
    input  logic [15:0]  in_coef_36,
    input  logic [15:0]  in_coef_37,
    input  logic [15:0]  in_coef_38,
    input  logic [15:0]  in_coef_39,
    input  logic [15:0]  in_coef_40,
    input  logic [15:0]  in_coef_41,
    input  logic [15:0]  in_coef_42,
    input  logic [15:0]  in_coef_43,
    input  logic [15:0]  in_coef_44,
    input  logic [15:0]  in_coef_45,
    input  logic [15:0]  in_coef_46,
    input  logic [15:0]  in_coef_47,
    input  logic [15:0]  in_coef_48,
    input  logic [15:0]  in_coef_49,
    input  logic [15:0]  in_coef_50,
    input  logic [15:0]  in_coef_51,
    input  logic [15:0]  in_coef_52,
    input  logic [15:0]  in_coef_53,
    input  logic [15:0]  in_coef_54,
    input  logic [15:0]  in_coef_55,
    input  logic [15:0]  in_coef_56,
    input  logic [15:0]  in_coef_57,
    input  logic [15:0]  in_coef_58,
    input  logic [15:0]  in_coef_59,
    input  logic [15:0]  in_coef_60,
    input  logic [15:0]  in_coef_61,
    input  logic [15:0]  in_coef_62,
    input  logic [15:0]  in_coef_63,

    input  logic [15:0]  in_quant_0,
    input  logic [15:0]  in_quant_1,
    input  logic [15:0]  in_quant_2,
    input  logic [15:0]  in_quant_3,
    input  logic [15:0]  in_quant_4,
    input  logic [15:0]  in_quant_5,
    input  logic [15:0]  in_quant_6,
    input  logic [15:0]  in_quant_7,
    input  logic [15:0]  in_quant_8,
    input  logic [15:0]  in_quant_9,
    input  logic [15:0]  in_quant_10,
    input  logic [15:0]  in_quant_11,
    input  logic [15:0]  in_quant_12,
    input  logic [15:0]  in_quant_13,
    input  logic [15:0]  in_quant_14,
    input  logic [15:0]  in_quant_15,
    input  logic [15:0]  in_quant_16,
    input  logic [15:0]  in_quant_17,
    input  logic [15:0]  in_quant_18,
    input  logic [15:0]  in_quant_19,
    input  logic [15:0]  in_quant_20,
    input  logic [15:0]  in_quant_21,
    input  logic [15:0]  in_quant_22,
    input  logic [15:0]  in_quant_23,
    input  logic [15:0]  in_quant_24,
    input  logic [15:0]  in_quant_25,
    input  logic [15:0]  in_quant_26,
    input  logic [15:0]  in_quant_27,
    input  logic [15:0]  in_quant_28,
    input  logic [15:0]  in_quant_29,
    input  logic [15:0]  in_quant_30,
    input  logic [15:0]  in_quant_31,
    input  logic [15:0]  in_quant_32,
    input  logic [15:0]  in_quant_33,
    input  logic [15:0]  in_quant_34,
    input  logic [15:0]  in_quant_35,
    input  logic [15:0]  in_quant_36,
    input  logic [15:0]  in_quant_37,
    input  logic [15:0]  in_quant_38,
    input  logic [15:0]  in_quant_39,
    input  logic [15:0]  in_quant_40,
    input  logic [15:0]  in_quant_41,
    input  logic [15:0]  in_quant_42,
    input  logic [15:0]  in_quant_43,
    input  logic [15:0]  in_quant_44,
    input  logic [15:0]  in_quant_45,
    input  logic [15:0]  in_quant_46,
    input  logic [15:0]  in_quant_47,
    input  logic [15:0]  in_quant_48,
    input  logic [15:0]  in_quant_49,
    input  logic [15:0]  in_quant_50,
    input  logic [15:0]  in_quant_51,
    input  logic [15:0]  in_quant_52,
    input  logic [15:0]  in_quant_53,
    input  logic [15:0]  in_quant_54,
    input  logic [15:0]  in_quant_55,
    input  logic [15:0]  in_quant_56,
    input  logic [15:0]  in_quant_57,
    input  logic [15:0]  in_quant_58,
    input  logic [15:0]  in_quant_59,
    input  logic [15:0]  in_quant_60,
    input  logic [15:0]  in_quant_61,
    input  logic [15:0]  in_quant_62,
    input  logic [15:0]  in_quant_63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_pix_0,
    output logic [7:0]   out_pix_1,
    output logic [7:0]   out_pix_2,
    output logic [7:0]   out_pix_3,
    output logic [7:0]   out_pix_4,
    output logic [7:0]   out_pix_5,
    output logic [7:0]   out_pix_6,
    output logic [7:0]   out_pix_7,
    output logic [7:0]   out_pix_8,
    output logic [7:0]   out_pix_9,
    output logic [7:0]   out_pix_10,
    output logic [7:0]   out_pix_11,
    output logic [7:0]   out_pix_12,
    output logic [7:0]   out_pix_13,
    output logic [7:0]   out_pix_14,
    output logic [7:0]   out_pix_15,
    output logic [7:0]   out_pix_16,
    output logic [7:0]   out_pix_17,
    output logic [7:0]   out_pix_18,
    output logic [7:0]   out_pix_19,
    output logic [7:0]   out_pix_20,
    output logic [7:0]   out_pix_21,
    output logic [7:0]   out_pix_22,
    output logic [7:0]   out_pix_23,
    output logic [7:0]   out_pix_24,
    output logic [7:0]   out_pix_25,
    output logic [7:0]   out_pix_26,
    output logic [7:0]   out_pix_27,
    output logic [7:0]   out_pix_28,
    output logic [7:0]   out_pix_29,
    output logic [7:0]   out_pix_30,
    output logic [7:0]   out_pix_31,
    output logic [7:0]   out_pix_32,
    output logic [7:0]   out_pix_33,
    output logic [7:0]   out_pix_34,
    output logic [7:0]   out_pix_35,
    output logic [7:0]   out_pix_36,
    output logic [7:0]   out_pix_37,
    output logic [7:0]   out_pix_38,
    output logic [7:0]   out_pix_39,
    output logic [7:0]   out_pix_40,
    output logic [7:0]   out_pix_41,
    output logic [7:0]   out_pix_42,
    output logic [7:0]   out_pix_43,
    output logic [7:0]   out_pix_44,
    output logic [7:0]   out_pix_45,
    output logic [7:0]   out_pix_46,
    output logic [7:0]   out_pix_47,
    output logic [7:0]   out_pix_48,
    output logic [7:0]   out_pix_49,
    output logic [7:0]   out_pix_50,
    output logic [7:0]   out_pix_51,
    output logic [7:0]   out_pix_52,
    output logic [7:0]   out_pix_53,
    output logic [7:0]   out_pix_54,
    output logic [7:0]   out_pix_55,
    output logic [7:0]   out_pix_56,
    output logic [7:0]   out_pix_57,
    output logic [7:0]   out_pix_58,
    output logic [7:0]   out_pix_59,
    output logic [7:0]   out_pix_60,
    output logic [7:0]   out_pix_61,
    output logic [7:0]   out_pix_62,
    output logic [7:0]   out_pix_63
);
```

### Behavior interface

```python
class CompressionJpegTransformJpegIdct2D8x8Int:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_coef_0, in_coef_1, in_coef_2, in_coef_3, in_coef_4, in_coef_5, in_coef_6, in_coef_7,
        in_coef_8, in_coef_9, in_coef_10, in_coef_11, in_coef_12, in_coef_13, in_coef_14, in_coef_15,
        in_coef_16, in_coef_17, in_coef_18, in_coef_19, in_coef_20, in_coef_21, in_coef_22, in_coef_23,
        in_coef_24, in_coef_25, in_coef_26, in_coef_27, in_coef_28, in_coef_29, in_coef_30, in_coef_31,
        in_coef_32, in_coef_33, in_coef_34, in_coef_35, in_coef_36, in_coef_37, in_coef_38, in_coef_39,
        in_coef_40, in_coef_41, in_coef_42, in_coef_43, in_coef_44, in_coef_45, in_coef_46, in_coef_47,
        in_coef_48, in_coef_49, in_coef_50, in_coef_51, in_coef_52, in_coef_53, in_coef_54, in_coef_55,
        in_coef_56, in_coef_57, in_coef_58, in_coef_59, in_coef_60, in_coef_61, in_coef_62, in_coef_63,
        in_quant_0, in_quant_1, in_quant_2, in_quant_3, in_quant_4, in_quant_5, in_quant_6, in_quant_7,
        in_quant_8, in_quant_9, in_quant_10, in_quant_11, in_quant_12, in_quant_13, in_quant_14, in_quant_15,
        in_quant_16, in_quant_17, in_quant_18, in_quant_19, in_quant_20, in_quant_21, in_quant_22, in_quant_23,
        in_quant_24, in_quant_25, in_quant_26, in_quant_27, in_quant_28, in_quant_29, in_quant_30, in_quant_31,
        in_quant_32, in_quant_33, in_quant_34, in_quant_35, in_quant_36, in_quant_37, in_quant_38, in_quant_39,
        in_quant_40, in_quant_41, in_quant_42, in_quant_43, in_quant_44, in_quant_45, in_quant_46, in_quant_47,
        in_quant_48, in_quant_49, in_quant_50, in_quant_51, in_quant_52, in_quant_53, in_quant_54, in_quant_55,
        in_quant_56, in_quant_57, in_quant_58, in_quant_59, in_quant_60, in_quant_61, in_quant_62, in_quant_63
    ): ...
```

One `process(...)` call represents one accepted block and returns one dictionary
with exactly 64 keys, `out_pix_0` through `out_pix_63`. Each value is an unsigned
8-bit pixel. These are separate fields, not a packed word or a list of output
records. Arguments are 16-bit port words, with signed coefficient and unsigned
quantizer interpretation as defined below. An all-zero dequantized block returns
all 64 pixels equal to 128, not an empty result. Calls do not represent pipeline
cycles. `reset()` returns `None`; no transform history persists between blocks.

## Modules

- `compression_jpeg_transform_jpeg_idct_2d_8x8_int`: top-level 8x8 dequantize + 2-D integer IDCT block.

## Functional Behavior

All `in_coef_*` ports are 16-bit two's-complement signed integers. Each `in_coef_k` must be sign-extended from bit 15 before arithmetic.

All `in_quant_*` ports are 16-bit unsigned integers.

The 64 coefficient and quantization entries are interpreted in raster order over an 8x8 block:
- index `8*r + c` is row `r`, column `c`
- `r` and `c` are integers in `[0, 7]`

For each entry `k`:
- `coef[k] = sign_extend_16(in_coef_k)`
- `quant[k] = zero_extend_16(in_quant_k)`
- `deq[k] = coef[k] * quant[k]`

`deq[k]` is exact signed integer multiplication with no saturation; the arithmetic result is the mathematical product.

The transform uses the following integer constants:

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

`DESCALE(x, n)` is defined as arithmetic right shift with rounding to nearest, ties toward positive infinity:
- `DESCALE(x, n) = (x + 2^(n-1)) >> n`
- `>>` is arithmetic right shift on a signed integer
- `n` is always positive in this design

Worked example:
- `DESCALE(15, 3) = (15 + 4) >> 3 = 19 >> 3 = 2`
- `DESCALE(-15, 3) = (-15 + 4) >> 3 = -11 >> 3 = -2`

The 2-D transform is performed as 1-D IDCT on each column, producing an 8x8 signed workspace, followed by 1-D IDCT on each row of that workspace.

### Pass 1: column transforms

For each column `c` in `[0, 7]`, define the eight signed inputs:

- `x0 = deq[8*0 + c]`
- `x1 = deq[8*1 + c]`
- `x2 = deq[8*2 + c]`
- `x3 = deq[8*3 + c]`
- `x4 = deq[8*4 + c]`
- `x5 = deq[8*5 + c]`
- `x6 = deq[8*6 + c]`
- `x7 = deq[8*7 + c]`

If `x1 == 0 && x2 == 0 && x3 == 0 && x4 == 0 && x5 == 0 && x6 == 0 && x7 == 0`, the entire output column is a replicated DC term:
- `dcval = x0 << PASS1_BITS`
- `workspace[8*r + c] = dcval` for all `r` in `[0, 7]`

Otherwise compute the column outputs with the exact sequence below.

Even part:
- `z2 = x2`
- `z3 = x6`
- `z1 = (z2 + z3) * FIX_0_541196100`
- `tmp2 = z1 + z3 * (-FIX_1_847759065)`
- `tmp3 = z1 + z2 * FIX_0_765366865`

- `z2 = x0`
- `z3 = x4`
- `tmp0 = (z2 + z3) << CONST_BITS`
- `tmp1 = (z2 - z3) << CONST_BITS`

- `tmp10 = tmp0 + tmp3`
- `tmp13 = tmp0 - tmp3`
- `tmp11 = tmp1 + tmp2`
- `tmp12 = tmp1 - tmp2`

Odd part:
- `tmp0 = x7`
- `tmp1 = x5`
- `tmp2 = x3`
- `tmp3 = x1`

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

Final output stage for pass 1:
- `workspace[8*0 + c] = DESCALE(tmp10 + tmp3, CONST_BITS - PASS1_BITS)`
- `workspace[8*7 + c] = DESCALE(tmp10 - tmp3, CONST_BITS - PASS1_BITS)`
- `workspace[8*1 + c] = DESCALE(tmp11 + tmp2, CONST_BITS - PASS1_BITS)`
- `workspace[8*6 + c] = DESCALE(tmp11 - tmp2, CONST_BITS - PASS1_BITS)`
- `workspace[8*2 + c] = DESCALE(tmp12 + tmp1, CONST_BITS - PASS1_BITS)`
- `workspace[8*5 + c] = DESCALE(tmp12 - tmp1, CONST_BITS - PASS1_BITS)`
- `workspace[8*3 + c] = DESCALE(tmp13 + tmp0, CONST_BITS - PASS1_BITS)`
- `workspace[8*4 + c] = DESCALE(tmp13 - tmp0, CONST_BITS - PASS1_BITS)`

### Pass 2: row transforms and sample conversion

For each row `r` in `[0, 7]`, define:
- `w0 = workspace[8*r + 0]`
- `w1 = workspace[8*r + 1]`
- `w2 = workspace[8*r + 2]`
- `w3 = workspace[8*r + 3]`
- `w4 = workspace[8*r + 4]`
- `w5 = workspace[8*r + 5]`
- `w6 = workspace[8*r + 6]`
- `w7 = workspace[8*r + 7]`

If `w1 == 0 && w2 == 0 && w3 == 0 && w4 == 0 && w5 == 0 && w6 == 0 && w7 == 0`, every output sample in the row is:
- `s = DESCALE(w0, PASS1_BITS + 3) + 128`
- `pixel = clamp_u8(s)`
- `out_pix_(8*r + c) = pixel` for all `c` in `[0, 7]`

Otherwise compute the row IDCT with the same Loeffler arithmetic sequence used in pass 1, but using `w0..w7` as inputs and using a different final descale.

Even part:
- `z2 = w2`
- `z3 = w6`
- `z1 = (z2 + z3) * FIX_0_541196100`
- `tmp2 = z1 + z3 * (-FIX_1_847759065)`
- `tmp3 = z1 + z2 * FIX_0_765366865`

- `tmp0 = (w0 + w4) << CONST_BITS`
- `tmp1 = (w0 - w4) << CONST_BITS`

- `tmp10 = tmp0 + tmp3`
- `tmp13 = tmp0 - tmp3`
- `tmp11 = tmp1 + tmp2`
- `tmp12 = tmp1 - tmp2`

Odd part:
- `tmp0 = w7`
- `tmp1 = w5`
- `tmp2 = w3`
- `tmp3 = w1`

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

Final row results before sample conversion:
- `y0 = DESCALE(tmp10 + tmp3, CONST_BITS + PASS1_BITS + 3)`
- `y7 = DESCALE(tmp10 - tmp3, CONST_BITS + PASS1_BITS + 3)`
- `y1 = DESCALE(tmp11 + tmp2, CONST_BITS + PASS1_BITS + 3)`
- `y6 = DESCALE(tmp11 - tmp2, CONST_BITS + PASS1_BITS + 3)`
- `y2 = DESCALE(tmp12 + tmp1, CONST_BITS + PASS1_BITS + 3)`
- `y5 = DESCALE(tmp12 - tmp1, CONST_BITS + PASS1_BITS + 3)`
- `y3 = DESCALE(tmp13 + tmp0, CONST_BITS + PASS1_BITS + 3)`
- `y4 = DESCALE(tmp13 - tmp0, CONST_BITS + PASS1_BITS + 3)`

Each final sample is level-shifted and clamped:
- `pixel = clamp_u8(y + 128)`

`clamp_u8(v)` is:
- `0` if `v < 0`
- `255` if `v > 255`
- otherwise `v`

The output mapping is:
- row 0: `out_pix_0`..`out_pix_7`
- row 1: `out_pix_8`..`out_pix_15`
- row 2: `out_pix_16`..`out_pix_23`
- row 3: `out_pix_24`..`out_pix_31`
- row 4: `out_pix_32`..`out_pix_39`
- row 5: `out_pix_40`..`out_pix_47`
- row 6: `out_pix_48`..`out_pix_55`
- row 7: `out_pix_56`..`out_pix_63`

The output transaction count is exactly one per accepted input transaction.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state is empty; the block is stateless apart from any implementation-specific handshake buffering.

After reset, `out_valid` shall be 0. While no output transaction is being presented, the values on `out_pix_0` through `out_pix_63` are don't-care to functionality; an implementation may drive zeros. `in_ready` may be 1 whenever the block can accept a new transaction.
