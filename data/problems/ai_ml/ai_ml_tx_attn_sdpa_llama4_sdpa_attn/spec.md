# Llama4CachedGroupedQueryAttention

Compute one streaming grouped-query attention token: eight hidden channels,
two four-channel query heads, one shared key/value head, and the eight most
recent tokens. This is a bounded fixed-point NoROPE inference configuration
of Llama4 text attention, without Q/K normalization, dropout or position-based
temperature tuning. Dense projections, masks, softmax, head grouping and
persistent K/V history are active and input-dependent.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_sdpa_llama4_sdpa_attn (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [127:0] in_hidden_states,
    input logic [1023:0] in_q_weight,
    input logic [127:0] in_q_bias,
    input logic [511:0] in_k_weight,
    input logic [63:0] in_k_bias,
    input logic [511:0] in_v_weight,
    input logic [63:0] in_v_bias,
    input logic [1023:0] in_proj_weight,
    input logic [127:0] in_proj_bias,
    input logic [0:0] in_attention_mask_valid,
    input logic [7:0] in_attention_mask,
    input logic [127:0] in_attention_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [127:0] out_attn_output
);
endmodule
```

Numeric lanes are signed 16-bit two's-complement Q8.8; lane zero is in the
least significant bits. Hidden input and output are `[8]`. Q/output weights
are row-major `[8][8]`; K/V weights are `[4][8]`, output-channel first.
Each projection bias has the corresponding output width. The mask has one
bit per retained history slot, and attention bias contains eight Q8.8 lanes.
Slots are ordered oldest to newest **after** appending the current token.
All input words are legal; parameter inputs are sampled on acceptance and
may differ between tokens. Cached entries retain their already projected
values, even when later transactions provide different weights.

Both channels use rising-edge ready/valid transfers outside reset. Each accepted
token produces one ordered output vector. Valid and payload stay stable while
stalled. No fixed latency is required. Mask and weights are part of the token,
not independently clocked configuration writes.

### Behavior interface

```python
class Llama4SdpaAttn:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_hidden_states, in_q_weight, in_q_bias, in_k_weight,
                in_k_bias, in_v_weight, in_v_bias, in_proj_weight, in_proj_bias,
                in_attention_mask_valid, in_attention_mask, in_attention_bias): ...
```

Bus arguments and `{"out_attn_output": packed_vector}` use unsigned integers.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_attn_output`: 128.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_tx_attn_sdpa_llama4_sdpa_attn`: cached two-query-head attention.

## Functional Behavior

All equations below operate on signed lane integers with exact intermediate
arithmetic. `floor` rounds toward negative infinity; `sat16` clips to
`[-32768,32767]`. For a projection with `D` outputs, define
`project(X,W,b)[i]=sat16(floor((256*b[i]+sum_d X[d]*W[i,d])/256))`, `d=0..7`.

1. Project the current hidden vector to eight Q lanes and four K and V lanes.
   Append the new K/V pair. If eight pairs were already stored, first evict
   the oldest. The current token participates in its own attention; future
   tokens do not. History length grows from zero to eight.
2. Query head `h` uses `Q[4*h:4*h+4]`. **Both heads use the same** four-channel
   K/V history. For retained slot `j`, form
   `S[h,j]=floor(sum_d Q[4*h+d]*K[j,d]/512)`, `d=0..3`.
   This includes the exact `1/sqrt(4)` head scale.
3. If mask-valid is one, add attention-bias lane `j` to `S[h,j]` and exclude
   slot `j` when mask bit `j` is one. Otherwise ignore both mask and bias.
   Bits/lanes beyond the retained length have no effect.
4. For each head, subtract the maximal eligible score `m`. For an eligible
   score `s`, let `g=floor((m-s+8)/16)`; its integer weight is
   `floor(65536*exp(-g/16)+1/2)` for `0<=g<=128`, otherwise zero.
   `exp` is natural exponential. This is a nearest-1/16 logit grid with a
   rounded Q16 exponential table and finite tail cutoff; floating-point
   bit equivalence is not promised. Masked slots have weight zero.
5. Each context lane is `floor(sum_j weight[j]*V[j,d]/sum_j weight[j])`.
   If no eligible slot exists, that head context is zero. Concatenate both
   heads and apply the supplied eight-output projection and bias. Pack the
   result. Masking every slot therefore returns the output-projection bias.

A zero Q projection with no masks/bias yields uniform attention over retained
values. With identity output projection, both heads return the same floored
mean of the four-channel V history. At the ninth token only tokens 2..9
contribute. Distinct Q heads or additive biases can produce different weights.
Masking does not cancel the cache append: an excluded token remains available
to subsequent transactions until eviction or reset.

## Reset Behavior

`rst` is synchronous active-high and takes priority over transfers. It clears
the K/V cache and its length, abandons in-flight work and pending output, and
sets `out_valid=0`, `out_attn_output=0`, `in_ready=1`. A subsequent token sees
only itself.
