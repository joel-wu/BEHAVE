# JPEG Dequantize Block Int16

This module dequantizes one 8×8 JPEG coefficient block. Each accepted input transaction supplies 64 signed 16-bit quantized DCT coefficients and the corresponding 64 unsigned 16-bit quantization-table entries in natural coefficient order. The module multiplies each coefficient by its quantizer entry independently and returns the 64 signed 16-bit dequantized coefficients. Arithmetic is integer-only and elementwise; there is no zigzag reordering, no block smoothing, no IDCT, and no cross-coefficient interaction.

## Interface

### RTL interface

```systemverilog
module compression_jpeg_quant_jpeg_dequantize_block_int16 (
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
    input  logic [15:0]  in_qtbl_0,
    input  logic [15:0]  in_qtbl_1,
    input  logic [15:0]  in_qtbl_2,
    input  logic [15:0]  in_qtbl_3,
    input  logic [15:0]  in_qtbl_4,
    input  logic [15:0]  in_qtbl_5,
    input  logic [15:0]  in_qtbl_6,
    input  logic [15:0]  in_qtbl_7,
    input  logic [15:0]  in_qtbl_8,
    input  logic [15:0]  in_qtbl_9,
    input  logic [15:0]  in_qtbl_10,
    input  logic [15:0]  in_qtbl_11,
    input  logic [15:0]  in_qtbl_12,
    input  logic [15:0]  in_qtbl_13,
    input  logic [15:0]  in_qtbl_14,
    input  logic [15:0]  in_qtbl_15,
    input  logic [15:0]  in_qtbl_16,
    input  logic [15:0]  in_qtbl_17,
    input  logic [15:0]  in_qtbl_18,
    input  logic [15:0]  in_qtbl_19,
    input  logic [15:0]  in_qtbl_20,
    input  logic [15:0]  in_qtbl_21,
    input  logic [15:0]  in_qtbl_22,
    input  logic [15:0]  in_qtbl_23,
    input  logic [15:0]  in_qtbl_24,
    input  logic [15:0]  in_qtbl_25,
    input  logic [15:0]  in_qtbl_26,
    input  logic [15:0]  in_qtbl_27,
    input  logic [15:0]  in_qtbl_28,
    input  logic [15:0]  in_qtbl_29,
    input  logic [15:0]  in_qtbl_30,
    input  logic [15:0]  in_qtbl_31,
    input  logic [15:0]  in_qtbl_32,
    input  logic [15:0]  in_qtbl_33,
    input  logic [15:0]  in_qtbl_34,
    input  logic [15:0]  in_qtbl_35,
    input  logic [15:0]  in_qtbl_36,
    input  logic [15:0]  in_qtbl_37,
    input  logic [15:0]  in_qtbl_38,
    input  logic [15:0]  in_qtbl_39,
    input  logic [15:0]  in_qtbl_40,
    input  logic [15:0]  in_qtbl_41,
    input  logic [15:0]  in_qtbl_42,
    input  logic [15:0]  in_qtbl_43,
    input  logic [15:0]  in_qtbl_44,
    input  logic [15:0]  in_qtbl_45,
    input  logic [15:0]  in_qtbl_46,
    input  logic [15:0]  in_qtbl_47,
    input  logic [15:0]  in_qtbl_48,
    input  logic [15:0]  in_qtbl_49,
    input  logic [15:0]  in_qtbl_50,
    input  logic [15:0]  in_qtbl_51,
    input  logic [15:0]  in_qtbl_52,
    input  logic [15:0]  in_qtbl_53,
    input  logic [15:0]  in_qtbl_54,
    input  logic [15:0]  in_qtbl_55,
    input  logic [15:0]  in_qtbl_56,
    input  logic [15:0]  in_qtbl_57,
    input  logic [15:0]  in_qtbl_58,
    input  logic [15:0]  in_qtbl_59,
    input  logic [15:0]  in_qtbl_60,
    input  logic [15:0]  in_qtbl_61,
    input  logic [15:0]  in_qtbl_62,
    input  logic [15:0]  in_qtbl_63,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_coef_0,
    output logic [15:0]  out_coef_1,
    output logic [15:0]  out_coef_2,
    output logic [15:0]  out_coef_3,
    output logic [15:0]  out_coef_4,
    output logic [15:0]  out_coef_5,
    output logic [15:0]  out_coef_6,
    output logic [15:0]  out_coef_7,
    output logic [15:0]  out_coef_8,
    output logic [15:0]  out_coef_9,
    output logic [15:0]  out_coef_10,
    output logic [15:0]  out_coef_11,
    output logic [15:0]  out_coef_12,
    output logic [15:0]  out_coef_13,
    output logic [15:0]  out_coef_14,
    output logic [15:0]  out_coef_15,
    output logic [15:0]  out_coef_16,
    output logic [15:0]  out_coef_17,
    output logic [15:0]  out_coef_18,
    output logic [15:0]  out_coef_19,
    output logic [15:0]  out_coef_20,
    output logic [15:0]  out_coef_21,
    output logic [15:0]  out_coef_22,
    output logic [15:0]  out_coef_23,
    output logic [15:0]  out_coef_24,
    output logic [15:0]  out_coef_25,
    output logic [15:0]  out_coef_26,
    output logic [15:0]  out_coef_27,
    output logic [15:0]  out_coef_28,
    output logic [15:0]  out_coef_29,
    output logic [15:0]  out_coef_30,
    output logic [15:0]  out_coef_31,
    output logic [15:0]  out_coef_32,
    output logic [15:0]  out_coef_33,
    output logic [15:0]  out_coef_34,
    output logic [15:0]  out_coef_35,
    output logic [15:0]  out_coef_36,
    output logic [15:0]  out_coef_37,
    output logic [15:0]  out_coef_38,
    output logic [15:0]  out_coef_39,
    output logic [15:0]  out_coef_40,
    output logic [15:0]  out_coef_41,
    output logic [15:0]  out_coef_42,
    output logic [15:0]  out_coef_43,
    output logic [15:0]  out_coef_44,
    output logic [15:0]  out_coef_45,
    output logic [15:0]  out_coef_46,
    output logic [15:0]  out_coef_47,
    output logic [15:0]  out_coef_48,
    output logic [15:0]  out_coef_49,
    output logic [15:0]  out_coef_50,
    output logic [15:0]  out_coef_51,
    output logic [15:0]  out_coef_52,
    output logic [15:0]  out_coef_53,
    output logic [15:0]  out_coef_54,
    output logic [15:0]  out_coef_55,
    output logic [15:0]  out_coef_56,
    output logic [15:0]  out_coef_57,
    output logic [15:0]  out_coef_58,
    output logic [15:0]  out_coef_59,
    output logic [15:0]  out_coef_60,
    output logic [15:0]  out_coef_61,
    output logic [15:0]  out_coef_62,
    output logic [15:0]  out_coef_63
);
```

