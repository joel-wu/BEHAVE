# Granite rotary grouped-query attention

Two query heads share one two-token KV head, with head dimension four. This preserves the archived Granite rotary transform, KV repetition, configurable attention multiplier and softmax-weighted values. Projection layers, cache management, dropout and output projection are outside this supplied-Q/K/V sub-operator.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_sdpa_granite_sdpa_attn (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [32*2*4-1:0]   in_query,
    input  logic [32*1*2*4-1:0] in_key,
    input  logic [32*1*2*4-1:0] in_value,
    input  logic [32*4-1:0]     in_cos,
    input  logic [32*4-1:0]     in_sin,
    input  logic [32*2*2-1:0]   in_attention_mask,
    input  logic [31:0]         in_scaling,

    output logic out_valid,
    input  logic out_ready,
    output logic [32*2*4-1:0]   out_attn_output,
    output logic [32*2*2-1:0]   out_attn_weights
);
```

### Behavior interface

```python
class GraniteSdpaAttention:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_query, in_key, in_value, in_cos, in_sin, in_attention_mask, in_scaling): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_attn_output`: 256.
- `out_attn_weights`: 128.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation and protocol

All data lanes are raw binary32 words. Packed lane zero occupies the least-significant 32 bits. Each accepted input produces one ordered output record. A transfer occurs on a rising edge with valid and ready both high. Output valid and payload stay stable while stalled; unaccepted inputs have no effect. Latency and architecture are unrestricted within the declared execution constraints.

## Modules

The named top-level module implements the full task with no external numerical state.

## Functional Behavior

Query/output lane index is `4*h+d` (`h=0,1; d=0..3`); key/value index is `4*t+d` (`t=0,1`). Mask and returned probability index is `2*h+t`. Cosine and sine each provide four lanes, broadcast to both query heads and both key positions. This shared-position-table choice is an explicit specialization; no rotary angles are computed internally.

For each query or key vector `a`, `rotate_half(a)=[-a2,-a3,a0,a1]`, and transformed lane `d` is `add(mul(a[d],cos[d]), mul(rotate_half(a)[d],sin[d]))`. For each head/token, form the four-product dot sum left-to-right, starting with the first product. Score is `add(mul(dot,in_scaling),mask[h,t])`.

Normalize the two scores as specified below. Return both probabilities and, for each dimension, the left-to-right weighted sum from +0 of the two shared value vectors. For equal finite scores, weights are `[0.5,0.5]`, so values `[2,6]` yield `4`, not `2`.

### Stable normalization

For each score row, any NaN makes every probability canonical NaN. An all-negative-infinity row produces all-zero probabilities. Positive-infinity maxima share probability equally; other positions get zero. Otherwise subtract the finite maximum, apply `E` to every difference, sum the numerators left-to-right from +0, and divide each numerator by that sum. A zero-probability value term contributes +0 without evaluating `0 * value`, so excluded values cannot contaminate the result. Nonzero/NaN weights follow ordinary floating arithmetic. Ties retain all contributions, not the first maximum.

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


## Reset Behavior

Synchronous active-high reset clears pending output, deasserts `out_valid`, zeros output payloads and leaves `in_ready=1`. There is no persistent numerical state. An unconsumed result is discarded by reset. Idle payloads are zero; a stalled result must not be overwritten.
