# HEVC Deblock Boundary Strength

This block computes the HEVC deblocking boundary strength for one prediction-unit boundary between a current block Q and its adjacent block P. It implements the HEVC boundary-strength decision used before pixel filtering: intra boundaries produce strength 2, transform-residual boundaries produce strength 1, and motion-compensated inter boundaries produce strength 0 or 1 according to reference-picture and motion-vector differences. The block is stateless and processes one boundary query per accepted input transaction.

## Interface

### RTL interface

```systemverilog
module hevc_deblock_boundary_strength (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_block_strength_pre,
    input  logic [0:0]  in_p_intra,
    input  logic [0:0]  in_q_intra,
    input  logic [0:0]  in_p_cbf_luma,
    input  logic [0:0]  in_q_cbf_luma,
    input  logic [0:0]  in_slice_p_is_inter_p,
    input  logic [0:0]  in_slice_q_is_inter_p,

    input  logic [7:0]  in_ref_p0,
    input  logic [7:0]  in_ref_q0,
    input  logic [7:0]  in_ref_p1,
    input  logic [7:0]  in_ref_q1,

    input  logic signed [15:0] in_mv_p0_x,
    input  logic signed [15:0] in_mv_p0_y,
    input  logic signed [15:0] in_mv_q0_x,
    input  logic signed [15:0] in_mv_q0_y,
    input  logic signed [15:0] in_mv_p1_x,
    input  logic signed [15:0] in_mv_p1_y,
    input  logic signed [15:0] in_mv_q1_x,
    input  logic signed [15:0] in_mv_q1_y,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [1:0]  out_bs
);
```

Signal interpretation:
- `in_block_strength_pre` is the unsigned two-bit mark before motion analysis: 0 means unmarked, 1 a prediction-unit boundary, and 2 a transform boundary. The residual test is enabled only for marks greater than 1; value 3 follows the same rule as 2. A query is evaluated even for mark 0; callers decide which physical boundaries need querying.
- `in_p_intra`, `in_q_intra` indicate whether side P or side Q is intra coded.
- `in_p_cbf_luma`, `in_q_cbf_luma` indicate whether side P or side Q has nonzero luma residual at its transform depth.
- `in_slice_p_is_inter_p`, `in_slice_q_is_inter_p` indicate that the corresponding slice is P-slice inter. When both are `1`, the P-slice rule is used. Otherwise the B-slice rule is used.
- `in_ref_*` are abstract reference-picture identifiers. Equality and inequality of these 8-bit IDs define whether two references are the same picture. The value has no other meaning.
- Motion vectors are signed 16-bit integers. Only pairwise differences and the threshold `abs(diff) >= 4` affect the result.

Reference validity encoding:
- A reference ID of `8'hFF` means “no reference present”.
- When a reference is absent, its associated motion vector is treated as `(0, 0)` for all comparisons.

### Behavior interface

```python
class HevcDeblockBoundaryStrength:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_block_strength_pre,
        in_p_intra,
        in_q_intra,
        in_p_cbf_luma,
        in_q_cbf_luma,
        in_slice_p_is_inter_p,
        in_slice_q_is_inter_p,
        in_ref_p0,
        in_ref_q0,
        in_ref_p1,
        in_ref_q1,
        in_mv_p0_x,
        in_mv_p0_y,
        in_mv_q0_x,
        in_mv_q0_y,
        in_mv_p1_x,
        in_mv_p1_y,
        in_mv_q1_x,
        in_mv_q1_y,
    ): ...
```

