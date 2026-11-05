# RepeatKVGroupedQueryAttention

Expand a labeled KV tensor element into the attention heads that share it in grouped-query attention.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_gqa_repeat_kv_gqa (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_data,
    input  logic [15:0] in_batch_idx,
    input  logic [15:0] in_kv_head_idx,
    input  logic [15:0] in_seq_idx,
    input  logic [15:0] in_head_dim_idx,
    input  logic [3:0]  in_n_rep,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_data,
    output logic [15:0] out_batch_idx,
    output logic [19:0] out_attn_head_idx,
    output logic [15:0] out_seq_idx,
    output logic [15:0] out_head_dim_idx
);
```

### Behavior interface

```python
class AiMlTxAttnGqaRepeatKvGqa:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_data,
        in_batch_idx,
        in_kv_head_idx,
        in_seq_idx,
        in_head_dim_idx,
        in_n_rep,
    ): ...
```

The Behavior arguments and returned fields are raw unsigned pin bit patterns; signed interpretation is specified below. One process call represents one accepted input. The output sequence is specified below.

For in_n_rep=0, return None or an empty list; both represent zero outputs. Otherwise
return a list of exactly in_n_rep dictionaries in replica order r=0 through
in_n_rep-1. Never return a bare dictionary. Every record has
out_attn_head_idx=in_kv_head_idx*in_n_rep+r.
Every returned dictionary contains exactly these fields (bit widths):

- `out_attn_head_idx`: 20.
- `out_batch_idx`, `out_head_dim_idx`, `out_seq_idx`: 16.
- `out_data`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_tx_attn_gqa_repeat_kv_gqa`: the complete operator and its input/output handshake.

## Functional Behavior

One accepted input labels a binary32 payload (copied bit-for-bit, without arithmetic) by batch, KV head, sequence position and head-dimension position. The four-bit in_n_rep is an unsigned repetition count 0..15.

Emit in_n_rep records, in increasing r=0..in_n_rep-1, with

    out_attn_head_idx = in_kv_head_idx * in_n_rep + r.

Copy data, batch, sequence and head-dimension fields unchanged. The 20-bit head index represents the complete result, whose maximum is 983039; it never wraps at 16 bits. Count 0 emits no records; count 1 emits one unchanged head index. Counts 4 and 8 are ordinary supported cases. Finish one input's records before emitting the next input's records.

This is a coordinate-labeled element stream, not a claim that its emission order is contiguous tensor-memory order. Reassembling labels implements (batch,KV_heads,sequence,dimension) -> (batch,KV_heads*n_rep,sequence,dimension). For KV head 65535 and count 4, output head indices are 262140,262141,262142,262143.

Input transfers occur on a rising clock edge when in_valid and in_ready are both 1; outputs transfer when out_valid and out_ready are both 1. The source holds every input field and in_valid until acceptance. The design holds every output field and out_valid until consumption. Each accepted input produces exactly in_n_rep ordered output transactions. No fixed latency or internal pipeline is required. With reset inactive and the sink accepting outputs, pending work must eventually complete. Reset cancels pending work.

## Reset Behavior

Synchronous, active-high rst. Reset clears out_valid and visible output payloads to zero and restores an input-ready empty state. Reset discards any remaining replicas of a pending input.
