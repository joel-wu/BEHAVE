# NVFP4 sixteen-element block quantization

Quantize sixteen BF16 values into an NVFP4 block with an E4M3FN block scale and a supplied tensor-wide scale.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_nvfp4_nvfp4_quant (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [15:0] in_x0,
    input logic [15:0] in_x1,
    input logic [15:0] in_x2,
    input logic [15:0] in_x3,
    input logic [15:0] in_x4,
    input logic [15:0] in_x5,
    input logic [15:0] in_x6,
    input logic [15:0] in_x7,
    input logic [15:0] in_x8,
    input logic [15:0] in_x9,
    input logic [15:0] in_x10,
    input logic [15:0] in_x11,
    input logic [15:0] in_x12,
    input logic [15:0] in_x13,
    input logic [15:0] in_x14,
    input logic [15:0] in_x15,
    input logic [31:0] in_global_scale,
    input logic out_ready,
    output logic out_valid,
    output logic [7:0] out_scale,
    output logic [3:0] out_q0,
    output logic [3:0] out_q1,
    output logic [3:0] out_q2,
    output logic [3:0] out_q3,
    output logic [3:0] out_q4,
    output logic [3:0] out_q5,
    output logic [3:0] out_q6,
    output logic [3:0] out_q7,
    output logic [3:0] out_q8,
    output logic [3:0] out_q9,
    output logic [3:0] out_q10,
    output logic [3:0] out_q11,
    output logic [3:0] out_q12,
    output logic [3:0] out_q13,
    output logic [3:0] out_q14,
    output logic [3:0] out_q15,
    output logic [0:0] out_error
);
```

### Behavior interface

```python
class AiMlQuantNvfp4Nvfp4Quant:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7, in_x8, in_x9, in_x10, in_x11, in_x12, in_x13, in_x14, in_x15, in_global_scale): ...
```

Call `process` once per accepted input. It returns one dictionary with exactly
these fields, including on an error:

```python
{"out_scale": ..., "out_error": ...,
 "out_q0": ..., "out_q1": ..., "out_q2": ..., "out_q3": ...,
 "out_q4": ..., "out_q5": ..., "out_q6": ..., "out_q7": ...,
 "out_q8": ..., "out_q9": ..., "out_q10": ..., "out_q11": ...,
 "out_q12": ..., "out_q13": ..., "out_q14": ..., "out_q15": ...}
```

Arguments and returned fields are Python integers holding the specified port
bit patterns, not Python floating-point values. The error flag is `0` or `1`.

## Modules

The top-level module implements one block operation. Tensor-wide scale calibration and multi-block scheduling remain outside this interface.

## Functional Behavior

Quantize sixteen BF16 inputs into one NVFP4 block. in_global_scale is a SUPPLIED positive finite-normal binary32 tensor-wide scale. It need not be recomputed from this block: different blocks of the same tensor share it. A caller may obtain it as tensor_amax/(6*448), but that outer reduction is not this block's operation.

Widen each BF16 word exactly by appending sixteen zero bits, then apply the numerical profile below. Let p be the largest absolute input. Compute base=mul(6,in_global_scale) and a=div(p,base); replace a=0 by 1, as in the source dynamic-scale path. Clamp a to [2^-9,448] and round to the nearest positive E4M3FN value, ties to an even encoding; return its eight-bit code as out_scale. This is NOT an E8M0 power-of-two scale.

For E4M3FN code c in 1..126, e=c>>3 and m=c&7. Its value is m*2^-9 when e=0, otherwise (1+m/8)*2^(e-7). Code 126 is 448; code 127 is NaN and is never generated. Set denominator=mul(decode(out_scale),in_global_scale). For each input x, round div(x,denominator) to the nearest E2M1 magnitude in [0,0.5,1,1.5,2,3,4,6], ties to an even three-bit magnitude code; saturate above 6. The high code bit is 1 iff the scaled value is strictly negative (so negative zero input maps to code 0, but a small negative nonzero value can round to code 8).

Return out_error=1 and every other output zero if any BF16 input is nonfinite, the supplied global scale is not positive finite-normal, or base/denominator is not positive finite-normal under the arithmetic profile. Otherwise out_error=0. Positive infinity produced by a finite input/denominator ratio saturates to magnitude code 7. These checked arithmetic boundaries are explicit finite extensions, not an assertion that all PyTorch dtypes flush subnormals.

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

One accepted input produces one output transaction containing all output fields. Transfers occur on rising edges with valid and ready high; output payload and valid remain stable while stalled. Only accepted input changes the pending result.

## Reset Behavior

Synchronous active-high reset clears the pending result, out_valid and all output fields. There is no persistent numerical state. in_ready=1 after reset; output payloads are zero when idle.
