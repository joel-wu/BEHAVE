# JPEG Inverse Zigzag Scan

This module converts one 8x8 JPEG coefficient block from zigzag order into natural raster order. Each accepted input transaction supplies 64 signed coefficients indexed by JPEG zigzag position 0 through 63. The module produces one output transaction containing the same 64 coefficients rearranged into natural order using the fixed JPEG natural-order mapping defined for an 8x8 DCT block.

## Interface

### RTL interface

```systemverilog
module compression_jpeg_transform_jpeg_inverse_zigzag_scan (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [15:0] in_coeff_0,
    input  logic signed [15:0] in_coeff_1,
    input  logic signed [15:0] in_coeff_2,
    input  logic signed [15:0] in_coeff_3,
    input  logic signed [15:0] in_coeff_4,
    input  logic signed [15:0] in_coeff_5,
    input  logic signed [15:0] in_coeff_6,
    input  logic signed [15:0] in_coeff_7,
    input  logic signed [15:0] in_coeff_8,
    input  logic signed [15:0] in_coeff_9,
    input  logic signed [15:0] in_coeff_10,
    input  logic signed [15:0] in_coeff_11,
    input  logic signed [15:0] in_coeff_12,
    input  logic signed [15:0] in_coeff_13,
    input  logic signed [15:0] in_coeff_14,
    input  logic signed [15:0] in_coeff_15,
    input  logic signed [15:0] in_coeff_16,
    input  logic signed [15:0] in_coeff_17,
    input  logic signed [15:0] in_coeff_18,
    input  logic signed [15:0] in_coeff_19,
    input  logic signed [15:0] in_coeff_20,
    input  logic signed [15:0] in_coeff_21,
    input  logic signed [15:0] in_coeff_22,
    input  logic signed [15:0] in_coeff_23,
    input  logic signed [15:0] in_coeff_24,
    input  logic signed [15:0] in_coeff_25,
    input  logic signed [15:0] in_coeff_26,
    input  logic signed [15:0] in_coeff_27,
    input  logic signed [15:0] in_coeff_28,
    input  logic signed [15:0] in_coeff_29,
    input  logic signed [15:0] in_coeff_30,
    input  logic signed [15:0] in_coeff_31,
    input  logic signed [15:0] in_coeff_32,
    input  logic signed [15:0] in_coeff_33,
    input  logic signed [15:0] in_coeff_34,
    input  logic signed [15:0] in_coeff_35,
    input  logic signed [15:0] in_coeff_36,
    input  logic signed [15:0] in_coeff_37,
    input  logic signed [15:0] in_coeff_38,
    input  logic signed [15:0] in_coeff_39,
    input  logic signed [15:0] in_coeff_40,
    input  logic signed [15:0] in_coeff_41,
    input  logic signed [15:0] in_coeff_42,
    input  logic signed [15:0] in_coeff_43,
    input  logic signed [15:0] in_coeff_44,
    input  logic signed [15:0] in_coeff_45,
    input  logic signed [15:0] in_coeff_46,
    input  logic signed [15:0] in_coeff_47,
    input  logic signed [15:0] in_coeff_48,
    input  logic signed [15:0] in_coeff_49,
    input  logic signed [15:0] in_coeff_50,
    input  logic signed [15:0] in_coeff_51,
    input  logic signed [15:0] in_coeff_52,
    input  logic signed [15:0] in_coeff_53,
    input  logic signed [15:0] in_coeff_54,
    input  logic signed [15:0] in_coeff_55,
    input  logic signed [15:0] in_coeff_56,
    input  logic signed [15:0] in_coeff_57,
    input  logic signed [15:0] in_coeff_58,
    input  logic signed [15:0] in_coeff_59,
    input  logic signed [15:0] in_coeff_60,
    input  logic signed [15:0] in_coeff_61,
    input  logic signed [15:0] in_coeff_62,
    input  logic signed [15:0] in_coeff_63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [15:0] out_coeff_0,
    output logic signed [15:0] out_coeff_1,
    output logic signed [15:0] out_coeff_2,
    output logic signed [15:0] out_coeff_3,
    output logic signed [15:0] out_coeff_4,
    output logic signed [15:0] out_coeff_5,
    output logic signed [15:0] out_coeff_6,
    output logic signed [15:0] out_coeff_7,
    output logic signed [15:0] out_coeff_8,
    output logic signed [15:0] out_coeff_9,
    output logic signed [15:0] out_coeff_10,
    output logic signed [15:0] out_coeff_11,
    output logic signed [15:0] out_coeff_12,
    output logic signed [15:0] out_coeff_13,
    output logic signed [15:0] out_coeff_14,
    output logic signed [15:0] out_coeff_15,
    output logic signed [15:0] out_coeff_16,
    output logic signed [15:0] out_coeff_17,
    output logic signed [15:0] out_coeff_18,
    output logic signed [15:0] out_coeff_19,
    output logic signed [15:0] out_coeff_20,
    output logic signed [15:0] out_coeff_21,
    output logic signed [15:0] out_coeff_22,
    output logic signed [15:0] out_coeff_23,
    output logic signed [15:0] out_coeff_24,
    output logic signed [15:0] out_coeff_25,
    output logic signed [15:0] out_coeff_26,
    output logic signed [15:0] out_coeff_27,
    output logic signed [15:0] out_coeff_28,
    output logic signed [15:0] out_coeff_29,
    output logic signed [15:0] out_coeff_30,
    output logic signed [15:0] out_coeff_31,
    output logic signed [15:0] out_coeff_32,
    output logic signed [15:0] out_coeff_33,
    output logic signed [15:0] out_coeff_34,
    output logic signed [15:0] out_coeff_35,
    output logic signed [15:0] out_coeff_36,
    output logic signed [15:0] out_coeff_37,
    output logic signed [15:0] out_coeff_38,
    output logic signed [15:0] out_coeff_39,
    output logic signed [15:0] out_coeff_40,
    output logic signed [15:0] out_coeff_41,
    output logic signed [15:0] out_coeff_42,
    output logic signed [15:0] out_coeff_43,
    output logic signed [15:0] out_coeff_44,
    output logic signed [15:0] out_coeff_45,
    output logic signed [15:0] out_coeff_46,
    output logic signed [15:0] out_coeff_47,
    output logic signed [15:0] out_coeff_48,
    output logic signed [15:0] out_coeff_49,
    output logic signed [15:0] out_coeff_50,
    output logic signed [15:0] out_coeff_51,
    output logic signed [15:0] out_coeff_52,
    output logic signed [15:0] out_coeff_53,
    output logic signed [15:0] out_coeff_54,
    output logic signed [15:0] out_coeff_55,
    output logic signed [15:0] out_coeff_56,
    output logic signed [15:0] out_coeff_57,
    output logic signed [15:0] out_coeff_58,
    output logic signed [15:0] out_coeff_59,
    output logic signed [15:0] out_coeff_60,
    output logic signed [15:0] out_coeff_61,
    output logic signed [15:0] out_coeff_62,
    output logic signed [15:0] out_coeff_63
);
```

