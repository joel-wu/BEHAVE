# Bitmap Roaring Bitset Intersect 16w

This block computes the bitwise intersection of two 1024-bit bitmap slices and reports whether the intersection is non-empty. Each input transaction carries one 16-word slice from each operand, where each word is 64 bits and together represents bit positions 0 through 1023. The block performs a word-wise bitwise AND, emits the 16 result words, and emits the exact population count of the 1024-bit intersection. The algorithm is the scalar bitmap-container intersection used for Roaring bitsets: for each word index `i` from 0 to 15, form `dst[i] = a[i] & b[i]`, then sum the per-word popcounts.

## Interface

### RTL interface

```systemverilog
module data_systems_bitmap_roaring_bitset_intersect_16w (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a0,
    input  logic [63:0]  in_a1,
    input  logic [63:0]  in_a2,
    input  logic [63:0]  in_a3,
    input  logic [63:0]  in_a4,
    input  logic [63:0]  in_a5,
    input  logic [63:0]  in_a6,
    input  logic [63:0]  in_a7,
    input  logic [63:0]  in_a8,
    input  logic [63:0]  in_a9,
    input  logic [63:0]  in_a10,
    input  logic [63:0]  in_a11,
    input  logic [63:0]  in_a12,
    input  logic [63:0]  in_a13,
    input  logic [63:0]  in_a14,
    input  logic [63:0]  in_a15,
    input  logic [63:0]  in_b0,
    input  logic [63:0]  in_b1,
    input  logic [63:0]  in_b2,
    input  logic [63:0]  in_b3,
    input  logic [63:0]  in_b4,
    input  logic [63:0]  in_b5,
    input  logic [63:0]  in_b6,
    input  logic [63:0]  in_b7,
    input  logic [63:0]  in_b8,
    input  logic [63:0]  in_b9,
    input  logic [63:0]  in_b10,
    input  logic [63:0]  in_b11,
    input  logic [63:0]  in_b12,
    input  logic [63:0]  in_b13,
    input  logic [63:0]  in_b14,
    input  logic [63:0]  in_b15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_w0,
    output logic [63:0]  out_w1,
    output logic [63:0]  out_w2,
    output logic [63:0]  out_w3,
    output logic [63:0]  out_w4,
    output logic [63:0]  out_w5,
    output logic [63:0]  out_w6,
    output logic [63:0]  out_w7,
    output logic [63:0]  out_w8,
    output logic [63:0]  out_w9,
    output logic [63:0]  out_w10,
    output logic [63:0]  out_w11,
    output logic [63:0]  out_w12,
    output logic [63:0]  out_w13,
    output logic [63:0]  out_w14,
    output logic [63:0]  out_w15,
    output logic [10:0]  out_cardinality,
    output logic         out_intersects
);
```

### Behavior interface

```python
class BitmapRoaringBitsetIntersect16w:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_a0, in_a1, in_a2, in_a3, in_a4, in_a5, in_a6, in_a7,
        in_a8, in_a9, in_a10, in_a11, in_a12, in_a13, in_a14, in_a15,
        in_b0, in_b1, in_b2, in_b3, in_b4, in_b5, in_b6, in_b7,
        in_b8, in_b9, in_b10, in_b11, in_b12, in_b13, in_b14, in_b15
    ): ...
```

Call `process` once per accepted request, passing each input word as an unsigned
64-bit integer. It returns one dictionary with exactly these keys:
`out_w0`, `out_w1`, `out_w2`, `out_w3`, `out_w4`, `out_w5`, `out_w6`, `out_w7`,
`out_w8`, `out_w9`, `out_w10`, `out_w11`, `out_w12`, `out_w13`, `out_w14`,
`out_w15`, `out_cardinality`, and `out_intersects`.
Each word is an integer in 0..2^64-1, cardinality is an integer in 0..1024,
and the flag is integer 0 or 1. An empty intersection returns the same complete
dictionary with all values zero, not `None` or sixteen separate records.
The model has no persistent algorithmic state; `reset()` is a no-op.

## Modules

- `data_systems_bitmap_roaring_bitset_intersect_16w`: top-level module that computes a 1024-bit bitwise intersection, exact population count, and non-empty flag.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

Each accepted input transaction is independent. The block has no architectural state that affects future transactions.

Interpret `in_a0` through `in_a15` and `in_b0` through `in_b15` as 16 unsigned 64-bit words each. Word index `0` corresponds to bit positions `[63:0]`, word index `1` to bit positions `[127:64]`, and in general word index `i` to bit positions `[64*i + 63 : 64*i]`.

For each word index `i` in `0..15`, compute:

- `out_wi = in_ai & in_bi`

The bitwise AND is an unsigned 64-bit operation with no widening and no signed interpretation. Each `out_wi` is returned as a 64-bit unsigned value.

The output cardinality is the exact number of `1` bits across all 16 intersection words:

- `out_cardinality = popcount(out_w0) + popcount(out_w1) + ... + popcount(out_w15)`

`popcount(x)` is the number of set bits in the 64-bit word `x`, in the inclusive range `[0, 64]`.

The maximum possible cardinality is `16 * 64 = 1024`, so `out_cardinality` is an 11-bit unsigned value in the inclusive range `[0, 1024]`.

The non-empty flag is derived from the intersection result:

- `out_intersects = 1` if `out_cardinality != 0`
- `out_intersects = 0` if `out_cardinality == 0`

This is equivalent to:
- `out_intersects = 1` if any `out_wi != 0`
- `out_intersects = 0` only if all `out_wi == 0`

There are no exceptional cases. All 64-bit input bit patterns are legal and are processed exactly as raw bitmaps.

## Reset Behavior

Reset is synchronous active-high `rst`.

On reset, the block contains no architectural state. After reset, it is ready to accept a new input transaction. If the implementation contains output holding registers, they shall be cleared so that:

- `out_valid = 0`
- `out_w0` through `out_w15` = `64'h0000000000000000`
- `out_cardinality = 11'h000`
- `out_intersects = 1'b0`

The block shall not retain any pending transaction across reset.
