# Masked Top-k Threshold, Including Ties

Select the top-k threshold of sixteen eligible signed scores. All entries tied
at that threshold survive: the result may contain more than k entries. This
is a filtering operator, not random token selection.

## Interface

### RTL interface

```systemverilog
module ai_ml_sample_filter_sample_topk_mask (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_k,
    input  logic [15:0]  in_logits_0,
    input  logic [15:0]  in_logits_1,
    input  logic [15:0]  in_logits_2,
    input  logic [15:0]  in_logits_3,
    input  logic [15:0]  in_logits_4,
    input  logic [15:0]  in_logits_5,
    input  logic [15:0]  in_logits_6,
    input  logic [15:0]  in_logits_7,
    input  logic [15:0]  in_logits_8,
    input  logic [15:0]  in_logits_9,
    input  logic [15:0]  in_logits_10,
    input  logic [15:0]  in_logits_11,
    input  logic [15:0]  in_logits_12,
    input  logic [15:0]  in_logits_13,
    input  logic [15:0]  in_logits_14,
    input  logic [15:0]  in_logits_15,
    input  logic         in_mask_0,
    input  logic         in_mask_1,
    input  logic         in_mask_2,
    input  logic         in_mask_3,
    input  logic         in_mask_4,
    input  logic         in_mask_5,
    input  logic         in_mask_6,
    input  logic         in_mask_7,
    input  logic         in_mask_8,
    input  logic         in_mask_9,
    input  logic         in_mask_10,
    input  logic         in_mask_11,
    input  logic         in_mask_12,
    input  logic         in_mask_13,
    input  logic         in_mask_14,
    input  logic         in_mask_15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic         out_keep_0,
    output logic         out_keep_1,
    output logic         out_keep_2,
    output logic         out_keep_3,
    output logic         out_keep_4,
    output logic         out_keep_5,
    output logic         out_keep_6,
    output logic         out_keep_7,
    output logic         out_keep_8,
    output logic         out_keep_9,
    output logic         out_keep_10,
    output logic         out_keep_11,
    output logic         out_keep_12,
    output logic         out_keep_13,
    output logic         out_keep_14,
    output logic         out_keep_15
);
endmodule
```

### Behavior interface

```python
class AiMlSampleFilterSampleTopkMask:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_k, in_logits_0, in_logits_1, in_logits_2, in_logits_3, in_logits_4, in_logits_5, in_logits_6, in_logits_7, in_logits_8, in_logits_9, in_logits_10, in_logits_11, in_logits_12, in_logits_13, in_logits_14, in_logits_15, in_mask_0, in_mask_1, in_mask_2, in_mask_3, in_mask_4, in_mask_5, in_mask_6, in_mask_7, in_mask_8, in_mask_9, in_mask_10, in_mask_11, in_mask_12, in_mask_13, in_mask_14, in_mask_15): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_keep_0`, `out_keep_1`, `out_keep_10`, `out_keep_11`, `out_keep_12`,
  `out_keep_13`, `out_keep_14`, `out_keep_15`, `out_keep_2`, `out_keep_3`,
  `out_keep_4`, `out_keep_5`, `out_keep_6`, `out_keep_7`, `out_keep_8`, `out_keep_9`:
  1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

The declared top-level module implements the complete operator below.

## Functional Behavior

Each `in_logits_i` is the two's-complement encoding of a signed 16-bit
integer. `in_mask_i=1` excludes token i. `in_k` is an unsigned 8-bit count.
All bit patterns are defined.

For each eligible token i, count eligible j whose score is **strictly greater**
than i's score. Set `out_keep_i=1` exactly when `in_k>0` and that count is less
than `in_k`. Masked tokens always return zero. Thus k=0 returns an empty mask;
k at least the eligible count retains every eligible token. All equal eligible
scores survive for any positive k; no index tie-break discards them.

The source sampler delegates filtering to vLLM's `TopKTopPSampler`. Its
threshold comparison, also present in the archived Transformers
`TopKLogitsWarper`, is retained. Sixteen integer scores, explicit exclusion bits,
and the k=0 empty-mask extension are task adaptations. This is neither a full
sampler nor a claim about a particular GPU sorting implementation.

## Reset Behavior

Reset is synchronous active-high. At a reset edge, discard pending work and
responses and clear output valid and payload. No transaction is accepted while
reset is asserted. Input and output transfer on a rising edge with their valid
and ready both high. A sender holds valid and all payload bits stable until
transfer. Responses are ordered; consume and accept may occur on the same edge.
Each accepted input produces one response, unless a reset interrupts it.
No particular implementation latency is required.