### Behavior interface

```python
class CompressionJpegTransformJpegInverseZigzagScan:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_coeff_0, in_coeff_1, in_coeff_2, in_coeff_3,
            in_coeff_4, in_coeff_5, in_coeff_6, in_coeff_7,
            in_coeff_8, in_coeff_9, in_coeff_10, in_coeff_11,
            in_coeff_12, in_coeff_13, in_coeff_14, in_coeff_15,
            in_coeff_16, in_coeff_17, in_coeff_18, in_coeff_19,
            in_coeff_20, in_coeff_21, in_coeff_22, in_coeff_23,
            in_coeff_24, in_coeff_25, in_coeff_26, in_coeff_27,
            in_coeff_28, in_coeff_29, in_coeff_30, in_coeff_31,
            in_coeff_32, in_coeff_33, in_coeff_34, in_coeff_35,
            in_coeff_36, in_coeff_37, in_coeff_38, in_coeff_39,
            in_coeff_40, in_coeff_41, in_coeff_42, in_coeff_43,
            in_coeff_44, in_coeff_45, in_coeff_46, in_coeff_47,
            in_coeff_48, in_coeff_49, in_coeff_50, in_coeff_51,
            in_coeff_52, in_coeff_53, in_coeff_54, in_coeff_55,
            in_coeff_56, in_coeff_57, in_coeff_58, in_coeff_59,
            in_coeff_60, in_coeff_61, in_coeff_62, in_coeff_63):
        pass
```

