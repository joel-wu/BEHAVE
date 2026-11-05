# Online FlashAttention row-sum update

One row of FlashAttention running maximum/sum state. Previous state is supplied explicitly; the tile contributes a sum relative to the merged maximum.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_softmax_softmax_online_flash (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_prev_row_max,
    input  logic [31:0] in_prev_row_sum,
    input  logic [31:0] in_tile_row_max,
    input  logic [31:0] in_tile_row_exp_sum,
    input  logic [31:0] in_softmax_scale_log2,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_row_max,
    output logic [31:0] out_row_sum
);
```

### Behavior interface

```python
class AiMlTxSoftmaxSoftmaxOnlineFlash:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_prev_row_max, in_prev_row_sum, in_tile_row_max,
                in_tile_row_exp_sum, in_softmax_scale_log2): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_row_max`, `out_row_sum`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The named top-level module is the complete task. Implementation latency and architecture are unrestricted subject to the declared execution constraints.

## Functional Behavior

All five data inputs and two outputs are binary32 words. The outputs are `out_row_max` and `out_row_sum`.

1. `m = maxNum(in_prev_row_max, in_tile_row_max)`: one NaN selects the other operand; two NaNs give canonical NaN. Numeric comparison preserves subnormal values; ties retain the previous maximum.
2. The supplied `in_tile_row_exp_sum` is **already referenced to this merged m**, as in FlashAttention's `scale_apply_exp2(scores,row_max,scale)` followed by reduction. It is the tile sum of `E2(mul(sub(score,m),scale_log2))`, using the stated arithmetic. It is NOT a sum relative to the tile's own maximum. The tile logits are outside this interface.
3. If the previous sum is exactly +0 or -0, its contribution is that signed zero. This makes the conventional empty state `(m_prev=-infinity,s_prev=0)` an identity without computing 0*NaN.
4. Otherwise `a=E2(mul(sub(m_prev,m),scale_log2))` and `previous=mul(s_prev,a)`.
5. Return `out_row_max=m`, `out_row_sum=add(previous,in_tile_row_exp_sum)`.

For ordinary softmax use, scale_log2 is finite nonnegative, row sums are nonnegative, and maxima come from their rows. Other bit patterns follow the same explicit primitive/special-value rules; this block does not invent missing tile data. Previous max4 and sum3, tile max5 and merged-reference tile sum2, scale1 produce max5 and sum3.5, because E2(-1)=1/2. Supplying consecutive tile contributions preserves nonzero earlier mass; approximation/rounding may make different tile partitions differ slightly.

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
