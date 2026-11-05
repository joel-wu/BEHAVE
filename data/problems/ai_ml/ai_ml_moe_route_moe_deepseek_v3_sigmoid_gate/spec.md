# Grouped sigmoid routing for eight experts

A finite DeepSeek-V3 routing operator retaining sigmoid scores, selection-only correction bias, group selection and optional normalized router weights.

## Interface

### RTL interface

```systemverilog
module ai_ml_moe_route_moe_deepseek_v3_sigmoid_gate (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_router_logits_0,
    input  logic [31:0] in_router_logits_1,
    input  logic [31:0] in_router_logits_2,
    input  logic [31:0] in_router_logits_3,
    input  logic [31:0] in_router_logits_4,
    input  logic [31:0] in_router_logits_5,
    input  logic [31:0] in_router_logits_6,
    input  logic [31:0] in_router_logits_7,
    input  logic [31:0] in_e_score_correction_bias_0,
    input  logic [31:0] in_e_score_correction_bias_1,
    input  logic [31:0] in_e_score_correction_bias_2,
    input  logic [31:0] in_e_score_correction_bias_3,
    input  logic [31:0] in_e_score_correction_bias_4,
    input  logic [31:0] in_e_score_correction_bias_5,
    input  logic [31:0] in_e_score_correction_bias_6,
    input  logic [31:0] in_e_score_correction_bias_7,
    input  logic        in_norm_topk_prob,
    input  logic [31:0] in_routed_scaling_factor,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [2:0]  out_topk_indices_0,
    output logic [2:0]  out_topk_indices_1,
    output logic [31:0] out_topk_weights_0,
    output logic [31:0] out_topk_weights_1
);
```

### Behavior interface

```python
class AiMlMoeRouteMoeDeepseekV3SigmoidGate:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_router_logits_0, in_router_logits_1, in_router_logits_2, in_router_logits_3,
                in_router_logits_4, in_router_logits_5, in_router_logits_6, in_router_logits_7,
                in_e_score_correction_bias_0, in_e_score_correction_bias_1,
                in_e_score_correction_bias_2, in_e_score_correction_bias_3,
                in_e_score_correction_bias_4, in_e_score_correction_bias_5,
                in_e_score_correction_bias_6, in_e_score_correction_bias_7,
                in_norm_topk_prob, in_routed_scaling_factor): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_topk_indices_0`, `out_topk_indices_1`: 3.
- `out_topk_weights_0`, `out_topk_weights_1`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The named top-level module is the complete task. Implementation latency and architecture are unrestricted subject to the declared execution constraints.

## Functional Behavior

Specialize DeepSeek-V3 route_tokens_to_experts to eight supplied router logits, two groups of four experts, one selected group and two selected experts. Computing the logits' input projection and executing experts are outside this task. Each logit, correction bias and scaling factor is a raw binary32 word.

1. gates[i]=sigmoid(logit[i]); choices[i]=add(gates[i],bias[i]).
2. Within groups {0,1,2,3} and {4,5,6,7}, select the two largest choices. A group's score is the rounded sum of these two choices. Select the larger-scoring group.
3. Emit that group's two selected expert indices in descending choices order. Correction biases affect selection only: fetch weights from the original gates, not choices.
4. If in_norm_topk_prob is 1, d=add(add(gates[sel0],gates[sel1]),0x1E3CE508), then weight[i]=div(gates[sel_i],d). Otherwise weight[i]=gates[sel_i]. Multiply each weight by in_routed_scaling_factor to form the output.

For every comparison, numeric values rank ahead of NaN; equal numerical values (including both zeros), or two NaNs, are ordered by smaller expert/group index. This specifies deterministic ties and output order where the source's unsorted topk does not. All eight logits matter even when they have the same sign. The returned mapping contains the two out_topk_indices fields (3-bit indices) and two out_topk_weights fields (binary32).

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
