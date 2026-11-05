# MergeSort8F32

This module sorts exactly eight IEEE-754 single-precision values using a merge-sort decomposition matching a top-down recursive split into two 4-element halves followed by merges using the predicate below. One accepted input transaction supplies the eight input elements and one direction bit; one output transaction returns the eight elements in sorted order. The comparison rule is defined on raw FP32 bit patterns with IEEE-style ordering for finite numbers and infinities, signed-zero equality, and a deterministic NaN rule derived from the merge predicate. The descending predicate selects the right half on equality, so stability is not guaranteed in both directions.

## Interface

### RTL interface

```systemverilog
module merge_sort8_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,
    input  logic [31:0] in_x4,
    input  logic [31:0] in_x5,
    input  logic [31:0] in_x6,
    input  logic [31:0] in_x7,
    input  logic        in_dir,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3,
    output logic [31:0] out_y4,
    output logic [31:0] out_y5,
    output logic [31:0] out_y6,
    output logic [31:0] out_y7
);
```

### Behavior interface

```python
class MergeSort8F32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7, in_dir):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`, `out_y4`, `out_y5`, `out_y6`, `out_y7`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `merge_sort8_f32`: top-level sorter for one 8-element FP32 block.

## Functional Behavior

Each accepted input transaction consumes:
- `in_x0`..`in_x7`: eight unsigned 32-bit FP32 bit patterns.
- `in_dir`: 1-bit sort direction.

Each accepted input transaction produces exactly one output transaction:
- `out_y0`..`out_y7`: eight unsigned 32-bit FP32 bit patterns.

`in_dir` selects the final order:
- `in_dir = 1`: ascending order.
- `in_dir = 0`: descending order.

The sorting algorithm is fixed to a top-down merge sort on the ordered input sequence
`[in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7]`:
1. Sort indices `[0:4)` and `[4:8)` recursively.
2. Each 4-element half is sorted by recursively sorting its two 2-element halves and then merging them.
3. Each 2-element half is sorted by a single comparison and swap if required.
4. Each merge selects its next element using the predicate below.

The merge predicate is the direct translation of:
- compute `le = fp32_le(left, right)`
- take the left element iff `in_dir == le`
- otherwise take the right element

`fp32_le(a, b)` is defined on 32-bit FP32 encodings as follows:
1. Let `a_exp = a[30:23]`, `a_frac = a[22:0]`, `a_sign = a[31]`.
2. Let `b_exp = b[30:23]`, `b_frac = b[22:0]`, `b_sign = b[31]`.
3. `a` is NaN iff `a_exp == 8'hFF` and `a_frac != 0`.
4. `b` is NaN iff `b_exp == 8'hFF` and `b_frac != 0`.
5. `a` is zero iff `a[30:0] == 31'd0`. `b` is zero iff `b[30:0] == 31'd0`. Therefore `+0` and `-0` are both treated as zero.
6. If `a` is NaN or `b` is NaN, `fp32_le(a, b) = 0`.
7. Else if both are zero, `fp32_le(a, b) = 1`.
8. Else if `a_sign != b_sign`, `fp32_le(a, b) = a_sign`. Therefore every negative nonzero value compares less than every positive nonzero value.
9. Else if `a_sign == 0`, `fp32_le(a, b) = (a[30:0] <= b[30:0])`.
10. Else `fp32_le(a, b) = (a[30:0] >= b[30:0])`.

This comparison yields the standard total order for all non-NaN FP32 values, with `+0` and `-0` treated as equal.

Because the merge takes the left element when `in_dir == fp32_le(left, right)`:
- For ascending order (`in_dir = 1`), the left element is chosen when `left <= right`.
- For descending order (`in_dir = 0`), the left element is chosen when `left > right` or when either operand is NaN.

Tie and NaN handling follow this predicate:
- Ascending: if `left` and `right` are equal non-NaN values, `left` is emitted first.
- Descending: if `left` and `right` are equal non-NaN values, `right` is emitted first, because `fp32_le(left, right) = 1` and `in_dir = 0`.
- If either operand in a comparison is NaN, `fp32_le = 0`. Therefore:
  - Ascending: the right operand is chosen before the left operand.
  - Descending: the left operand is chosen before the right operand.
  Across the full merge-sort recursion, this defines the required NaN placement deterministically; no NaN payload bits are modified.

No arithmetic is performed on FP values. Outputs are always a permutation of the eight input 32-bit words; every output bit pattern exactly matches one input bit pattern.

Worked examples:
- Ascending with finite values: inputs `[3.0, 1.0, 2.0, 0.0, 7.0, 6.0, 5.0, 4.0]` produce `[0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]`.
- Descending with equal finite values at positions 0 then 1: the value from position 1 appears before the equal value from position 0.
- In any comparison between a NaN on the left and a non-NaN on the right, ascending chooses the right operand first and descending chooses the left operand first.

## Reset Behavior

Reset is synchronous active-high through `rst`.

On any cycle with `rst = 1`:
- `out_valid` shall be `0`.
- `in_ready` shall be `1`.
- `out_y0`..`out_y7` shall each be `32'h00000000`.

Architectural state after reset:
- No input transaction is pending.
- No output transaction is pending.
- The stored output data words, if implemented as registers, are all `32'h00000000`.
