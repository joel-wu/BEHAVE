# NVFP4 scaled block dot product

Compute one sixteen-term NVFP4 block dot product using both block scales and supplied global scales; this is a block primitive, not a complete matrix multiplication.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_nvfp4_nvfp4_dequant_matmul (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [63:0] in_a,
    input logic [63:0] in_b,
    input logic [7:0] in_scale_a,
    input logic [7:0] in_scale_b,
    input logic [31:0] in_global_a,
    input logic [31:0] in_global_b,
    input logic out_ready,
    output logic out_valid,
    output logic [31:0] out_y,
    output logic [0:0] out_error
);
```

### Behavior interface

```python
class AiMlQuantNvfp4Nvfp4DequantMatmul:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a, in_b, in_scale_a, in_scale_b, in_global_a, in_global_b): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_y`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The top-level module implements one block operation. Tensor-wide scale calibration and multi-block scheduling remain outside this interface.

## Functional Behavior

Compute one sixteen-term dot product from two complete NVFP4 blocks. This is the block dot primitive of dequantize-and-matmul, not a full matrix tile. in_a and in_b carry sixteen E2M1 nibbles at [4*i+:4]. Each block has its own E4M3FN eight-bit scale and supplied binary32 global scale. Omitting either scale is not this operation.

Decode E4M3FN c in 1..126 using e=c>>3, m=c&7: m*2^-9 for e=0, otherwise (1+m/8)*2^(e-7). Codes 0 and 127..255 are invalid scale encodings. E2M1 low-three-bit magnitudes are [0,0.5,1,1.5,2,3,4,6], negated for a set sign bit. As in the selected source dequantization table, both zero codes decode to +0.

Form sa=mul(decode(in_scale_a),in_global_a) and sb likewise. For i=0..15, dequantize ai=mul(decode(in_a[i]),sa) and bi=mul(decode(in_b[i]),sb). Starting from +0, accumulate add(acc,mul(ai,bi)) in increasing i order. out_y is binary32, not a clipped Q8.8 surrogate. This explicit separate dequantization/multiply/add order does not claim bit identity to a fused hardware GEMM.

Return out_error=1 and out_y=0 if a scale encoding is invalid, a global scale or sa/sb is not positive finite-normal, or any dequantized operand, product or partial sum is nonfinite. Otherwise out_error=0 with the computed dot product. The entire 16-lane block participates; no implicit padding or hidden scale-one convention applies.

### Numerical contract

All floating arithmetic uses raw binary32 encodings. Each stated add,
subtract, multiply or divide rounds separately to nearest, ties to even.
Subnormal operands retain their numerical values and underflow is gradual,
including rounding at the zero/subnormal and subnormal/normal boundaries.
Overflow produces signed infinity. No fusion or reassociation may change
the stated rounding points; compare output encodings exactly, without a
tolerance. The task's explicit finite-normal scale checks remain in force.

An arithmetic NaN operand or invalid operation produces `0x7FC00000` (a
project sign/payload convention). Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity,
zero/zero and infinity/infinity. Multiply/divide use the XOR sign for zero
and infinity. Finite nonzero/zero gives signed infinity; finite/infinity
gives signed zero. Adding two negative zeros gives negative zero; other
exact cancellation gives positive zero. Ordered comparisons with NaN are
false, both zeros compare equal, and comparisons do not flush subnormals.
Raw copying, BF16 promotion and sign-bit operations preserve their stated
bits. No exception flags or dynamic rounding modes are required.

One accepted input produces one mapping with all output fields. Transfers occur on rising edges with valid and ready high; output payload and valid remain stable while stalled. Only accepted input changes the pending result.

## Reset Behavior

Synchronous active-high reset clears the pending result, out_valid and all output fields. There is no persistent numerical state. in_ready=1 after reset; output payloads are zero when idle.