`process()` returns:
- `{"out_bs": ...}` for every accepted input transaction.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_bs`: 2.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `hevc_deblock_boundary_strength`: top-level stateless boundary-strength calculator.

## Functional Behavior

For each accepted input transaction, the block computes a 2-bit unsigned output `out_bs` in the following order.

Let:
- `pre = in_block_strength_pre` as an unsigned 2-bit value.
- `p_intra = in_p_intra != 0`
- `q_intra = in_q_intra != 0`
- `p_cbf_luma = in_p_cbf_luma != 0`
- `q_cbf_luma = in_q_cbf_luma != 0`
- `p_inter_p = in_slice_p_is_inter_p != 0`
- `q_inter_p = in_slice_q_is_inter_p != 0`

Reference presence:
- `ref_p0_present = (in_ref_p0 != 8'hFF)`
- `ref_q0_present = (in_ref_q0 != 8'hFF)`
- `ref_p1_present = (in_ref_p1 != 8'hFF)`
- `ref_q1_present = (in_ref_q1 != 8'hFF)`

Effective motion vectors:
- If `ref_p0_present` is false, `mv_p0 = (0, 0)`, else `mv_p0 = (in_mv_p0_x, in_mv_p0_y)`.
- If `ref_q0_present` is false, `mv_q0 = (0, 0)`, else `mv_q0 = (in_mv_q0_x, in_mv_q0_y)`.
- If `ref_p1_present` is false, `mv_p1 = (0, 0)`, else `mv_p1 = (in_mv_p1_x, in_mv_p1_y)`.
- If `ref_q1_present` is false, `mv_q1 = (0, 0)`, else `mv_q1 = (in_mv_q1_x, in_mv_q1_y)`.

Reference equality:
- Two references are equal iff their 8-bit IDs are bitwise equal, including the absent code `8'hFF`.

Motion-difference predicate:
- `mv_far(a, b)` is true iff `abs(a.x - b.x) >= 4` or `abs(a.y - b.y) >= 4`.
- `abs` is mathematical absolute value on the signed 16-bit inputs interpreted as signed integers. The subtraction is exact integer subtraction; no wrapping is applied before the comparison.

Output selection:

1. Intra boundary rule:
   - If `p_intra` or `q_intra` is true, `out_bs = 2`.

2. Residual boundary rule:
   - Else, if `pre > 1` and (`p_cbf_luma` or `q_cbf_luma`) is true, `out_bs = 1`.

3. P-slice inter rule:
   - Else, if `p_inter_p` and `q_inter_p` are both true:
     - If `in_ref_p0 != in_ref_q0`, `out_bs = 1`.
     - Else if `mv_far(mv_q0, mv_p0)` is true, `out_bs = 1`.
     - Else `out_bs = 0`.

4. B-slice inter rule:
   - Else compute with list-0 and list-1 references and motion vectors.

   Define:
   - `same_order = (in_ref_p0 == in_ref_q0) and (in_ref_p1 == in_ref_q1)`
   - `cross_order = (in_ref_p0 == in_ref_q1) and (in_ref_p1 == in_ref_q0)`

   4a. If neither `same_order` nor `cross_order` is true, `out_bs = 1`.

   4b. Otherwise, if `in_ref_p0 != in_ref_p1`:
   - If `in_ref_p0 == in_ref_q0`:
     - If `mv_far(mv_q0, mv_p0)` or `mv_far(mv_q1, mv_p1)` is true, `out_bs = 1`.
     - Else `out_bs = 0`.
   - Else:
     - If `mv_far(mv_q1, mv_p0)` or `mv_far(mv_q0, mv_p1)` is true, `out_bs = 1`.
     - Else `out_bs = 0`.

   4c. Otherwise, `in_ref_p0 == in_ref_p1`:
   - Let
     - `cond_a = mv_far(mv_q0, mv_p0) or mv_far(mv_q1, mv_p1)`
     - `cond_b = mv_far(mv_q1, mv_p0) or mv_far(mv_q0, mv_p1)`
   - If `cond_a` and `cond_b` are both true, `out_bs = 1`.
   - Else `out_bs = 0`.

Output encoding:
- `out_bs` is returned as a 2-bit unsigned integer with legal values `0`, `1`, or `2`.

This block does not compute edge marking, transform-boundary discovery, luma/chroma filtering decisions, beta/tc lookup, or pixel filtering. It only computes the final boundary strength for one already-selected boundary.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state:
- None.

Output ports after reset and until the first accepted input:
- `in_ready = 1`
- `out_valid = 0`
- `out_bs = 2'b00`

Because the block is stateless and 1:1, every accepted input transaction produces one output transaction with the value defined in Functional Behavior.
