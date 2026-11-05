# Two-channel affine INT8 quantization

Quantize a binary32 tensor of shape [2 channels, 2 samples] using independent channel scales and integer zero points. This specializes torchao quantize_affine with block_size=(1,2) and signed INT8 output. It quantizes floating values, not already-quantized int8 inputs.

## Interface

### RTL interface

```systemverilog
module Int8PerChannelQuant (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [127:0] in_input,
    input logic [63:0] in_scale,
    input logic [15:0] in_zero_point,
    input logic [7:0] in_quant_min, in_quant_max,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_quantized,
    output logic out_error
);
```

### Behavior interface

```python
class Int8PerChannelQuant:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_input, in_scale, in_zero_point, in_quant_min, in_quant_max): ...
```

 process returns out_quantized and out_error in one record.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_quantized`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Lane i=2*channel+sample occupies bits [32*i +: 32] of in_input. Channel c's binary32 scale occupies [32*c +:32] of in_scale and its signed 8-bit zero point occupies [8*c +:8] of in_zero_point. in_quant_min and in_quant_max are signed 8-bit bounds shared by both channels. The output byte at [8*i +:8] is the two's-complement INT8 result for lane i.

## Modules

Int8PerChannelQuant is the complete operator; calibration/scale selection and dequantization are not included.

## Functional Behavior

For channel c compute inv[c]=div(1.0,scale[c]), rounded to binary32. For each of its two samples, z=mul(input[i],inv[c]), again rounded to binary32. Then q=round_to_nearest_integer_ties_even(z)+zero_point[c], clamped to [quant_min,quant_max]. Pack q as the corresponding signed byte. Preserve this reciprocal-then-multiply order from the source; a single direct division is not its bit-level definition. Add the integer zero point after rounding, without intermediate integer wrap.

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
bits. No exception flags or dynamic rounding modes are required. A finite scaled value beyond the INT8 range saturates after zero-point addition; positive/negative infinity resulting from overflow also saturates to the respective bound.

Every input pattern has a defined response. Return out_error=1 and out_quantized=0 if quant_min>quant_max, if any scale is not positive finite normal binary32, if an input lane is nonfinite, or if reciprocal/multiply creates NaN (e.g. zero times an overflowing reciprocal). Otherwise out_error=0. Invalid rows are not partially published. These explicit error results and numerical rules extend/specify the finite source domain; they do not claim PyTorch's unspecified NaN-to-integer casting behavior.

Examples with bounds [-128,127]: inputs [.5,1.5,2.5,3.5], scales [1,1], zero points [0,0] produce [0,2,2,4]. Inputs [1,2,1,2], scales [1,.5], zero points [0,10] produce [1,2,12,14]. Scale changes apply to both samples in that channel only.

## Reset Behavior

Synchronous active-high reset discards pending output, deasserts out_valid and clears output payloads. There is no persistent numerical state. in_ready=1 after reset. Each accepted input produces one ordered output; transfers occur on rising clock edges when valid and ready are high. A pending response stays stable under backpressure. Inputs have no effect before acceptance and output payloads are zero while idle.
