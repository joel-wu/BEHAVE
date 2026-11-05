# Eight-lane stable masked softmax

A finite binary32 specialization of the stable exponential normalization used in attention. It emits all eight probabilities, not merely a selected index.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_softmax_softmax_max_subtract_stable (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [255:0] in_logits,
    input logic [7:0] in_mask,
    output logic out_valid,
    input logic out_ready,
    output logic [255:0] out_prob
);
```

### Behavior interface

```python
class StableSoftmax:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_logits, in_mask): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_prob`: 256.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The named top-level module is the complete task. Implementation latency and architecture are unrestricted subject to the declared execution constraints.

## Functional Behavior

This is the normalized softmax sub-operator of Qwen2 attention, not its projections, cache, or value multiplication. Eight binary32 logits are packed with lane i in bits `[32*i +: 32]`. `in_mask[i]=1` includes lane i; zero excludes it completely, including any NaN payload. The output `out_prob` uses the same lane layout.

Let A be the included lanes. If any included logit is NaN, every output lane is canonical NaN. Otherwise:

1. If A is empty or all its logits are negative infinity, every probability is +0.
2. If any included logit is positive infinity, use numerator 1 for those lanes and 0 for all others.
3. Otherwise find the greatest included logit m, with numerical comparisons preserving subnormals. Each included numerator is `E(sub(logit[i],m))`; excluded numerators are zero. Negative infinity therefore receives zero.
4. Sum numerators left-to-right, starting from +0. Divide each numerator by this sum, rounding separately. Excluded lanes remain +0.

The max subtraction avoids positive exponential overflow. This finite specialization preserves normalization, ordering, masking, and contributions from nonmaximal logits. It is not an argmax or winner selection. Two equal included logits produce exactly (1/2,1/2); one included logit gives probability1. Independent rounding means the sum of output words need not be bit-exact1 for every row.

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

Asynchronous active-high reset discards any pending output, deasserts `out_valid` and zeros output payloads. There is no persistent numerical state. When idle, output payloads are zero and the module can accept an input; `in_ready=1` after reset. A stalled result must not be overwritten.
