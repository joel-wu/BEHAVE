# JPEG Zigzag Scan

This block converts one 8×8 JPEG coefficient block from natural raster order into JPEG zigzag order. The transform is a fixed permutation of 64 coefficient positions defined by the standard JPEG zigzag scan. Each accepted input transaction contains all 64 coefficients of one block, and each output transaction returns the same 64 coefficients reordered by the zigzag scan.

## Interface

### RTL interface

```systemverilog
module compression_jpeg_transform_jpeg_zigzag_scan (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_coeff_0,
    input  logic [15:0]  in_coeff_1,
    input  logic [15:0]  in_coeff_2,
    input  logic [15:0]  in_coeff_3,
    input  logic [15:0]  in_coeff_4,
    input  logic [15:0]  in_coeff_5,
    input  logic [15:0]  in_coeff_6,
    input  logic [15:0]  in_coeff_7,
    input  logic [15:0]  in_coeff_8,
    input  logic [15:0]  in_coeff_9,
    input  logic [15:0]  in_coeff_10,
    input  logic [15:0]  in_coeff_11,
    input  logic [15:0]  in_coeff_12,
    input  logic [15:0]  in_coeff_13,
    input  logic [15:0]  in_coeff_14,
    input  logic [15:0]  in_coeff_15,
    input  logic [15:0]  in_coeff_16,
    input  logic [15:0]  in_coeff_17,
    input  logic [15:0]  in_coeff_18,
    input  logic [15:0]  in_coeff_19,
    input  logic [15:0]  in_coeff_20,
    input  logic [15:0]  in_coeff_21,
    input  logic [15:0]  in_coeff_22,
    input  logic [15:0]  in_coeff_23,
    input  logic [15:0]  in_coeff_24,
    input  logic [15:0]  in_coeff_25,
    input  logic [15:0]  in_coeff_26,
    input  logic [15:0]  in_coeff_27,
    input  logic [15:0]  in_coeff_28,
    input  logic [15:0]  in_coeff_29,
    input  logic [15:0]  in_coeff_30,
    input  logic [15:0]  in_coeff_31,
    input  logic [15:0]  in_coeff_32,
    input  logic [15:0]  in_coeff_33,
    input  logic [15:0]  in_coeff_34,
    input  logic [15:0]  in_coeff_35,
    input  logic [15:0]  in_coeff_36,
    input  logic [15:0]  in_coeff_37,
    input  logic [15:0]  in_coeff_38,
    input  logic [15:0]  in_coeff_39,
    input  logic [15:0]  in_coeff_40,
    input  logic [15:0]  in_coeff_41,
    input  logic [15:0]  in_coeff_42,
    input  logic [15:0]  in_coeff_43,
    input  logic [15:0]  in_coeff_44,
    input  logic [15:0]  in_coeff_45,
    input  logic [15:0]  in_coeff_46,
    input  logic [15:0]  in_coeff_47,
    input  logic [15:0]  in_coeff_48,
    input  logic [15:0]  in_coeff_49,
    input  logic [15:0]  in_coeff_50,
    input  logic [15:0]  in_coeff_51,
    input  logic [15:0]  in_coeff_52,
    input  logic [15:0]  in_coeff_53,
    input  logic [15:0]  in_coeff_54,
    input  logic [15:0]  in_coeff_55,
    input  logic [15:0]  in_coeff_56,
    input  logic [15:0]  in_coeff_57,
    input  logic [15:0]  in_coeff_58,
    input  logic [15:0]  in_coeff_59,
    input  logic [15:0]  in_coeff_60,
    input  logic [15:0]  in_coeff_61,
    input  logic [15:0]  in_coeff_62,
    input  logic [15:0]  in_coeff_63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_coeff_0,
    output logic [15:0]  out_coeff_1,
    output logic [15:0]  out_coeff_2,
    output logic [15:0]  out_coeff_3,
    output logic [15:0]  out_coeff_4,
    output logic [15:0]  out_coeff_5,
    output logic [15:0]  out_coeff_6,
    output logic [15:0]  out_coeff_7,
    output logic [15:0]  out_coeff_8,
    output logic [15:0]  out_coeff_9,
    output logic [15:0]  out_coeff_10,
    output logic [15:0]  out_coeff_11,
    output logic [15:0]  out_coeff_12,
    output logic [15:0]  out_coeff_13,
    output logic [15:0]  out_coeff_14,
    output logic [15:0]  out_coeff_15,
    output logic [15:0]  out_coeff_16,
    output logic [15:0]  out_coeff_17,
    output logic [15:0]  out_coeff_18,
    output logic [15:0]  out_coeff_19,
    output logic [15:0]  out_coeff_20,
    output logic [15:0]  out_coeff_21,
    output logic [15:0]  out_coeff_22,
    output logic [15:0]  out_coeff_23,
    output logic [15:0]  out_coeff_24,
    output logic [15:0]  out_coeff_25,
    output logic [15:0]  out_coeff_26,
    output logic [15:0]  out_coeff_27,
    output logic [15:0]  out_coeff_28,
    output logic [15:0]  out_coeff_29,
    output logic [15:0]  out_coeff_30,
    output logic [15:0]  out_coeff_31,
    output logic [15:0]  out_coeff_32,
    output logic [15:0]  out_coeff_33,
    output logic [15:0]  out_coeff_34,
    output logic [15:0]  out_coeff_35,
    output logic [15:0]  out_coeff_36,
    output logic [15:0]  out_coeff_37,
    output logic [15:0]  out_coeff_38,
    output logic [15:0]  out_coeff_39,
    output logic [15:0]  out_coeff_40,
    output logic [15:0]  out_coeff_41,
    output logic [15:0]  out_coeff_42,
    output logic [15:0]  out_coeff_43,
    output logic [15:0]  out_coeff_44,
    output logic [15:0]  out_coeff_45,
    output logic [15:0]  out_coeff_46,
    output logic [15:0]  out_coeff_47,
    output logic [15:0]  out_coeff_48,
    output logic [15:0]  out_coeff_49,
    output logic [15:0]  out_coeff_50,
    output logic [15:0]  out_coeff_51,
    output logic [15:0]  out_coeff_52,
    output logic [15:0]  out_coeff_53,
    output logic [15:0]  out_coeff_54,
    output logic [15:0]  out_coeff_55,
    output logic [15:0]  out_coeff_56,
    output logic [15:0]  out_coeff_57,
    output logic [15:0]  out_coeff_58,
    output logic [15:0]  out_coeff_59,
    output logic [15:0]  out_coeff_60,
    output logic [15:0]  out_coeff_61,
    output logic [15:0]  out_coeff_62,
    output logic [15:0]  out_coeff_63
);
```

