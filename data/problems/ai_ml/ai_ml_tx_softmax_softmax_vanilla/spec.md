# Four-logit softmax

A finite binary32 softmax specialization retaining exponential normalization and every output probability.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_softmax_softmax_vanilla (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
);
```

### Behavior interface

```python
class AiMlTxSoftmaxSoftmaxVanilla:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The named top-level module is the complete task. Implementation latency and architecture are unrestricted subject to the declared execution constraints.

## Functional Behavior

This specializes Llama attention's float32 softmax to one row of four logits. It does not include attention projections or value multiplication. Produce all four out_y fields together.

If any logit is NaN, all outputs are canonical NaN. Otherwise if the row contains positive infinities, give each such lane numerator 1 and all other lanes numerator 0. An all-negative-infinity row returns four +0 words (an explicit extension outside finite source inputs). For every other row, let m be the maximum numerical logit and form n[i]=E(sub(x[i],m)). Sum n[0] through n[3] left-to-right starting at +0, then return div(n[i],sum) in lane i. Every operation rounds separately under the numerical contract below.

The exponential approximation is continuous within its normal-output domain, rather than rounding each logit to an integer bin. Equal finite logits produce exactly four 1/4 values; nonmaximal finite logits retain nonzero contributions unless they underflow. Max subtraction permits large common logit offsets. The rounded probabilities need not sum to bit-exact 1.

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