### Behavior interface

```python
class CompressionJpegQuantJpegDequantizeBlockInt16:
    def __init__(self):
        pass

    def reset(self):
        pass

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
        in_qtbl_0, in_qtbl_1, in_qtbl_2, in_qtbl_3, in_qtbl_4, in_qtbl_5, in_qtbl_6, in_qtbl_7,
        in_qtbl_8, in_qtbl_9, in_qtbl_10, in_qtbl_11, in_qtbl_12, in_qtbl_13, in_qtbl_14, in_qtbl_15,
        in_qtbl_16, in_qtbl_17, in_qtbl_18, in_qtbl_19, in_qtbl_20, in_qtbl_21, in_qtbl_22, in_qtbl_23,
        in_qtbl_24, in_qtbl_25, in_qtbl_26, in_qtbl_27, in_qtbl_28, in_qtbl_29, in_qtbl_30, in_qtbl_31,
        in_qtbl_32, in_qtbl_33, in_qtbl_34, in_qtbl_35, in_qtbl_36, in_qtbl_37, in_qtbl_38, in_qtbl_39,
        in_qtbl_40, in_qtbl_41, in_qtbl_42, in_qtbl_43, in_qtbl_44, in_qtbl_45, in_qtbl_46, in_qtbl_47,
        in_qtbl_48, in_qtbl_49, in_qtbl_50, in_qtbl_51, in_qtbl_52, in_qtbl_53, in_qtbl_54, in_qtbl_55,
        in_qtbl_56, in_qtbl_57, in_qtbl_58, in_qtbl_59, in_qtbl_60, in_qtbl_61, in_qtbl_62, in_qtbl_63
    ):
        pass
```

