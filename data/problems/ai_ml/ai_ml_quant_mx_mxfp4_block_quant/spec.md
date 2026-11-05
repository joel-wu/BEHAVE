# BF16-to-MXFP4 Block Quantization

Quantize 32 BF16 values into 32 E2M1 codes and one shared E8M0 power-of-two
scale. All lanes contribute to the block maximum; scale selection and element
rounding are distinct operations.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_mx_mxfp4_block_quant (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x0,
    input  logic [15:0] in_x1,
    input  logic [15:0] in_x2,
    input  logic [15:0] in_x3,
    input  logic [15:0] in_x4,
    input  logic [15:0] in_x5,
    input  logic [15:0] in_x6,
    input  logic [15:0] in_x7,
    input  logic [15:0] in_x8,
    input  logic [15:0] in_x9,
    input  logic [15:0] in_x10,
    input  logic [15:0] in_x11,
    input  logic [15:0] in_x12,
    input  logic [15:0] in_x13,
    input  logic [15:0] in_x14,
    input  logic [15:0] in_x15,
    input  logic [15:0] in_x16,
    input  logic [15:0] in_x17,
    input  logic [15:0] in_x18,
    input  logic [15:0] in_x19,
    input  logic [15:0] in_x20,
    input  logic [15:0] in_x21,
    input  logic [15:0] in_x22,
    input  logic [15:0] in_x23,
    input  logic [15:0] in_x24,
    input  logic [15:0] in_x25,
    input  logic [15:0] in_x26,
    input  logic [15:0] in_x27,
    input  logic [15:0] in_x28,
    input  logic [15:0] in_x29,
    input  logic [15:0] in_x30,
    input  logic [15:0] in_x31,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_scale,
    output logic [7:0]  out_q0,
    output logic [7:0]  out_q1,
    output logic [7:0]  out_q2,
    output logic [7:0]  out_q3,
    output logic [7:0]  out_q4,
    output logic [7:0]  out_q5,
    output logic [7:0]  out_q6,
    output logic [7:0]  out_q7,
    output logic [7:0]  out_q8,
    output logic [7:0]  out_q9,
    output logic [7:0]  out_q10,
    output logic [7:0]  out_q11,
    output logic [7:0]  out_q12,
    output logic [7:0]  out_q13,
    output logic [7:0]  out_q14,
    output logic [7:0]  out_q15
);
```

### Behavior interface

```python
class ai_ml_quant_mx_mxfp4_block_quant:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7,
        in_x8, in_x9, in_x10, in_x11, in_x12, in_x13, in_x14, in_x15,
        in_x16, in_x17, in_x18, in_x19, in_x20, in_x21, in_x22, in_x23,
        in_x24, in_x25, in_x26, in_x27, in_x28, in_x29, in_x30, in_x31
    ): ...
```

`process()` consumes exactly one accepted input transaction and returns exactly one output transaction as a dict with keys `out_scale`, `out_q0`, `out_q1`, `out_q2`, `out_q3`, `out_q4`, `out_q5`, `out_q6`, `out_q7`, `out_q8`, `out_q9`, `out_q10`, `out_q11`, `out_q12`, `out_q13`, `out_q14`, and `out_q15`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_q0`, `out_q1`, `out_q10`, `out_q11`, `out_q12`, `out_q13`, `out_q14`,
  `out_q15`, `out_q2`, `out_q3`, `out_q4`, `out_q5`, `out_q6`, `out_q7`, `out_q8`,
  `out_q9`, `out_scale`: 8.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

An input transfer produces one ordered result. Transfers occur on rising clock
edges with valid and ready high. Stalled outputs remain valid with unchanged
fields. No fixed computation latency is required.

## Modules

`ai_ml_quant_mx_mxfp4_block_quant` is the stateless 32-element quantizer.

## Functional Behavior

Inputs are raw BF16 encodings. Preserve finite subnormal inputs and both signs
of zero. For a finite, nonzero block maximum a, choose
`s = max(-127, floor(log2(a)) - 2)` and return `out_scale = s + 127`.
An all-zero block uses s=-127. E8M0 code c=0..254 means exactly 2^(c-127),
including c=0: it is not zero and is not clamped to a larger scale.

For each input x, divide exactly by 2^s and round to the nearest magnitude in
`[0, 0.5, 1, 1.5, 2, 3, 4, 6]`, saturating at 6. Their three-bit payloads
are 0..7 respectively. On a midpoint, choose the payload with even low bit;
for example 1.25 rounds to 1, and 5 rounds to 4. Prefix the original sign as
bit 3, preserving signed zero even when a nonzero magnitude rounds to zero.
There is no intermediate floating-point rounding or denormal flush.

If any input is NaN or infinity, return the invalid-block encoding
`out_scale=255` and all packed bytes zero. This is the task's explicit
non-finite-block policy; the E2M1 payload itself has no NaN or infinity code.

Pack lane 2*k in the low nibble and lane 2*k+1 in the high nibble of `out_qk`.
For example a block with maximum 6 has scale code 127. In that block +1.25
and -5 encode as 0x2 and 0xE. A block with maximum 1 has scale code 125.

## Reset Behavior

Reset is asynchronous active-high. It cancels any pending result, clears all
output fields, deasserts output valid and leaves input ready asserted. There
is no other state. Output data is meaningful only when output valid is high.