Each call represents one accepted 8x8 block and returns exactly one dictionary
with all 64 keys `out_coeff_0` through `out_coeff_63`. Arguments and returned
values are unsigned 16-bit port words encoding the signed coefficients. The
return is neither a list nor 64 separate responses; an all-zero block still
returns all 64 keys. Calls do not represent waiting clock cycles. There is no
cross-block state; `reset()` returns `None` and emits no response.

## Modules

- `compression_jpeg_transform_jpeg_inverse_zigzag_scan`: top-level inverse-zigzag block for one 8x8 coefficient block.

## Functional Behavior

The module is a 1:1 transform. One accepted input transaction produces exactly one output transaction.

Each `in_coeff_k` is a 16-bit two's-complement signed integer bit pattern. The module does not modify coefficient values; it only permutes their positions. Each `out_coeff_n` is returned as a 16-bit two's-complement signed integer bit pattern.

Input coefficients are interpreted as a 64-element vector in JPEG zigzag order:
- `in_coeff_0` is zigzag index 0.
- `in_coeff_1` is zigzag index 1.
- ...
- `in_coeff_63` is zigzag index 63.

Output coefficients are interpreted as a 64-element vector in natural order for an 8x8 block, where index `n` corresponds to raster position `row = n >> 3`, `col = n & 7`.

The output mapping is defined by the fixed JPEG natural-order table. For each zigzag index `k`, place `in_coeff_k` into output position `jpeg_natural_order[k]`. The 64-entry mapping is:

- `jpeg_natural_order[0]  = 0`
- `jpeg_natural_order[1]  = 1`
- `jpeg_natural_order[2]  = 8`
- `jpeg_natural_order[3]  = 16`
- `jpeg_natural_order[4]  = 9`
- `jpeg_natural_order[5]  = 2`
- `jpeg_natural_order[6]  = 3`
- `jpeg_natural_order[7]  = 10`
- `jpeg_natural_order[8]  = 17`
- `jpeg_natural_order[9]  = 24`
- `jpeg_natural_order[10] = 32`
- `jpeg_natural_order[11] = 25`
- `jpeg_natural_order[12] = 18`
- `jpeg_natural_order[13] = 11`
- `jpeg_natural_order[14] = 4`
- `jpeg_natural_order[15] = 5`
- `jpeg_natural_order[16] = 12`
- `jpeg_natural_order[17] = 19`
- `jpeg_natural_order[18] = 26`
- `jpeg_natural_order[19] = 33`
- `jpeg_natural_order[20] = 40`
- `jpeg_natural_order[21] = 48`
- `jpeg_natural_order[22] = 41`
- `jpeg_natural_order[23] = 34`
- `jpeg_natural_order[24] = 27`
- `jpeg_natural_order[25] = 20`
- `jpeg_natural_order[26] = 13`
- `jpeg_natural_order[27] = 6`
- `jpeg_natural_order[28] = 7`
- `jpeg_natural_order[29] = 14`
- `jpeg_natural_order[30] = 21`
- `jpeg_natural_order[31] = 28`
- `jpeg_natural_order[32] = 35`
- `jpeg_natural_order[33] = 42`
- `jpeg_natural_order[34] = 49`
- `jpeg_natural_order[35] = 56`
- `jpeg_natural_order[36] = 57`
- `jpeg_natural_order[37] = 50`
- `jpeg_natural_order[38] = 43`
- `jpeg_natural_order[39] = 36`
- `jpeg_natural_order[40] = 29`
- `jpeg_natural_order[41] = 22`
- `jpeg_natural_order[42] = 15`
- `jpeg_natural_order[43] = 23`
- `jpeg_natural_order[44] = 30`
- `jpeg_natural_order[45] = 37`
- `jpeg_natural_order[46] = 44`
- `jpeg_natural_order[47] = 51`
- `jpeg_natural_order[48] = 58`
- `jpeg_natural_order[49] = 59`
- `jpeg_natural_order[50] = 52`
- `jpeg_natural_order[51] = 45`
- `jpeg_natural_order[52] = 38`
- `jpeg_natural_order[53] = 31`
- `jpeg_natural_order[54] = 39`
- `jpeg_natural_order[55] = 46`
- `jpeg_natural_order[56] = 53`
- `jpeg_natural_order[57] = 60`
- `jpeg_natural_order[58] = 61`
- `jpeg_natural_order[59] = 54`
- `jpeg_natural_order[60] = 47`
- `jpeg_natural_order[61] = 55`
- `jpeg_natural_order[62] = 62`
- `jpeg_natural_order[63] = 63`

