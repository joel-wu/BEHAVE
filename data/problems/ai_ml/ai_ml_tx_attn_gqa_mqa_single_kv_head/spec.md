# Single-KV-head causal attention

Falcon-style multi-query attention with a single shared KV head and a four-token prefix cache. Q/K/V are supplied after projection and position processing. Cache truncation/overwrite is a bounded hardware adaptation; softmax weighting is preserved.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_gqa_mqa_single_kv_head #(
    parameter int NUM_HEADS = 2,
    parameter int HEAD_DIM = 4,
    parameter int MAX_SEQ_LEN = 4,
    parameter int ELEM_W = 16,
    parameter int ACC_W = 64,
    parameter int PROB_W = 16,
    parameter int SEQ_LEN_W = 3
) (
    input  logic                                 clk,
    input  logic                                 rst,

    input  logic                                 in_valid,
    output logic                                 in_ready,
    input  logic [SEQ_LEN_W-1:0]                 in_valid_length,
    input  logic [NUM_HEADS*HEAD_DIM*ELEM_W-1:0] in_query,
    input  logic [HEAD_DIM*ELEM_W-1:0]           in_key,
    input  logic [HEAD_DIM*ELEM_W-1:0]           in_value,

    output logic                                 out_valid,
    input  logic                                 out_ready,
    output logic [NUM_HEADS*HEAD_DIM*ELEM_W-1:0] out_context
);
```

### Behavior interface

```python
class AiMlTxAttnGqaMqaSingleKvHead:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_valid_length, in_query, in_key, in_value): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_context`: NUM_HEADS*HEAD_DIM*ELEM_W.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation and protocol

Packed lane index 0 occupies the least-significant bits. Each accepted input produces one ordered output record. Transfer is sampled on a rising clock edge with valid and ready both high. Pending output valid and payload remain stable under backpressure, and state changes only on an accepted input or reset. Circuit architecture and latency are unrestricted within the task's execution constraints.

## Modules

The named top module implements the complete task; no external memory or hidden model dependencies are required.

## Functional Behavior

This task retains the fixed architecture `NUM_HEADS=2, HEAD_DIM=4, MAX_SEQ_LEN=4, ELEM_W=16, ACC_W=64, PROB_W=16, SEQ_LEN_W=3`; the HDL parameter names do not advertise other configurations. `PROB_W` is retained for interface compatibility, but no signed Q15 probability is constructed. Query/output index is `h*4+d`, KV index is `d`. All data lanes are signed Q8.8, representing the signed16 integer divided by 256.

Let `p=min(unsigned(in_valid_length), cache_len, 3)`. Retain the old prefix before `p`, write the new KV at `p`, and set `cache_len=p+1`. Attention includes precisely positions `0..p`; positions beyond the new length are inaccessible until overwritten by later appends. Thus length zero restarts the prefix; a full length-four cache replaces its last token, not its oldest one. All query heads share this KV cache. For each head/token, the Q16 score is `trunc(sum_d q[h,d]*k[t,d]/2)`, since `sqrt(HEAD_DIM)=2` and Q8.8 products are already Q16.

For each head set `m=max_t s[t]`, `w[t]=W(m-s[t])` and `S=sum_t w[t]`. Output lane `d` is `trunc(sum_t w[t]*v[t,d]/S)`. Division normalizes the weights directly: a one-token cache returns that token's value exactly, with no negative representation of probability one. Ties average all eligible values. Values remain inside their signed lane range by convexity. The updated cache is committed only when the input transfers, independently of when its output transfers.

### Exponential weights

Scores are signed Q16 values: the stored integer `s` represents `s / 65536`. Subtract the row maximum first, giving a nonnegative distance `d = max_score - score`. Define the nonnegative integer weight `W(d)` as follows (all divisions below are floor divisions of nonnegative integers):

- If `d >= 16 * 65536`, return zero.
- Otherwise set `z = floor(d * 94548 / 65536)`, `n = floor(z / 65536)`, `f = z mod 65536`, `j = floor(f / 2048)`, `r = f mod 2048`.
- For `i = 0..32`, `T[i] = round-to-nearest-even(2^(-i/32) * 2^30)`.
- Return `floor((T[j] - floor((T[j] - T[j+1]) * r / 2048)) / 2^n)`.

Thus `W(0) = 2^30`, weights are monotone nonincreasing, and the maximum always has positive mass. On `0 <= d/65536 < 16`, the absolute error of `W(d)/2^30` versus `exp(-d/65536)` is below `0.0001`; the omitted tail is at most `exp(-16) < 1.13e-7`. This is an exponential approximation, not an argmax or a nonzero tail floor. The formula defines the exact benchmark result; the error bound describes its relationship to real exponential weights.


All intermediate products, sums and divisions use the mathematical integers: there is no internal wraparound. Signed division truncates toward zero; only explicitly mentioned floor divisions or shifts round downward. Packing retains the specified lane width.

## Reset Behavior

Synchronous active-high reset clears cached state, pending output and output payload, with `in_ready=1` after reset. Stateless blocks have no cache to clear. Idle output payload is zero. A reset discards an unconsumed result and subsequent inputs start a new sequence.