Each `in_coeff_i` and `out_coeff_i` is a 16-bit coefficient bit pattern. The block treats coefficients as opaque 16-bit values and only permutes their positions; it does not interpret signedness and does not modify any bit within a coefficient.

### Behavior interface

```python
class CompressionJpegTransformJpegZigzagScan:
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
        in_coeff_60, in_coeff_61, in_coeff_62, in_coeff_63
    ):
        pass
```

One `process()` call represents one accepted complete input block. Pass each
coefficient as an unsigned 16-bit encoding. Every call returns one dictionary
with exactly the 64 keys `out_coeff_0` through `out_coeff_63`, each an unsigned
16-bit encoding. These fields form one output record, not 64 transactions;
an all-zero block still returns the full dictionary, never `None` or a list.
Calls retain no transform state. Construction and `reset()` establish the
same stateless model; `reset()` returns `None` without emitting a record.

## Modules

- `compression_jpeg_transform_jpeg_zigzag_scan`: top-level zigzag permutation block.

## Functional Behavior

The input block is in natural 8×8 raster order. `in_coeff_0` is row 0 column 0, `in_coeff_1` is row 0 column 1, ..., `in_coeff_7` is row 0 column 7, `in_coeff_8` is row 1 column 0, and `in_coeff_63` is row 7 column 7.

