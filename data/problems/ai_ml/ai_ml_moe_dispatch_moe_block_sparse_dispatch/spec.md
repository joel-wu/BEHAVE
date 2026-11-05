# MoE Block Sparse Dispatch

Generate the stable routing permutation and complete block-sparse topology for eight tokens, two routes per token, four experts, and an expert FFN width of 256. Each nonempty expert is padded independently to a 128-token block. Consequently there can be four block rows, even though there are only sixteen unpadded routes. This is an index-generation task, not expert computation.

## Interface

### RTL interface

```systemverilog
module ai_ml_moe_dispatch_moe_block_sparse_dispatch #(
    parameter int NUM_TOKENS = 8,
    parameter int NUM_EXPERTS = 4,
    parameter int TOP_K = 2,
    parameter int FFN_HIDDEN_SIZE = 256
) (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [31:0] in_top_experts,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_indices,
    output logic [31:0] out_bin_ids,
    output logic [19:0] out_bins,
    output logic [39:0] out_padded_bins,
    output logic [19:0] out_tokens_per_expert,
    output logic [2:0] out_block_rows,
    output logic [19:0] out_offsets,
    output logic [23:0] out_column_indices,
    output logic [15:0] out_row_indices,
    output logic [15:0] out_column_indices_t,
    output logic [35:0] out_offsets_t,
    output logic [23:0] out_block_offsets_t
);
```

Only the listed default parameter values are supported. Input field `i` occupies bits `[2*i +: 2]` and gives the expert for route `i`; all values 0..3 are legal. Every packed array below places element zero in its least-significant field.

### Behavior interface

```python
class MoEBlockSparseDispatch:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_top_experts): ...
```

`process` takes an unsigned 32-bit integer and returns one dictionary with all twelve output names shown above. Each output is an unsigned integer containing its packed fields.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_bin_ids`: 32.
- `out_bins`, `out_offsets`, `out_tokens_per_expert`: 20.
- `out_block_offsets_t`, `out_column_indices`: 24.
- `out_block_rows`: 3.
- `out_column_indices_t`, `out_row_indices`: 16.
- `out_indices`: 64.
- `out_offsets_t`: 36.
- `out_padded_bins`: 40.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_moe_dispatch_moe_block_sparse_dispatch`: stateless routing/topology calculation and a response holding register.

## Functional Behavior

Stable-sort routes by ascending expert, preserving original route order within ties. Let `indices[j]` be the original route index and `bin_ids[j]` its expert after sorting. For expert `e`, let `count[e]` be its route count, `bins[e] = sum(count[0:e+1])`, and `padded[e] = 128 * count_nonzero(count[0:e+1])`.

Enumerate nonempty experts in ascending order as `active[r]`. The number of block rows `B = len(active)` lies in 1..4; the number of nonzero blocks is `K = 2*B`. Block row `r` has columns `2*active[r]` and `2*active[r]+1`, each representing 128 FFN features. The sparse block matrix has `B` rows and eight columns.

| Output | Fields | Meaning |
|---|---:|---|
| `out_indices` | 16 × 4 bits | Stable route permutation |
| `out_bin_ids` | 16 × 2 bits | Sorted expert IDs |
| `out_tokens_per_expert` | 4 × 5 bits | `count[e]` |
| `out_bins` | 4 × 5 bits | Inclusive unpadded boundaries, final value 16 |
| `out_padded_bins` | 4 × 10 bits | Inclusive padded boundaries, final value `128*B` |
| `out_block_rows` | 3 bits | `B` |
| `out_offsets` | 5 × 4 bits | CSR offsets: entry `r` is `2*min(r,B)`, including the end sentinel |
| `out_column_indices` | 8 × 3 bits | CSR block columns; the first `K` entries are valid |
| `out_row_indices` | 8 × 2 bits | Row of each CSR block; first `K` entries valid |
| `out_column_indices_t` | 8 × 2 bits | Original row indices in transpose order |
| `out_offsets_t` | 9 × 4 bits | Transpose CSR offsets, including both initial and final sentinels |
| `out_block_offsets_t` | 8 × 3 bits | Original CSR entry index for each transposed entry |

CSR entries are in row order, then column order. Transpose entries are a stable sort of the valid CSR entries by column. `offsets_t[c]` counts valid entries with original column less than `c`, for `c=0..8`. Unused slots in all block-entry arrays are zero. All five row-offset fields and all nine transpose-offset fields are meaningful, including repeated end sentinels; no counts wrap.

For routes cycling `0,1,2,3` four times, counts are `[4,4,4,4]`, padded bins are `[128,256,384,512]`, `B=4`, row offsets are `[0,2,4,6,8]`, columns are `[0,1,2,3,4,5,6,7]`, and rows are `[0,0,1,1,2,2,3,3]`. For routes all assigned to expert 2, `B=1`, padded bins are `[0,0,128,128]`, valid columns are `[4,5]`, and transpose offsets are `[0,0,0,0,0,1,2,2,2]`.

A request transfers on a rising clock edge with `in_valid && in_ready`. It produces one ordered response, transferred with `out_valid && out_ready`. Hold valid and payload stable while stalled. Latency is implementation-defined within the task's run bounds; no fixed computation cycle is part of the function.

## Reset Behavior

Reset is asynchronous active-high. It clears output valid and every stored output field and cancels any pending response. There is no persistent arithmetic state. Input ready is high after reset when no response is pending.
