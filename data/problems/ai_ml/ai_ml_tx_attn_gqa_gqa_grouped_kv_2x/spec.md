# ai_ml_tx_attn_gqa_gqa_grouped_kv_2x

This block performs grouped-query attention key/value expansion for the fixed 2× replication case used in GQA. Each accepted input transaction carries one key vector and one value vector for a single key/value head. The block emits two output transactions in order, reproducing the same key and value payload for two attention-head slots belonging to that key/value group. The function is the exact 2× specialization of `repeat_kv(hidden_states, n_rep)` with `n_rep = 2`, preserving data bit patterns without arithmetic modification.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_gqa_gqa_grouped_kv_2x (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_group_id,
    input  logic [15:0]  in_seq_id,
    input  logic [15:0]  in_kv_head_id,
    input  logic [15:0]  in_head_dim,
    input  logic [255:0] in_key,
    input  logic [255:0] in_value,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_group_id,
    output logic [15:0]  out_seq_id,
    output logic [15:0]  out_attention_head_id,
    output logic [15:0]  out_head_dim,
    output logic [255:0] out_key,
    output logic [255:0] out_value
);
```

### Behavior interface

```python
class AiMlTxAttnGqaGqaGroupedKv2x:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_group_id, in_seq_id, in_kv_head_id, in_head_dim, in_key, in_value): ...
```

Each returned dict contains:
- `out_group_id`
- `out_seq_id`
- `out_attention_head_id`
- `out_head_dim`
- `out_key`
- `out_value`

Return a list of exactly two dictionaries on every call, in increasing replica order:
out_attention_head_id=(2*in_kv_head_id) mod 2^16, then (2*in_kv_head_id+1) mod 2^16.
Never return None or a bare dictionary.
Every returned dictionary contains exactly these fields (bit widths):

- `out_attention_head_id`, `out_group_id`, `out_head_dim`, `out_seq_id`: 16.
- `out_key`, `out_value`: 256.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_tx_attn_gqa_gqa_grouped_kv_2x`: top-level grouped-KV expander for 2× replication.

## Functional Behavior

For every accepted input transaction, the block emits exactly two output transactions, in the order listed below.

Input fields:
- `in_group_id` is a 16-bit unsigned metadata field copied to both outputs.
- `in_seq_id` is a 16-bit unsigned metadata field copied to both outputs.
- `in_kv_head_id` is a 16-bit unsigned key/value head index.
- `in_head_dim` is a 16-bit unsigned metadata field copied to both outputs.
- `in_key` is a 256-bit payload copied bit-for-bit to both outputs.
- `in_value` is a 256-bit payload copied bit-for-bit to both outputs.

Output beat 0:
- `out_group_id = in_group_id`
- `out_seq_id = in_seq_id`
- `out_attention_head_id = 2 * in_kv_head_id`
- `out_head_dim = in_head_dim`
- `out_key = in_key`
- `out_value = in_value`

Output beat 1:
- `out_group_id = in_group_id`
- `out_seq_id = in_seq_id`
- `out_attention_head_id = 2 * in_kv_head_id + 1`
- `out_head_dim = in_head_dim`
- `out_key = in_key`
- `out_value = in_value`

All arithmetic on `out_attention_head_id` is unsigned modulo `2^16`. Equivalently:
- beat 0 uses `(in_kv_head_id << 1) & 16'hFFFF`
- beat 1 uses `((in_kv_head_id << 1) + 16'h0001) & 16'hFFFF`

No other arithmetic is performed. `in_key` and `in_value` are not interpreted numerically by this block; they are transported as opaque 256-bit vectors.

This block is a 1:N expander with fixed `N = 2`. One accepted input always produces two outputs, and the outputs must appear in the same order as defined above.

## Reset Behavior

Reset is synchronous active-high. While `rst = 1`, all architectural state is cleared.

Architectural state:
- No persistent computational state is required.

Output port values during reset:
- `out_valid = 0`
- `out_group_id = 16'h0000`
- `out_seq_id = 16'h0000`
- `out_attention_head_id = 16'h0000`
- `out_head_dim = 16'h0000`
- `out_key = 256'h0`
- `out_value = 256'h0`

After reset deassertion, the block is ready to accept input transactions and must continue to implement the two-output mapping defined above.
