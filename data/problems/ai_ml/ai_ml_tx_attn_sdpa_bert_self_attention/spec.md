# BERT-style fixed-point self-attention

Two-head self-attention over four four-channel tokens. This specializes BERT eager attention to identity Q/K/V projections and disables dropout; it does not claim to implement learned projections or the entire BERT layer. Positive exponential weighting retains contributions from nonmaximal keys.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_sdpa_bert_self_attention (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_hidden_states,
    input  logic [63:0]  in_attention_mask,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_attn_output
);
```

### Behavior interface

```python
class AiMlTxAttnSdpaBertSelfAttention:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_hidden_states, in_attention_mask): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_attn_output`: 256.

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

Input and output lanes are signed Q8.8: a signed16 word `x` represents `x/256`. `in_hidden_states` and the returned `out_attn_output` use row-major index `4*token+channel`. The mask packs sixteen signed4 **integer** additive logit biases at index `4*query+key`; it is not a Boolean exclusion mask.

Head `h=0,1` uses channels `2h,2h+1`. For each query `q` and key `k`, compute `dot = sum_d x[q,2h+d]*x[k,2h+d]` and Q16 score `s[k] = trunc(dot*46341/65536) + mask[q,k]*65536`. The constant `46341/65536` approximates `1/sqrt(2)`; Q8.8 input products already have sixteen fractional bits.

Set `m=max_k s[k]`, `w[k]=W(m-s[k])`, `S=sum_k w[k]`, and nonnegative Q16 probability `p[k]=floor(w[k]*65536/S)`. The value `65536` is representable and denotes probability one. Output lane `(q,2h+d)` is `trunc(sum_k p[k]*x[k,2h+d]/65536)`. Nonnegative probabilities sum to at most one, so the result fits signed16 without saturation. Equal scores average all four keys (up to the stated quantization), rather than selecting the first key.

### Exponential weights

Scores are signed Q16 values: the stored integer `s` represents `s / 65536`. Subtract the row maximum first, giving a nonnegative distance `d = max_score - score`. Define the nonnegative integer weight `W(d)` as follows (all divisions below are floor divisions of nonnegative integers):

- If `d >= 16 * 65536`, return zero.
- Otherwise set `z = floor(d * 94548 / 65536)`, `n = floor(z / 65536)`, `f = z mod 65536`, `j = floor(f / 2048)`, `r = f mod 2048`.
- For `i = 0..32`, `T[i] = round-to-nearest-even(2^(-i/32) * 2^30)`.
- Return `floor((T[j] - floor((T[j] - T[j+1]) * r / 2048)) / 2^n)`.

Thus `W(0) = 2^30`, weights are monotone nonincreasing, and the maximum always has positive mass. On `0 <= d/65536 < 16`, the absolute error of `W(d)/2^30` versus `exp(-d/65536)` is below `0.0001`; the omitted tail is at most `exp(-16) < 1.13e-7`. This is an exponential approximation, not an argmax or a nonzero tail floor. The formula defines the exact benchmark result; the error bound describes its relationship to real exponential weights.


All intermediate products, sums and divisions use the mathematical integers: there is no internal wraparound. Signed division truncates toward zero; only explicitly mentioned floor divisions or shifts round downward. Packing retains the specified lane width.

## Reset Behavior

Asynchronous active-high reset clears cached state, pending output and output payload, with `in_ready=1` after reset. Stateless blocks have no cache to clear. Idle output payload is zero. A reset discards an unconsumed result and subsequent inputs start a new sequence.
