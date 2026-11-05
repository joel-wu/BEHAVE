# TemperatureTopKTopP

Filter eight binary32 logits using temperature, a top-k threshold and genuine
nucleus (top-p) cumulative probability. Return the filtered logits and their
deterministic argmax, not a random token sample. The exponential stage has an
explicit finite-grid approximation; ordinary probabilities from zero through
one are supported, and top-p is not disabled by the input domain.

## Interface

### RTL interface

```systemverilog
module ai_ml_sample_filter_sample_topk_topp_combined (
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
    input  logic [31:0]  in_temperature,
    input  logic [3:0]   in_top_k,
    input  logic [31:0]  in_top_p,
    input  logic [3:0]   in_min_tokens_to_keep_k,
    input  logic [3:0]   in_min_tokens_to_keep_p,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_score0,
    output logic [31:0]  out_score1,
    output logic [31:0]  out_score2,
    output logic [31:0]  out_score3,
    output logic [31:0]  out_score4,
    output logic [31:0]  out_score5,
    output logic [31:0]  out_score6,
    output logic [31:0]  out_score7,
    output logic [2:0]   out_token,
    output logic out_error
);
endmodule
```

Scores, temperature and top-p are raw IEEE-754 binary32 words. Top-k and both
minimum counts are unsigned integers. `out_token` is an index, `out_error`
indicates an invalid control value. Inputs/outputs transfer on rising edges
with their respective valid and ready signals high. One accepted vector
produces one ordered response. Valid and payload remain stable while stalled;
there is no fixed response latency.

### Behavior interface

```python
class AiMlSampleFilterSampleTopkToppCombined:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_score0, in_score1, in_score2, in_score3,
                in_score4, in_score5, in_score6, in_score7,
                in_temperature, in_top_k, in_top_p,
                in_min_tokens_to_keep_k, in_min_tokens_to_keep_p): ...
```

All values are unsigned bus integers. The method returns one dictionary with
`out_score0` through `out_score7`, `out_token`, and `out_error`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_error`: 1.
- `out_score0`, `out_score1`, `out_score2`, `out_score3`, `out_score4`, `out_score5`,
  `out_score6`, `out_score7`: 32.
- `out_token`: 3.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_sample_filter_sample_topk_topp_combined`: stateless logits filter.

## Functional Behavior

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

1. Temperature must be positive, normal and finite. Top-p must be finite in
   `[0,1]` (either signed zero is allowed). An invalid control produces eight
   negative-infinity words `0xff800000`, token zero and `out_error=1`.
   All other transactions have `out_error=0`; every score bit pattern is legal.
2. Divide each score by temperature in binary32, round-to-nearest ties-to-even.
   Preserve subnormal operands and use gradual underflow. Overflow produces signed infinity; Replace every NaN result
   with negative infinity; preserve other results including signed zero.
3. Let `k=min(8,max(1,in_top_k,in_min_tokens_to_keep_k))`. Keep every scaled
   score greater than or equal to the kth-largest value. Ties at this boundary
   are all retained. Replace lower scores with negative infinity.
4. Compute nucleus weights on the retained logits. If positive infinities
   exist, each gets weight 65536 and all other positions get zero. Otherwise,
   negative infinity has weight zero. For a finite value `x`, let `g(x)` be
   `16*x` rounded to the nearest integer, ties away from zero. With largest
   finite retained value `m`, let `d=g(m)-g(x)`; the weight is
   `floor(65536*exp(-d/16)+1/2)` for `0<=d<=128`, otherwise zero.
   `exp` is the natural exponential. This defines the finite exponential
   grid and tail cutoff exactly, rather than promising FP32 softmax agreement.
   If no retained finite or positive-infinite value exists, all weights are zero.
5. Sort the scaled scores ascending, with lower token index first on equal
   values (`-0` equals `+0`). Let `C` be the cumulative integer weight and `Z`
   the total. Remove a position if `C <= Z*(1-p)`, evaluated exactly with `p`
   the real number represented by `in_top_p`. Always exempt the last
   `min(8,max(1,in_min_tokens_to_keep_p))` sorted positions. This cannot revive
   a logit already replaced by negative infinity at top-k.
6. Scatter logits back to token order. Select the largest final score, breaking
   ties by smaller index; an all-negative-infinity vector selects token zero.

With eight equal zero logits, temperature one, top-k eight, both minima one
and top-p one-half, the final four positions survive (indices 4..7); token 4
is selected. At top-p one, all eight survive. At top-p zero, only the mandatory
highest sorted position survives. A smaller top-k does not discard equal-score
ties, and increasing either minimum can preserve additional tokens.

## Reset Behavior

`rst` is synchronous active-high and overrides transfers. It discards any
pending response. After its edge, `out_valid=0`, all output payloads are zero,
and `in_ready=1`. No algorithm state survives or accumulates between vectors.
