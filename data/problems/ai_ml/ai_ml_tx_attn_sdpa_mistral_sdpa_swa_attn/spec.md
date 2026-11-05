# Sliding-window grouped-query attention

Mistral-style grouped-query attention with an oldest-first sliding KV window. Q/K/V are supplied after projection and rotary embedding; this block preserves cache evolution, head sharing and scaled exponential attention, not a complete decoder layer.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_sdpa_mistral_sdpa_swa_attn #(
    parameter int HEAD_DIM = 4,
    parameter int NUM_ATTENTION_HEADS = 4,
    parameter int NUM_KEY_VALUE_HEADS = 2,
    parameter int SLIDING_WINDOW = 8,
    parameter int ELEM_W = 32
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [NUM_ATTENTION_HEADS*HEAD_DIM*ELEM_W-1:0] in_query,
    input  logic [NUM_KEY_VALUE_HEADS*HEAD_DIM*ELEM_W-1:0] in_key,
    input  logic [NUM_KEY_VALUE_HEADS*HEAD_DIM*ELEM_W-1:0] in_value,

    output logic out_valid,
    input  logic out_ready,
    output logic [NUM_ATTENTION_HEADS*HEAD_DIM*ELEM_W-1:0] out_attn
);
```

### Behavior interface

```python
class ai_ml_tx_attn_sdpa_mistral_sdpa_swa_attn:
    def __init__(self, HEAD_DIM=4, NUM_ATTENTION_HEADS=4, NUM_KEY_VALUE_HEADS=2, SLIDING_WINDOW=8, ELEM_W=32): ...
    def reset(self): ...
    def process(self, in_query, in_key, in_value): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_attn`: NUM_ATTENTION_HEADS*HEAD_DIM*ELEM_W.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor parameters have the same names and values as the RTL specialization.

### Data representation and protocol

Packed lane index 0 occupies the least-significant bits. Each accepted input produces one ordered output record. Transfer is sampled on a rising clock edge with valid and ready both high. Pending output valid and payload remain stable under backpressure, and state changes only on an accepted input or reset. Circuit architecture and latency are unrestricted within the task's execution constraints.

## Modules

The named top module implements the complete task; no external memory or hidden model dependencies are required.

## Functional Behavior

The positive static dimensions use their declared defaults unless overridden; `ELEM_W=32` and `NUM_ATTENTION_HEADS` must be a multiple of `NUM_KEY_VALUE_HEADS`. A signed32 input/output word `v` represents Q16.16 value `v/65536`. Query lane `h*HEAD_DIM+d` maps to KV head `floor(h/(NUM_ATTENTION_HEADS/NUM_KEY_VALUE_HEADS))`; KV lane index is `kv_head*HEAD_DIM+d`.

The cache holds at most `SLIDING_WINDOW` tokens, oldest first. Append the new token; if full, evict exactly the oldest token. The current output attends to this updated window, including the new token. For each query head and cached token, form the exact signed dot product. Let `R=floor(sqrt(HEAD_DIM)*65536)`, and set Q16 score `s=trunc(dot/R)`. This combines conversion of Q32 products to Q16 and the source's `1/sqrt(HEAD_DIM)` factor; for `HEAD_DIM=4`, `R=131072` exactly. Root quantization changes the scale by less than `1/65536` relatively, followed by less than one Q16 unit of truncation.

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