Each `process` call represents one accepted 8x8 block. Pass all 128 arguments
as unsigned 16-bit integers: coefficient arguments encode signed values in
two's complement, while quantizers are unsigned. It always returns one
dictionary with exactly the keys `out_coef_0` through `out_coef_63`, each
holding an integer in 0..65535 encoding the wrapped signed coefficient.
All 64 keys are present even for zero coefficients or quantizers; do not
return a list, a packed vector, or `None`. There is no state between blocks.
Construction and `reset()` have no numerical effect; `reset()` returns `None`.
Physical latency, idle and stall cycles require no additional calls.

## Modules

- `compression_jpeg_quant_jpeg_dequantize_block_int16`: top-level block dequantizer for one 8×8 coefficient block.

## Functional Behavior

Each `in_coef_i` is interpreted as a signed 16-bit two's-complement integer in the range [-32768, 32767].

Each `in_qtbl_i` is interpreted as an unsigned 16-bit integer in the range [0, 65535].

For each coefficient index `i` from 0 to 63, the module computes one dequantized coefficient independently:

- `prod_i = coef_i * qtbl_i`
- `coef_i` is the signed interpretation of `in_coef_i`
- `qtbl_i` is the unsigned interpretation of `in_qtbl_i`
- `prod_i` is computed in unbounded signed integer arithmetic for specification purposes
- `out_coef_i` is the low 16 bits of `prod_i`, returned as a 16-bit port bit pattern

This is modulo-2^16 wraparound of the exact product. Equivalently, `out_coef_i = prod_i mod 2^16`, encoded on the output port as a 16-bit two's-complement bit pattern.

No saturation is applied. No rounding is applied. No coefficient is skipped. A quantizer entry of zero produces output zero for that coefficient because `coef_i * 0 = 0`.

The 64 coefficient positions are processed in natural order. Position 0 is the DC coefficient. Positions 1 through 63 are AC coefficients. The module does not perform zigzag-to-natural or natural-to-zigzag conversion; it assumes both the input coefficient vector and the quantization-table vector are already aligned by index.

The output values are independent across indices. There is exactly one output transaction for each accepted input transaction.

Worked examples:

- If `in_coef_0 = 16'hFFFD` and `in_qtbl_0 = 16'd7`, then `coef_0 = -3`, `qtbl_0 = 7`, `prod_0 = -21`, and `out_coef_0 = 16'hFFEB`.
- If `in_coef_1 = 16'h7FFF` and `in_qtbl_1 = 16'h0002`, then `prod_1 = 65534`, and `out_coef_1 = 16'hFFFE`.
- If `in_coef_2 = 16'h8000` and `in_qtbl_2 = 16'h0002`, then `coef_2 = -32768`, `prod_2 = -65536`, and `out_coef_2 = 16'h0000`.

## Reset Behavior

Reset is synchronous active-high `rst`.

The module has no architectural state affecting functional results across transactions.

During reset, `out_valid` shall be 0. `in_ready` may be 1. All output coefficient ports `out_coef_0` through `out_coef_63` shall be driven to 0 while reset is asserted.
