# AWQ four-by-four quantized scale trial

Evaluate one supplied activation-aware scaling candidate with a genuine W4 quantizer and reconstruction loss.

## Interface

### RTL interface

```systemverilog
module AWQFusedDequantMatMul (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [31:0] in_x0,
    input logic [31:0] in_x1,
    input logic [31:0] in_x2,
    input logic [31:0] in_x3,
    input logic [31:0] in_w00,
    input logic [31:0] in_w01,
    input logic [31:0] in_w02,
    input logic [31:0] in_w03,
    input logic [31:0] in_w10,
    input logic [31:0] in_w11,
    input logic [31:0] in_w12,
    input logic [31:0] in_w13,
    input logic [31:0] in_w20,
    input logic [31:0] in_w21,
    input logic [31:0] in_w22,
    input logic [31:0] in_w23,
    input logic [31:0] in_w30,
    input logic [31:0] in_w31,
    input logic [31:0] in_w32,
    input logic [31:0] in_w33,
    input logic [31:0] in_s0,
    input logic [31:0] in_s1,
    input logic [31:0] in_s2,
    input logic [31:0] in_s3,
    input logic out_ready,
    output logic out_valid,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3,
    output logic [31:0] out_loss,
    output logic [63:0] out_qweight,
    output logic [127:0] out_quant_scale,
    output logic [0:0] out_error
);
```

### Behavior interface

```python
class AWQFusedDequantMatMul:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3, in_w00, in_w01, in_w02, in_w03, in_w10, in_w11, in_w12, in_w13, in_w20, in_w21, in_w22, in_w23, in_w30, in_w31, in_w32, in_w33, in_s0, in_s1, in_s2, in_s3): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_loss`, `out_y0`, `out_y1`, `out_y2`, `out_y3`: 32.
- `out_quant_scale`: 128.
- `out_qweight`: 64.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

AWQFusedDequantMatMul is the complete trial. An external search chooses scales from multiple trials; no scale is chosen implicitly here.

## Functional Behavior

This is one AWQ scale-search trial, not the outer search or activation observer. It retains the selected TorchAO observer's essential order: scale weight columns, quantize/dequantize those weights, divide activations by the same scales, evaluate the quantized output and measure its error. Omitting quantization would cancel every nonzero scale and is not this task.

All x, w and s ports contain binary32 values. w_ij is output row i/input column j. Scales s_j must be positive finite normal. The selected base quantizer is symmetric signed W4 with one scale per four-element row, codes [-7,7], zero point zero and nearest-integer ties-even rounding. This is an explicit base-quantizer choice: the archived generic AOBaseConfig does not fix a particular rounding/backend format. No bias is included.

For each column, X_j=div(x_j,s_j). For each row i, V_j=mul(w_ij,s_j), peak=max(abs(V_j)), scale_i=div(peak,7) (use 1 if peak=0). Compute q_ij=clip(round_even(div(V_j,scale_i)),-7,7), D_ij=mul(float(q_ij),scale_i). Return out_yi as the left-to-right sum, starting at +0, of mul(X_j,D_ij). The reference unquantized output O_i is the corresponding left fold of mul(x_j,w_ij). out_loss=mul(0.25,left_fold_i(mul(sub(O_i,out_yi),sub(O_i,out_yi)))). Each primitive rounds separately; no fused multiply-add or reassociation.

out_qweight contains two's-complement four-bit q_ij at bits [4*(4*i+j)+:4]. out_quant_scale contains row i's binary32 scale at [32*i+:32]. These outputs expose the actual quantization trial, not just its matrix result.

Return out_error=1 with every other output zero if any x/w is nonfinite, any s is invalid, any scaled weight or ratio is nonfinite, a nonzero peak produces a non-normal quantization scale, or any scaled activation, final output or loss is nonfinite. Otherwise return out_error=0. These explicit error records complete the finite source domain; they do not claim PyTorch's unspecified casting behavior. Subnormal input words retain their values under the rules below.

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

One accepted input produces one mapping containing all eight output fields. Transfers occur at rising edges with valid and ready high. Output payload and valid remain stable under backpressure; inputs take effect only on acceptance.

## Reset Behavior

Synchronous active-high reset discards pending output, clears output payloads and out_valid. There is no persistent numerical state. in_ready=1 after reset. Output payloads are zero while idle.
