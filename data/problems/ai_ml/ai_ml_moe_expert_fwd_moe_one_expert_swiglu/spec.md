# Two-channel routed SwiGLU expert

One finite Mixtral expert with independent gate and up projections, smooth SiLU gating, a down projection and a router weight.

## Interface

### RTL interface

```systemverilog
module ai_ml_moe_expert_fwd_moe_one_expert_swiglu (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [31:0] in_x_0,
    input logic [31:0] in_x_1,
    input logic [31:0] in_gate_w_0_0,
    input logic [31:0] in_gate_w_0_1,
    input logic [31:0] in_gate_w_1_0,
    input logic [31:0] in_gate_w_1_1,
    input logic [31:0] in_up_w_0_0,
    input logic [31:0] in_up_w_0_1,
    input logic [31:0] in_up_w_1_0,
    input logic [31:0] in_up_w_1_1,
    input logic [31:0] in_down_w_0_0,
    input logic [31:0] in_down_w_0_1,
    input logic [31:0] in_down_w_1_0,
    input logic [31:0] in_down_w_1_1,
    input logic [31:0] in_top_k_weight,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_y_0, out_y_1
);
```

### Behavior interface

```python
class AiMlMoeExpertFwdMoeOneExpertSwiglu:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x_0, in_x_1, in_gate_w_0_0, in_gate_w_0_1, in_gate_w_1_0, in_gate_w_1_1, in_up_w_0_0, in_up_w_0_1, in_up_w_1_0, in_up_w_1_1, in_down_w_0_0, in_down_w_0_1, in_down_w_1_0, in_down_w_1_1, in_top_k_weight): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y_0`, `out_y_1`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The named top-level module is the complete task. Implementation latency and architecture are unrestricted subject to the declared execution constraints.

## Functional Behavior

This is one already-selected Mixtral expert's contribution for one token, with hidden and intermediate dimensions both 2, no biases and independent gate/up/down matrices. Each in_x_i and in_*_w_j_i is a binary32 word; matrix indices mean output row j, input column i. The routing weight is supplied separately. Expert selection and summing several experts are outside this task.

For j=0,1 compute gate[j]=add(mul(x[0],gate_w[j,0]),mul(x[1],gate_w[j,1])) and up[j] with the independent up_w matrix. Then h[j]=mul(SiLU(gate[j]),up[j]). For each output row j, compute y[j]=add(mul(h[0],down_w[j,0]),mul(h[1],down_w[j,1])) and out_y_j=mul(y[j],in_top_k_weight). Do not tie the gate/up weights, fuse operations or move router scaling into a projection. process returns both out_y fields in one record.

For sigmoid, let a=E(-abs(x)) and d=add(1,a). Return div(1,d) for a nonnegative sign bit and div(a,d) for a negative sign bit. Thus no positive exponential is formed. SiLU is mul(x,sigmoid(x)), not a three-region clamp. Sigmoid maps both zeros to 1/2, +infinity to 1, -infinity to +0, and NaN to canonical NaN. Subnormal inputs follow the same composed formula: E2's small-input rule tests the result of mul(-abs(x),log2(e)), not the original sigmoid input. SiLU follows the separate multiplication, including -infinity times zero producing NaN. Finite very negative values may produce negative zero under the exponential cutoff or gradual rounding.

### Numerical contract

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.

The exponential uses range reduction and a positive piecewise-linear approximation, not a sign-based switch. Define `E2(x)` on a raw binary32 word. As an explicit approximation rule, either zero or any subnormal input returns exactly 1; this is not a rule of the basic FP32 arithmetic. For other inputs:

- NaN gives canonical NaN; negative infinity or finite x < -126 gives +0; positive infinity or x >= 128 gives +infinity.
- Otherwise q=floor(65536*x), n=floor(q/65536), f=q-65536*n, j=floor(f/2048), r=f-2048*j.
- For i=0..32, T[i] is the nearest-even integer to `2^(i/32)*2^30`. Set Y=T[j]+floor((T[j+1]-T[j])*r/2048).
- Return the nearest-even binary32 rounding of `Y*2^(n-30)`, with nearest-even rounding and signed-infinity overflow. The explicit x < -126 cutoff above belongs only to this approximation.

`E(x) = E2(mul(x, 0x3FB8AA3B))`, where the constant is binary32 log2(e). This defines both the table and the interpolation independently of either implementation. For -126 <= x < 128, E2 has relative error below 0.01% against 2^x (away from overflow); integer powers of two are exact. For E, the same 0.01% bound applies where its scaled argument is in this normal-output range. The approximation deliberately returns zero below its stated cutoff; basic FP32 arithmetic still uses gradual underflow. The task fixes observable arithmetic, not a required ROM or circuit architecture.


Each accepted input emits exactly one ordered output record. Transfers occur at rising clock edges when valid and ready are both high. Output valid and payload stay stable while stalled; inputs have no effect before acceptance.

## Reset Behavior

Synchronous active-high reset discards any pending output, deasserts `out_valid` and zeros output payloads. There is no persistent numerical state. When idle, output payloads are zero and the module can accept an input; `in_ready=1` after reset. A stalled result must not be overwritten.