The output block is in JPEG zigzag order. `out_coeff_k` equals the natural-order coefficient located at the natural index `jpeg_natural_order[k]`, for `k` from 0 to 63, using the following fixed table:

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

Equivalently, the outputs are:
- `out_coeff_0  = in_coeff_0`
- `out_coeff_1  = in_coeff_1`
- `out_coeff_2  = in_coeff_8`
- `out_coeff_3  = in_coeff_16`
- `out_coeff_4  = in_coeff_9`
- `out_coeff_5  = in_coeff_2`
- `out_coeff_6  = in_coeff_3`
- `out_coeff_7  = in_coeff_10`
- `out_coeff_8  = in_coeff_17`
- `out_coeff_9  = in_coeff_24`
- `out_coeff_10 = in_coeff_32`
- `out_coeff_11 = in_coeff_25`
- `out_coeff_12 = in_coeff_18`
- `out_coeff_13 = in_coeff_11`
- `out_coeff_14 = in_coeff_4`
- `out_coeff_15 = in_coeff_5`
- `out_coeff_16 = in_coeff_12`
- `out_coeff_17 = in_coeff_19`
- `out_coeff_18 = in_coeff_26`
- `out_coeff_19 = in_coeff_33`
- `out_coeff_20 = in_coeff_40`
- `out_coeff_21 = in_coeff_48`
- `out_coeff_22 = in_coeff_41`
- `out_coeff_23 = in_coeff_34`
- `out_coeff_24 = in_coeff_27`
- `out_coeff_25 = in_coeff_20`
- `out_coeff_26 = in_coeff_13`
- `out_coeff_27 = in_coeff_6`
- `out_coeff_28 = in_coeff_7`
- `out_coeff_29 = in_coeff_14`
- `out_coeff_30 = in_coeff_21`
- `out_coeff_31 = in_coeff_28`
- `out_coeff_32 = in_coeff_35`
- `out_coeff_33 = in_coeff_42`
- `out_coeff_34 = in_coeff_49`
- `out_coeff_35 = in_coeff_56`
- `out_coeff_36 = in_coeff_57`
- `out_coeff_37 = in_coeff_50`
- `out_coeff_38 = in_coeff_43`
- `out_coeff_39 = in_coeff_36`
- `out_coeff_40 = in_coeff_29`
- `out_coeff_41 = in_coeff_22`
- `out_coeff_42 = in_coeff_15`
- `out_coeff_43 = in_coeff_23`
- `out_coeff_44 = in_coeff_30`
- `out_coeff_45 = in_coeff_37`
- `out_coeff_46 = in_coeff_44`
- `out_coeff_47 = in_coeff_51`
- `out_coeff_48 = in_coeff_58`
- `out_coeff_49 = in_coeff_59`
- `out_coeff_50 = in_coeff_52`
- `out_coeff_51 = in_coeff_45`
- `out_coeff_52 = in_coeff_38`
- `out_coeff_53 = in_coeff_31`
- `out_coeff_54 = in_coeff_39`
- `out_coeff_55 = in_coeff_46`
- `out_coeff_56 = in_coeff_53`
- `out_coeff_57 = in_coeff_60`
- `out_coeff_58 = in_coeff_61`
- `out_coeff_59 = in_coeff_54`
- `out_coeff_60 = in_coeff_47`
- `out_coeff_61 = in_coeff_55`
- `out_coeff_62 = in_coeff_62`
- `out_coeff_63 = in_coeff_63`

There is exactly one output transaction for each accepted input transaction.

No arithmetic is performed. No saturation, sign extension, rounding, or clipping is performed. Every output coefficient is the input coefficient from the selected source position with all 16 bits unchanged.

## Reset Behavior

Reset is synchronous and active high.

The block has no architectural state.

During reset, `out_valid` shall be 0. Because the block has no architectural state, all output coefficient ports may be driven to 16'h0000 while no output is being presented. After reset deassertion, the next accepted input transaction is processed according to the permutation defined above.