Equivalently, the natural-order outputs are:

- `out_coeff_0  = in_coeff_0`
- `out_coeff_1  = in_coeff_1`
- `out_coeff_2  = in_coeff_5`
- `out_coeff_3  = in_coeff_6`
- `out_coeff_4  = in_coeff_14`
- `out_coeff_5  = in_coeff_15`
- `out_coeff_6  = in_coeff_27`
- `out_coeff_7  = in_coeff_28`
- `out_coeff_8  = in_coeff_2`
- `out_coeff_9  = in_coeff_4`
- `out_coeff_10 = in_coeff_7`
- `out_coeff_11 = in_coeff_13`
- `out_coeff_12 = in_coeff_16`
- `out_coeff_13 = in_coeff_26`
- `out_coeff_14 = in_coeff_29`
- `out_coeff_15 = in_coeff_42`
- `out_coeff_16 = in_coeff_3`
- `out_coeff_17 = in_coeff_8`
- `out_coeff_18 = in_coeff_12`
- `out_coeff_19 = in_coeff_17`
- `out_coeff_20 = in_coeff_25`
- `out_coeff_21 = in_coeff_30`
- `out_coeff_22 = in_coeff_41`
- `out_coeff_23 = in_coeff_43`
- `out_coeff_24 = in_coeff_9`
- `out_coeff_25 = in_coeff_11`
- `out_coeff_26 = in_coeff_18`
- `out_coeff_27 = in_coeff_24`
- `out_coeff_28 = in_coeff_31`
- `out_coeff_29 = in_coeff_40`
- `out_coeff_30 = in_coeff_44`
- `out_coeff_31 = in_coeff_53`
- `out_coeff_32 = in_coeff_10`
- `out_coeff_33 = in_coeff_19`
- `out_coeff_34 = in_coeff_23`
- `out_coeff_35 = in_coeff_32`
- `out_coeff_36 = in_coeff_39`
- `out_coeff_37 = in_coeff_45`
- `out_coeff_38 = in_coeff_52`
- `out_coeff_39 = in_coeff_54`
- `out_coeff_40 = in_coeff_20`
- `out_coeff_41 = in_coeff_22`
- `out_coeff_42 = in_coeff_33`
- `out_coeff_43 = in_coeff_38`
- `out_coeff_44 = in_coeff_46`
- `out_coeff_45 = in_coeff_51`
- `out_coeff_46 = in_coeff_55`
- `out_coeff_47 = in_coeff_60`
- `out_coeff_48 = in_coeff_21`
- `out_coeff_49 = in_coeff_34`
- `out_coeff_50 = in_coeff_37`
- `out_coeff_51 = in_coeff_47`
- `out_coeff_52 = in_coeff_50`
- `out_coeff_53 = in_coeff_56`
- `out_coeff_54 = in_coeff_59`
- `out_coeff_55 = in_coeff_61`
- `out_coeff_56 = in_coeff_35`
- `out_coeff_57 = in_coeff_36`
- `out_coeff_58 = in_coeff_48`
- `out_coeff_59 = in_coeff_49`
- `out_coeff_60 = in_coeff_57`
- `out_coeff_61 = in_coeff_58`
- `out_coeff_62 = in_coeff_62`
- `out_coeff_63 = in_coeff_63`

No saturation, rounding, clipping, sign conversion, or arithmetic is performed.

The 16 extra `63` entries that exist in the 80-entry JPEG decoder safety table are not part of this module's input domain and are not used. This module consumes exactly 64 coefficients and produces exactly 64 coefficients.

## Reset Behavior

Reset is active-high and synchronous to `clk`.

The block has no architectural state.

After reset, `out_valid` shall be `0`. Output coefficient ports may be driven to `0` when no valid output is being presented. `in_ready` may be `1` immediately after reset deassertion.
