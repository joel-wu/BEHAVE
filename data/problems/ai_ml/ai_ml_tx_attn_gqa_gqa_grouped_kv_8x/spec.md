# AI ML TX ATTN GQA GQA Grouped KV 8x

This block performs grouped-query attention key/value head replication with a fixed replication factor of 8. Each accepted input transaction supplies one key vector and one value vector for a single key/value head. The block emits eight output transactions in order, each carrying the same key and value vector and a group-local replica index from 0 through 7. The functional algorithm is the fixed-factor form of grouped-query attention `repeat_kv`, where one KV head is expanded to eight attention heads without changing element values or ordering inside each vector.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_attn_gqa_gqa_grouped_kv_8x #(
    parameter int HEAD_DIM = 16,
    parameter int ELEM_W   = 16
) (
    input  logic                           clk,
    input  logic                           rst,

    input  logic                           in_valid,
    output logic                           in_ready,
    input  logic [HEAD_DIM*ELEM_W-1:0]     in_key,
    input  logic [HEAD_DIM*ELEM_W-1:0]     in_value,

    output logic                           out_valid,
    input  logic                           out_ready,
    output logic [HEAD_DIM*ELEM_W-1:0]     out_key,
    output logic [HEAD_DIM*ELEM_W-1:0]     out_value,
    output logic [2:0]                     out_replica
);
endmodule
```

`in_key` and `in_value` are packed vectors containing `HEAD_DIM` elements of width `ELEM_W`. Element `i` occupies bit slice `[(i+1)*ELEM_W-1 : i*ELEM_W]`. The block treats these fields as uninterpreted bit patterns and copies them bit-exactly.

### Behavior interface

```python
class AiMlTxAttnGqaGqaGroupedKv8x:
    def __init__(self, head_dim=16, elem_w=16):
        pass

    def reset(self):
        pass

    def process(self, in_key, in_value):
        pass
```

`process(...)` returns a list of 8 dicts. Each dict contains keys `out_key`, `out_value`, and `out_replica`.

Return a list of exactly eight dictionaries on every call, with out_replica=0 through
7 in that order. Never return None or a bare dictionary.
Every returned dictionary contains exactly these fields (bit widths):

- `out_key`, `out_value`: HEAD_DIM*ELEM_W.
- `out_replica`: 3.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor head_dim and elem_w match RTL HEAD_DIM and ELEM_W.

## Modules

- `ai_ml_tx_attn_gqa_gqa_grouped_kv_8x`: top-level grouped-KV replication block. No helper modules are required.

## Functional Behavior

For every accepted input transaction, the block consumes one packed key vector `in_key` and one packed value vector `in_value`.

Let `K = in_key` and `V = in_value`.

The block produces exactly 8 output transactions, in this order:

1. output 0: `out_key = K`, `out_value = V`, `out_replica = 3'b000`
2. output 1: `out_key = K`, `out_value = V`, `out_replica = 3'b001`
3. output 2: `out_key = K`, `out_value = V`, `out_replica = 3'b010`
4. output 3: `out_key = K`, `out_value = V`, `out_replica = 3'b011`
5. output 4: `out_key = K`, `out_value = V`, `out_replica = 3'b100`
6. output 5: `out_key = K`, `out_value = V`, `out_replica = 3'b101`
7. output 6: `out_key = K`, `out_value = V`, `out_replica = 3'b110`
8. output 7: `out_key = K`, `out_value = V`, `out_replica = 3'b111`

No arithmetic is performed on key or value elements. There is no transpose, permutation, scaling, masking, saturation, sign interpretation, or format conversion. The block is a pure replication operator.

The output order is replica-major: all eight replicas of one accepted input are emitted before any output derived from a later accepted input.

If the implementation buffers pending outputs internally, the buffered `out_key` and `out_value` for all eight outputs of a transaction shall equal the accepted `in_key` and `in_value` from that transaction, even if later inputs are accepted only after the buffered outputs have drained.

## Reset Behavior

Reset is synchronous active-high `rst`.

On any cycle with `rst == 1`, all architectural state is set to its initial value. The block has no algorithmic state other than any optional internal buffering needed to emit the 8 outputs of an accepted input.

After reset:
- there are no pending output transactions,
- `out_valid = 0`,
- `out_key = 0`,
- `out_value = 0`,
- `out_replica = 0`.

Because there are no pending outputs after reset, the block is able to accept a new input transaction immediately after reset deasserts, subject only to the implementation's chosen buffering for the required 1-to-8 expansion.
