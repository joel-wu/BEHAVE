# SparseSupermask2of4

This selects `GetSubnet.forward` at sparsity 0.5 and `ApplyMask.forward`, with four unsigned integer scores. Equal scores at the threshold all survive, as in the source; this is not an exact-two-nonzeros Ampere encoding. Weights are opaque 32-bit payloads. No training/backward pass is included.

SparseSupermask2of4 computes a fixed 2:4 structured pruning mask from four input score values and applies that mask to four corresponding weight values. For each accepted input transaction, the module selects the two smallest scores in the 4-element group as masked-off lanes and the two largest scores as kept lanes, then outputs the masked weights. The selection rule is the exact 50th-percentile thresholding behavior induced by `k = 1 + round(0.5 * (4 - 1)) = 3`: any score strictly less than the 3rd-smallest score is masked to zero, and any score greater than or equal to that threshold is kept. This yields a 2:4 mask except when ties occur at the threshold, in which case more than two lanes may be kept.

## Interface

### RTL interface

```systemverilog
module ai_ml_sparse_2_4_sparse_supermask_2_4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_score0,
    input  logic [31:0] in_score1,
    input  logic [31:0] in_score2,
    input  logic [31:0] in_score3,
    input  logic [31:0] in_weight0,
    input  logic [31:0] in_weight1,
    input  logic [31:0] in_weight2,
    input  logic [31:0] in_weight3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic        out_mask0,
    output logic        out_mask1,
    output logic        out_mask2,
    output logic        out_mask3,
    output logic [31:0] out_weight0,
    output logic [31:0] out_weight1,
    output logic [31:0] out_weight2,
    output logic [31:0] out_weight3
);
```

### Behavior interface

```python
class SparseSupermask2of4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_score0,
        in_score1,
        in_score2,
        in_score3,
        in_weight0,
        in_weight1,
        in_weight2,
        in_weight3,
    ):
        pass
```

The `process()` return value is one dictionary with keys:
`out_mask0`, `out_mask1`, `out_mask2`, `out_mask3`, `out_weight0`, `out_weight1`, `out_weight2`, `out_weight3`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_mask0`, `out_mask1`, `out_mask2`, `out_mask3`: 1.
- `out_weight0`, `out_weight1`, `out_weight2`, `out_weight3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_sparse_2_4_sparse_supermask_2_4`: top-level module that computes the 2:4 supermask threshold for one 4-element group and applies the mask to the four weight lanes.

## Functional Behavior

Each accepted input transaction contains four unsigned 32-bit score values `in_score0..in_score3` and four unsigned 32-bit weight bit patterns `in_weight0..in_weight3`.

The score values are compared as unsigned 32-bit integers. No signed interpretation and no floating-point interpretation is applied inside this module. The weight values are also treated as opaque 32-bit bit patterns; masking does not inspect their sign or numeric meaning.

The module forms the multiset of four scores:
- `s0 = in_score0`
- `s1 = in_score1`
- `s2 = in_score2`
- `s3 = in_score3`

It then computes the 3rd-smallest element of `{s0, s1, s2, s3}`. This value is the threshold `k_val`. Equivalently, if the four scores are sorted in nondecreasing order as `a0 <= a1 <= a2 <= a3`, then `k_val = a2`.

The four 1-bit output mask lanes are generated independently by strict-less-than thresholding:
- `out_maski = 0` if `si < k_val`
- `out_maski = 1` if `si >= k_val`

This comparison is inclusive at the threshold. Therefore:
- if all four scores are distinct, exactly the two largest scores produce mask bit `1` and the two smallest scores produce mask bit `0`;
- if one or more scores are equal to `k_val`, every lane equal to `k_val` is kept, so the number of `1` bits may be 2, 3, or 4.

Masked weights are produced lane-wise:
- `out_weighti = in_weighti` when `out_maski = 1`
- `out_weighti = 32'h00000000` when `out_maski = 0`

There is one output transaction for every accepted input transaction.

Worked examples:
- Scores `[1, 2, 3, 4]` sort to `[1, 2, 3, 4]`, so `k_val = 3`. The mask is `[0, 0, 1, 1]`.
- Scores `[5, 5, 5, 7]` sort to `[5, 5, 5, 7]`, so `k_val = 5`. The mask is `[1, 1, 1, 1]`.
- Scores `[9, 1, 9, 1]` sort to `[1, 1, 9, 9]`, so `k_val = 9`. The mask is `[1, 0, 1, 0]`.

## Reset Behavior

The module has no architectural state.

`rst` is active-high. Reset may be implemented synchronously or asynchronously; in either case, while reset is asserted the module must not hold stale output data as valid.

After reset, any internal state is empty. `out_valid` shall be `0` until a post-reset input transaction is accepted and produces an output transaction according to the handshake protocol. `in_ready` may be `1` immediately after reset. Output payload ports `out_mask0..out_mask3` and `out_weight0..out_weight3` have no architecturally significant value while `out_valid` is `0`.
