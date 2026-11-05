# Checked Min-Heap Pop U32 / U64, Capacity 8

This block removes the smallest (value, identifier) pair from a checked min-heap of at most eight entries. The full storage image is returned with an error flag. One accepted input transaction produces exactly one output transaction.

## Interface

### RTL interface

```systemverilog
module data_systems_sort_rank_minheap_pop (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [3:0]   in_k,
    input  logic [31:0]  in_val_0,
    input  logic [31:0]  in_val_1,
    input  logic [31:0]  in_val_2,
    input  logic [31:0]  in_val_3,
    input  logic [31:0]  in_val_4,
    input  logic [31:0]  in_val_5,
    input  logic [31:0]  in_val_6,
    input  logic [31:0]  in_val_7,
    input  logic [63:0]  in_id_0,
    input  logic [63:0]  in_id_1,
    input  logic [63:0]  in_id_2,
    input  logic [63:0]  in_id_3,
    input  logic [63:0]  in_id_4,
    input  logic [63:0]  in_id_5,
    input  logic [63:0]  in_id_6,
    input  logic [63:0]  in_id_7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic         out_error,
    output logic [31:0]  out_val_0,
    output logic [31:0]  out_val_1,
    output logic [31:0]  out_val_2,
    output logic [31:0]  out_val_3,
    output logic [31:0]  out_val_4,
    output logic [31:0]  out_val_5,
    output logic [31:0]  out_val_6,
    output logic [31:0]  out_val_7,
    output logic [63:0]  out_id_0,
    output logic [63:0]  out_id_1,
    output logic [63:0]  out_id_2,
    output logic [63:0]  out_id_3,
    output logic [63:0]  out_id_4,
    output logic [63:0]  out_id_5,
    output logic [63:0]  out_id_6,
    output logic [63:0]  out_id_7
);
```

### Behavior interface

```python
class DataSystemsSortRankMinheapPop:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_k,
        in_val_0, in_val_1, in_val_2, in_val_3,
        in_val_4, in_val_5, in_val_6, in_val_7,
        in_id_0, in_id_1, in_id_2, in_id_3,
        in_id_4, in_id_5, in_id_6, in_id_7,
    ): ...
```

Each `process(...)` call represents one accepted request and returns one
dictionary with exactly seventeen keys: `out_error`, `out_val_0` through
`out_val_7`, and `out_id_0` through `out_id_7`. Arguments and results are unsigned
port bit patterns: `in_k` is 4 bits, values are 32 bits, identifiers are 64 bits,
and `out_error` is 1 bit. The dictionary contains the full storage image even
for an invalid request or a one-entry heap. It is not `None` or a list.

No heap state persists between calls. `reset()` returns `None` without an
output record. Clock cycles, stalls and output consumption are not additional
model calls.

### Transaction protocol

Transfers occur on rising edges with valid and ready both high. Output payloads
remain valid and unchanged under backpressure. The block supports ordered
responses and simultaneous output consumption and input acceptance.

## Modules

- `data_systems_sort_rank_minheap_pop`: top-level transaction operator.

## Functional Behavior

Each value is unsigned 32-bit and each identifier unsigned 64-bit. Compare pairs lexicographically: value first, identifier on equality. The four-bit active length `in_k` must lie in [1,8]. In that prefix, each child pair at zero-based index i>0 must be greater than or equal to its parent at `floor((i-1)/2)`.

For an invalid length or heap ordering, emit `out_error=1` and return all eight pairs unchanged. This checked-error response extends the nonempty source operation; it is not a successful pop of an empty heap.

Otherwise emit `out_error=0` and apply this exact storage update (one-based indexing here):
```text
last = pair[k]
i = 1
while 2*i <= k:
    left = 2*i
    right = left+1
    child = left if right > k or pair[left] < pair[right] else right
    if last < pair[child]: break
    pair[i] = pair[child]
    i = child
pair[i] = last
```
The result's active heap is the first k-1 entries. Return every storage entry as produced by the algorithm, including the former last slot; storage above k is unchanged. If k=1, the active result is empty and the returned image is unchanged. No external state is retained.

## Reset Behavior

Reset is synchronous active-high `rst`. It cancels any pending response, clears `out_valid` and all output payloads to zero. There is no retained algorithmic state. New requests can be accepted after reset is released.
