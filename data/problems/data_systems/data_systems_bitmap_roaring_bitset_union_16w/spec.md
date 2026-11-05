# Roaring Bitset Union 16w

This block computes the bitwise union of two 1024-bit roaring bitmap fragments and reports the population count of the union. Each accepted input transaction supplies one 1024-bit fragment from source A and one 1024-bit fragment from source B, each encoded as 16 independent 64-bit words. The output is the 1024-bit wordwise bitwise OR of the two fragments, together with the exact cardinality of the resulting fragment. The required result is the wordwise union and its exact cardinality; word scheduling and popcount architecture are implementation choices.

## Interface

### RTL interface

```systemverilog
module data_systems_bitmap_roaring_bitset_union_16w (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_a_w0,
    input  logic [63:0]  in_a_w1,
    input  logic [63:0]  in_a_w2,
    input  logic [63:0]  in_a_w3,
    input  logic [63:0]  in_a_w4,
    input  logic [63:0]  in_a_w5,
    input  logic [63:0]  in_a_w6,
    input  logic [63:0]  in_a_w7,
    input  logic [63:0]  in_a_w8,
    input  logic [63:0]  in_a_w9,
    input  logic [63:0]  in_a_w10,
    input  logic [63:0]  in_a_w11,
    input  logic [63:0]  in_a_w12,
    input  logic [63:0]  in_a_w13,
    input  logic [63:0]  in_a_w14,
    input  logic [63:0]  in_a_w15,
    input  logic [63:0]  in_b_w0,
    input  logic [63:0]  in_b_w1,
    input  logic [63:0]  in_b_w2,
    input  logic [63:0]  in_b_w3,
    input  logic [63:0]  in_b_w4,
    input  logic [63:0]  in_b_w5,
    input  logic [63:0]  in_b_w6,
    input  logic [63:0]  in_b_w7,
    input  logic [63:0]  in_b_w8,
    input  logic [63:0]  in_b_w9,
    input  logic [63:0]  in_b_w10,
    input  logic [63:0]  in_b_w11,
    input  logic [63:0]  in_b_w12,
    input  logic [63:0]  in_b_w13,
    input  logic [63:0]  in_b_w14,
    input  logic [63:0]  in_b_w15,

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
    output logic [10:0]  out_cardinality
);
endmodule
```

### Behavior interface

```python
class DataSystemsBitmapRoaringBitsetUnion16w:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_a_w0, in_a_w1, in_a_w2, in_a_w3,
        in_a_w4, in_a_w5, in_a_w6, in_a_w7,
        in_a_w8, in_a_w9, in_a_w10, in_a_w11,
        in_a_w12, in_a_w13, in_a_w14, in_a_w15,
        in_b_w0, in_b_w1, in_b_w2, in_b_w3,
        in_b_w4, in_b_w5, in_b_w6, in_b_w7,
        in_b_w8, in_b_w9, in_b_w10, in_b_w11,
        in_b_w12, in_b_w13, in_b_w14, in_b_w15
    ): ...
```

Each call consumes one accepted input transaction and returns one `dict` with
exactly these keys: `out_w0`, `out_w1`, `out_w2`, `out_w3`, `out_w4`, `out_w5`,
`out_w6`, `out_w7`, `out_w8`, `out_w9`, `out_w10`, `out_w11`, `out_w12`,
`out_w13`, `out_w14`, `out_w15`, and `out_cardinality`. Word values are unsigned
64-bit Python integers; cardinality is an integer in 0..1024. The dictionary
contains the whole result, not a sequence of word records. An empty union still
returns all fields with zero values, never `None` or an empty list. There is no
algorithmic history between calls; `reset()` restores the initial model state.

## Modules

- `data_systems_bitmap_roaring_bitset_union_16w`: top-level module that accepts two 16-word bitsets, computes the 16 wordwise OR results, and computes the population count of the 1024-bit union.

## Functional Behavior

Transfers occur on rising clock edges with `rst = 0`: an input is accepted when `in_valid && in_ready`, and an output is consumed when `out_valid && out_ready`. The sender holds `in_valid` and all input payload fields stable while waiting for `in_ready`; the block likewise holds `out_valid` and all output payload fields stable under output backpressure. Responses preserve accepted-input order. Reset cancels all outstanding transactions; no pre-reset response may appear after reset. No fixed latency or pipeline topology is required.

Each input transaction contains two 1024-bit bitsets represented as 16 unsigned 64-bit words per operand.

Word numbering is little-endian with respect to bit index within the 1024-bit set:
- `in_a_w0` and `in_b_w0` contain bits 63:0 of their respective sets.
- `in_a_w1` and `in_b_w1` contain bits 127:64.
- ...
- `in_a_w15` and `in_b_w15` contain bits 1023:960.

For each word index `i` in `[0, 15]`, the output word is:
- `out_wi = in_a_wi | in_b_wi`

The OR is a pure 64-bit bitwise OR with no signed interpretation. Each `out_wi` is truncated to 64 bits, which is equivalent to ordinary 64-bit OR.

The output cardinality is the total number of `1` bits across all 16 output words:
- `out_cardinality = popcount(out_w0) + popcount(out_w1) + ... + popcount(out_w15)`

`popcount(x)` is the number of set bits in the 64-bit unsigned value `x`, in the range `[0, 64]`.

`out_cardinality` is an 11-bit unsigned value. Its legal numeric range is `[0, 1024]`, since the union spans exactly 1024 bits.

The operation is 1:1: every accepted input transaction produces exactly one output transaction.

No auxiliary metadata is carried. There is no input cardinality field, no “unknown cardinality” mode, and no alternative no-cardinality path. The output cardinality is always the exact popcount of the union words presented on `out_w0` through `out_w15`.

Edge cases:
- If every input word of both operands is `0`, every output word is `0` and `out_cardinality = 0`.
- If for every `i`, `in_a_wi | in_b_wi == 64'hFFFF_FFFF_FFFF_FFFF`, then `out_cardinality = 1024`.
- Identical inputs are allowed; the result is that same bitset and its popcount.
- All 64-bit input patterns are legal on every input word.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state after reset:
- No buffered input transaction is pending.
- No buffered output transaction is pending.

Output interface values while idle after reset:
- `out_valid = 0`
- `out_w0` through `out_w15` = `0`
- `out_cardinality = 0`

The block may accept a new input transaction immediately after reset is deasserted. `in_ready` shall be high whenever the block is able to accept a new transaction and low only when holding an unconsumed output or otherwise internally busy.
