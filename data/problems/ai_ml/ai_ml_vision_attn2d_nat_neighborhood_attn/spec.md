# Stable nine-lane neighborhood aggregation

A nine-way masked softmax-weighted sum over a supplied neighborhood, with stable range reduction and positive weights.

## Interface

### RTL interface

```systemverilog
/* verilator lint_off WIDTH */
/* verilator lint_off WIDTHEXPAND */
/* verilator lint_off WIDTHTRUNC */
/* verilator lint_off SELRANGE */
module ai_ml_vision_attn2d_nat_neighborhood_attn (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_score0,
    input  logic [31:0]  in_score1,
    input  logic [31:0]  in_score2,
    input  logic [31:0]  in_score3,
    input  logic [31:0]  in_score4,
    input  logic [31:0]  in_score5,
    input  logic [31:0]  in_score6,
    input  logic [31:0]  in_score7,
    input  logic [31:0]  in_score8,
    input  logic [31:0]  in_mask0,
    input  logic [31:0]  in_mask1,
    input  logic [31:0]  in_mask2,
    input  logic [31:0]  in_mask3,
    input  logic [31:0]  in_mask4,
    input  logic [31:0]  in_mask5,
    input  logic [31:0]  in_mask6,
    input  logic [31:0]  in_mask7,
    input  logic [31:0]  in_mask8,
    input  logic [31:0]  in_value0,
    input  logic [31:0]  in_value1,
    input  logic [31:0]  in_value2,
    input  logic [31:0]  in_value3,
    input  logic [31:0]  in_value4,
    input  logic [31:0]  in_value5,
    input  logic [31:0]  in_value6,
    input  logic [31:0]  in_value7,
    input  logic [31:0]  in_value8,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_value
);
```

### Behavior interface

```python
class AiMlVisionAttn2dNatNeighborhoodAttn:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_score0, in_score1, in_score2, in_score3, in_score4, in_score5, in_score6, in_score7, in_score8, in_mask0, in_mask1, in_mask2, in_mask3, in_mask4, in_mask5, in_mask6, in_mask7, in_mask8, in_value0, in_value1, in_value2, in_value3, in_value4, in_value5, in_value6, in_value7, in_value8): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_value`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The named top-level module is the complete task. Implementation latency and architecture are unrestricted subject to the declared execution constraints.

## Functional Behavior

Each of the nine supplied score, additive-mask and value words is binary32. The input caller has already extracted a 3x3 neighborhood and computed/scaled its scores. This task specializes the archived generic attention `softmax(scores+mask) @ values` stage; it does not implement neighborhood extraction, Q/K projections, head scaling or an entire NAT layer. Set `out_value` to the result.

1. Form `a[i]=add(score[i],mask[i])`. A mask of negative infinity excludes an ordinary finite score. If any resulting a[i] is NaN, the output is canonical NaN.
2. If all a[i] are negative infinity, return +0. If any are positive infinity, use numerator1 for those lanes and0 elsewhere. Otherwise m=max(a) and numerator[i]=E(sub(a[i],m)).
3. Sum numerators in index order starting from +0, then divide each by the sum to obtain weights. No negative weights are introduced by the approximation.
4. Starting from +0, accumulate `add(acc,mul(weight[i],value[i]))` in index order. A zero-weight lane is skipped entirely, so a masked NaN/infinite value does not contaminate the result. Nonzero-weight nonfinite values follow the numerical contract.

Equal finite scores give approximately1/9 to every lane; nine values all3 therefore return approximately3. A single unmasked lane gives its value exactly (subject to the stated arithmetic). For finite values the result lies within their convex hull up to binary32 rounding. Nonmaximal logits contribute positive mass until the explicit exponential-approximation cutoff; there is no winner-only shortcut.

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
