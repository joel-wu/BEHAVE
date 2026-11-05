# AI ML TX KV Cache KV Cache Paged RR

This block performs one paged key/value cache write-and-read transaction for transformer attention state. Each request supplies a token's key and value vectors, a logical write slot and a separate read address. The slot selects a cache block and offset within that block. The request writes every head and element of that token, then returns the addressed key/value cell from the updated cache. State persists between requests. This is direct slot mapping, not round-robin allocation or an attention computation; `paged_rr` is a legacy task identifier.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_kv_cache_kv_cache_paged_rr #(
    parameter int NUM_BLOCKS    = 2,
    parameter int NUM_KV_HEADS  = 2,
    parameter int HEAD_SIZE     = 4,
    parameter int BLOCK_SIZE    = 2,
    parameter int ELEM_WIDTH    = 16,
    parameter int SLOT_WIDTH    = 32
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [SLOT_WIDTH-1:0] in_slot_mapping,
    input  logic [NUM_KV_HEADS*HEAD_SIZE*ELEM_WIDTH-1:0] in_key,
    input  logic [NUM_KV_HEADS*HEAD_SIZE*ELEM_WIDTH-1:0] in_value,

    input  logic [0:0] in_read_block,
    input  logic [0:0] in_read_head,
    input  logic [1:0] in_read_elem,
    input  logic [0:0] in_read_slot,

    output logic out_valid,
    input  logic out_ready,
    output logic out_ok,
    output logic [ELEM_WIDTH-1:0] out_key_elem,
    output logic [ELEM_WIDTH-1:0] out_value_elem
);
```

`in_key` and `in_value` are packed in head-major, element-minor order. Element `(h, e)` occupies bits `((h*HEAD_SIZE + e)*ELEM_WIDTH) +: ELEM_WIDTH`, with `h` in `[0, NUM_KV_HEADS-1]` and `e` in `[0, HEAD_SIZE-1]`.

### Behavior interface

```python
class AiMlTxKvCacheKvCachePagedRr:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_slot_mapping, in_key, in_value,
                in_read_block, in_read_head, in_read_elem, in_read_slot):
        pass
```

The returned dict has three keys: `out_ok` (write-in-range status), `out_key_elem` (`ELEM_WIDTH` bits at the addressed read location, post-bypass), `out_value_elem` (same for the value cache).

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_key_elem`, `out_value_elem`: ELEM_WIDTH.
- `out_ok`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Parameter domain

The task fixes the following architectural configuration.
The RTL parameter names above document this configuration; other overrides
are outside this task's contract.

- `NUM_BLOCKS = 2`
- `NUM_KV_HEADS = 2`
- `HEAD_SIZE = 4`
- `BLOCK_SIZE = 2`
- `ELEM_WIDTH = 16`
- `SLOT_WIDTH = 32`

There are 32 key cells and 32 value cells. Each packed input vector is
128 bits. The read address widths are 1, 1, 2 and 1 bits for block, head,
element and slot, respectively; every encoded read address is valid.

## Modules

- `ai_ml_tx_kv_cache_kv_cache_paged_rr`: top module and complete implementation.

## Functional Behavior

The block contains two internal memories:

- `key_cache[NUM_BLOCKS][NUM_KV_HEADS][HEAD_SIZE][BLOCK_SIZE]`
- `value_cache[NUM_BLOCKS][NUM_KV_HEADS][HEAD_SIZE][BLOCK_SIZE]`

Every cache element is an unsigned `ELEM_WIDTH`-bit stored bit pattern. The block does not interpret the element format; integer, fixed-point, or floating-point encodings are stored transparently.

For each accepted input transaction:

1. Let `slot = in_slot_mapping` interpreted as an unsigned `SLOT_WIDTH`-bit integer.
2. Let `block_index = floor(slot / BLOCK_SIZE)`.
3. Let `slot_index = slot - block_index * BLOCK_SIZE`. Equivalently, `slot % BLOCK_SIZE`.
4. If `block_index >= NUM_BLOCKS`, no cache location is modified and the block returns `out_ok = 0`.
5. Otherwise, for every KV head `h` from `0` to `NUM_KV_HEADS-1` and every head element `e` from `0` to `HEAD_SIZE-1`:
   - Extract `key_elem(h,e)` from `in_key` bits `((h*HEAD_SIZE + e)*ELEM_WIDTH) +: ELEM_WIDTH`.
   - Extract `value_elem(h,e)` from `in_value` bits `((h*HEAD_SIZE + e)*ELEM_WIDTH) +: ELEM_WIDTH`.
   - Write `key_cache[block_index][h][e][slot_index] = key_elem(h,e)`.
   - Write `value_cache[block_index][h][e][slot_index] = value_elem(h,e)`.
   - All writes of one transaction are logically simultaneous.
6. For an in-range write, the block returns `out_ok = 1`.
7. Return `out_key_elem = key_cache[in_read_block][in_read_head][in_read_elem][in_read_slot]`
   and the corresponding `value_cache` cell, after the write above. Reading
   the written block/slot observes the new token, including the selected head
   and element. Reading another block/slot observes its previously stored value.

Thus write slots 0 and 1 belong to block 0; slots 2 and 3 belong to block 1.
Any 32-bit write slot of 4 or greater leaves both caches unchanged, reports
`out_ok=0`, and still returns the addressed existing cell. This provides a
read-only request. No address wraps into a valid write slot.

Output cardinality is 1:1: every accepted input transaction produces exactly one output transaction.

Arithmetic rules:

- `slot`, `block_index`, and `slot_index` are non-negative integers.
- Division in step 2 is exact integer floor division.
- No signed interpretation is used anywhere in address calculation.
- Stored cache values are truncated to `ELEM_WIDTH` bits before writing.
- Input vectors wider than required by the packed field definitions are ignored above bit `NUM_KV_HEADS*HEAD_SIZE*ELEM_WIDTH - 1`.

For example, write distinct tokens to slots 1 and 2, then issue an invalid
write slot while reading `(block=0, head=1, elem=3, slot=1)`. The response
returns element 7 of the token stored at slot 1, not the token at slot 2.
Overwriting slot 2 must not change that result. Key and value payloads are
stored independently; all 16-bit patterns are valid.

An input is accepted only on a rising edge with `in_valid && in_ready`; an
output transfers on a rising edge with `out_valid && out_ready`. The sender
holds valid and its payload until acceptance. A pending response and all its
fields remain stable while the receiver is stalled. Requests take effect in
acceptance order and produce exactly one ordered response each. An unaccepted
request must not modify cache state. Latency and internal storage layout are
implementation choices, subject to the run's declared limits.

## Reset Behavior

`rst` is active-high and asynchronous. Reset clears both caches and any
pending response; a canceled response must not appear after reset. Inputs
presented during reset are not accepted. While reset is asserted:

- Every element of `key_cache` is `0`.
- Every element of `value_cache` is `0`.

- `out_valid`, `out_ok`, `out_key_elem` and `out_value_elem` are zero.

After reset, an invalid-write/read-only request returns zero from every
address until that cell is written. Whenever `out_valid=0`, the three output
payload fields are zero. Stalled valid outputs are not cleared.
