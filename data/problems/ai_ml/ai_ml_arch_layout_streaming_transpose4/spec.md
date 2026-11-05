# Streaming 4x4 Tile Transposer

This block converts row-major tiles into column-major streams for a matrix
accelerator. It collects four rows of four 16-bit elements and emits the four
columns in order. Tiles are independent, and backpressure must not change their
contents or ordering. The elements are bit patterns; no arithmetic is performed.

## Interface

### RTL interface

```systemverilog
module streaming_transpose4 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [63:0] in_row,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_col
);
```

### Behavior interface

```python
class StreamingTranspose4:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_row): ...
```

Call `process` once per accepted row, with an unsigned 64-bit integer. Reuse the
instance across calls. It returns an ordered list of dictionaries: `[]` for
the first three rows of each tile, then exactly four dictionaries on the fourth
row, each with the sole key `out_col` and an unsigned 64-bit value. Never return
`None`, a bare dictionary, or a packed four-column record. The four list entries
represent four separate output transactions, not four simultaneous ports.
`reset()` discards a partial tile, restores the row index to zero, returns
`None`, and emits no records. Physical idle and output-stall cycles do not call
`process`. There are no constructor parameters or external memories.

### Transaction and timing behavior

Inputs transfer on rising clock edges with `in_valid && in_ready`, outside
reset. Outputs transfer on rising edges with `out_valid && out_ready`. A sender
holds a pending valid offer and its payload until transfer or reset.

The block alternates between collecting four rows and emitting four columns.
It cannot accept a row of the next tile until the fourth output of the current
tile has transferred. It does not accept an input on that final-output edge;
the next rising edge may accept the next tile's first row. No output is offered
before the fourth row is accepted. With input rows available, the collection
phase accepts one row per cycle. The first column is offered after the fourth
input edge, and each output transfer advances to the next column. Output valid
and payload remain stable under backpressure. During collection, `out_valid=0`
and `out_col=0`. During emission, `in_ready=0`.

## Modules

`streaming_transpose4` implements the complete fixed-size tile transposer.

## Functional Behavior

For each tile, number accepted input rows `r=0,1,2,3`. Element `A[r,c]` is
`(in_row[r] >> (16*c)) & 0xFFFF`, for `c=0,1,2,3`. Lane zero occupies the least
significant 16 bits. Emit columns `c=0,1,2,3`, with

`out_col[c] = sum(A[r,c] << (16*r) for r=0..3)`.

All 16-bit element patterns are legal, including zeros and sign-bit-set values.
No signed conversion, saturation, permutation reversal, or element summation
is applied. Each complete group of four accepted rows produces exactly four
ordered output columns. An incomplete group produces no output and remains
stored until more rows arrive or reset cancels it.

For example, rows containing elements `[0,1,2,3]`, `[4,5,6,7]`, `[8,9,10,11]`,
and `[12,13,14,15]` produce columns `[0,4,8,12]`, `[1,5,9,13]`, `[2,6,10,14]`,
and `[3,7,11,15]`, using the lane packing above.

## Reset Behavior

Reset is synchronous and active-high. A rising edge with `rst=1` clears all
stored rows and row/column indices and returns the block to collection. No
input or output transfers while reset is asserted; `in_ready=0`, `out_valid=0`
and `out_col=0`. Reset discards both a partial input tile and any unconsumed
columns of a completed tile. No pre-reset column may reappear. After reset
release, `in_ready=1`, and four newly accepted rows are required for output.
