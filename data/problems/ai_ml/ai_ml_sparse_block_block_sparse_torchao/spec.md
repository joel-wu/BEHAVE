# AI ML Sparse Block Block Sparse TorchAO

This module performs one block-sparse matrix multiply using fixed block size 2×2. The sparse weight matrix is provided in BSR form by `crow_indices`, `col_indices`, and `values`; the dense input matrix is provided by `x`. On each accepted input transaction, the module reconstructs the dense weight matrix implied by the BSR data, computes `y = W × x`, and returns the dense result matrix. All arithmetic is signed 8-bit by 8-bit multiply with accumulation in signed 32-bit and final wrap-to-8-bit truncation per output element.

## Interface

### RTL interface

```systemverilog
module ai_ml_sparse_block_block_sparse_torchao (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [2:0]   in_M,
    input  logic [2:0]   in_K,
    input  logic [2:0]   in_N,
    input  logic [2:0]   in_num_block_rows,
    input  logic [2:0]   in_nnz_blocks,
    input  logic [39:0]  in_crow_indices,
    input  logic [23:0]  in_col_indices,
    input  logic [255:0] in_values,
    input  logic [511:0] in_x,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [511:0] out_y
);
```

### Behavior interface

```python
class AiMlSparseBlockBlockSparseTorchao:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_M,
        in_K,
        in_N,
        in_num_block_rows,
        in_nnz_blocks,
        in_crow_indices,
        in_col_indices,
        in_values,
        in_x,
    ): ...
```

`process(...)` returns one dict with key `out_y`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 512.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_sparse_block_block_sparse_torchao`: top-level block-sparse matrix multiply with fixed 2×2 BSR blocks and 8×8 maximum matrix envelope.

## Functional Behavior

The design is a 1:1 transaction mapper: each accepted input transaction produces exactly one output transaction.

All matrix storage is bounded to an 8×8 envelope. `in_M`, `in_K`, and `in_N` are unsigned 3-bit values in the range 0 to 4 inclusive (values 5..7 are out-of-domain and produce all-zero outputs) and define the active output-row count, reduction dimension, and output-column count respectively. `in_num_block_rows` is an unsigned 3-bit value in the range 0 to 2 inclusive. `in_nnz_blocks` is an unsigned 3-bit value in the range 0 to 4 inclusive.

The BSR block size is fixed:
- block rows have height 2
- block columns have width 2

`in_crow_indices` contains five packed 8-bit unsigned entries:
- `crow[0] = in_crow_indices[7:0]`
- `crow[1] = in_crow_indices[15:8]`
- `crow[2] = in_crow_indices[23:16]`
- `crow[3] = in_crow_indices[31:24]`
- `crow[4] = in_crow_indices[39:32]`

`in_col_indices` contains eight packed 3-bit unsigned entries:
- for `p = 0..7`, `col[p] = (in_col_indices >> (3*p)) & 7`

`in_values` contains eight packed 2×2 signed-int8 blocks. For block `p = 0..7`, bytes are packed in row-major order:
- `v[p][0][0] = signed(in_values[32*p +  7 : 32*p +  0])`
- `v[p][0][1] = signed(in_values[32*p + 15 : 32*p +  8])`
- `v[p][1][0] = signed(in_values[32*p + 23 : 32*p + 16])`
- `v[p][1][1] = signed(in_values[32*p + 31 : 32*p + 24])`

`in_x` contains an 8×8 dense signed-int8 matrix `x[k][n]`, packed in row-major order over rows `k = 0..7` and columns `n = 0..7`:
- `x[k][n] = signed(in_x[8*(8*k + n) + 7 : 8*(8*k + n)])`

The computation proceeds in two stages.

1. Dense weight reconstruction

Create an 8×8 signed-int8 matrix `W`, initialised to zero in every element.

Only block rows `br` with `0 <= br < in_num_block_rows` participate. For each such `br`, consider block positions `p` satisfying:
- `crow[br] <= p`
- `p < crow[br + 1]`
- `p < in_nnz_blocks`

Each participating `p` selects one 2×2 block destination in `W`:
- destination row base = `2 * br`
- destination column base = `2 * col[p]`

A participating block is written into `W` only when `col[p]` is 0, 1, 2, or 3. If `col[p] >= 4`, that block is ignored and makes no change to `W`.

For `col[p]` in 0..3, write:
- `W[2*br + 0][2*col[p] + 0] = v[p][0][0]`
- `W[2*br + 0][2*col[p] + 1] = v[p][0][1]`
- `W[2*br + 1][2*col[p] + 0] = v[p][1][0]`
- `W[2*br + 1][2*col[p] + 1] = v[p][1][1]`

If multiple participating blocks target the same destination entries, the block with the largest `p` overwrites earlier blocks. This is the required conflict-resolution rule because writes are applied in increasing `p` order.

No other entries of `W` are modified.

2. Matrix multiply and output packing

Create an 8×8 signed 32-bit accumulation matrix `y32`, initialised to zero.

For each output row `m = 0..7` and output column `n = 0..7`:
- if `m >= in_M` or `n >= in_N`, `y32[m][n]` remains zero
- otherwise
  - `y32[m][n] = Σ_{k=0}^{in_K-1} ( W[m][k] * x[k][n] )`

Each product uses signed 8-bit operands and signed multiplication. The sum is accumulated in signed 32-bit with wrap modulo 2^32 after each addition; equivalently, `y32[m][n]` is the two's-complement 32-bit result of the sum.

The final output matrix `y8` is produced by taking the least-significant 8 bits of each `y32[m][n]`:
- `y8[m][n] = y32[m][n] mod 256`

This is wrap, not saturation. Interpreted as signed 8-bit, `0x80..0xFF` represent negative values.

`out_y` packs `y8[m][n]` in the same row-major layout used by `in_x`:
- bits `8*(8*m + n) +: 8` carry `y8[m][n]`
- all 64 byte positions are always present
- positions with `m >= in_M` or `n >= in_N` are zero because their accumulators remain zero

Worked example for one block: if `br = 1`, `p = 2`, `col[2] = 3`, and the block bytes are `[a, b, c, d]`, then the write targets are:
- `W[2][6] = a`
- `W[2][7] = b`
- `W[3][6] = c`
- `W[3][7] = d`

Worked example for truncation: if one accumulator equals decimal 300, its output byte is `300 mod 256 = 44 = 0x2C`. If one accumulator equals decimal -1, its output byte is `0xFF`.

## Reset Behavior

The reset is synchronous active-high.

Architectural state:
- no persistent algorithmic state is required between transactions

Output port state after reset:
- `out_valid = 0`
- `out_y = 512'b0`

The module may contain internal handshake state to hold one completed output while stalled by `out_ready = 0`. That state shall reset to idle, with no pending output transaction.
