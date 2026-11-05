# signal_processing_isp_aux_bnf

This block performs 5×5 bilateral noise filtering on one grayscale window per command. It combines range weights selected by distance from the center pixel with programmable per-position distance weights, forms their weighted average, and clips the result. The caller supplies the complete window, including any image-boundary padding.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_aux_bnf (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_p00,
    input  logic [15:0]  in_p01,
    input  logic [15:0]  in_p02,
    input  logic [15:0]  in_p03,
    input  logic [15:0]  in_p04,
    input  logic [15:0]  in_p10,
    input  logic [15:0]  in_p11,
    input  logic [15:0]  in_p12,
    input  logic [15:0]  in_p13,
    input  logic [15:0]  in_p14,
    input  logic [15:0]  in_p20,
    input  logic [15:0]  in_p21,
    input  logic [15:0]  in_p22,
    input  logic [15:0]  in_p23,
    input  logic [15:0]  in_p24,
    input  logic [15:0]  in_p30,
    input  logic [15:0]  in_p31,
    input  logic [15:0]  in_p32,
    input  logic [15:0]  in_p33,
    input  logic [15:0]  in_p34,
    input  logic [15:0]  in_p40,
    input  logic [15:0]  in_p41,
    input  logic [15:0]  in_p42,
    input  logic [15:0]  in_p43,
    input  logic [15:0]  in_p44,

    input  logic [15:0]  in_dw00,
    input  logic [15:0]  in_dw01,
    input  logic [15:0]  in_dw02,
    input  logic [15:0]  in_dw03,
    input  logic [15:0]  in_dw04,
    input  logic [15:0]  in_dw10,
    input  logic [15:0]  in_dw11,
    input  logic [15:0]  in_dw12,
    input  logic [15:0]  in_dw13,
    input  logic [15:0]  in_dw14,
    input  logic [15:0]  in_dw20,
    input  logic [15:0]  in_dw21,
    input  logic [15:0]  in_dw22,
    input  logic [15:0]  in_dw23,
    input  logic [15:0]  in_dw24,
    input  logic [15:0]  in_dw30,
    input  logic [15:0]  in_dw31,
    input  logic [15:0]  in_dw32,
    input  logic [15:0]  in_dw33,
    input  logic [15:0]  in_dw34,
    input  logic [15:0]  in_dw40,
    input  logic [15:0]  in_dw41,
    input  logic [15:0]  in_dw42,
    input  logic [15:0]  in_dw43,
    input  logic [15:0]  in_dw44,

    input  logic [15:0]  in_rw0,
    input  logic [15:0]  in_rw1,
    input  logic [15:0]  in_rw2,
    input  logic [15:0]  in_rw3,

    input  logic [15:0]  in_rthres0,
    input  logic [15:0]  in_rthres1,
    input  logic [15:0]  in_rthres2,

    input  logic [15:0]  in_clip,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_pixel
);
```

### Behavior interface

```python
class SignalProcessingIspAuxBnf:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_p00, in_p01, in_p02, in_p03, in_p04,
        in_p10, in_p11, in_p12, in_p13, in_p14,
        in_p20, in_p21, in_p22, in_p23, in_p24,
        in_p30, in_p31, in_p32, in_p33, in_p34,
        in_p40, in_p41, in_p42, in_p43, in_p44,
        in_dw00, in_dw01, in_dw02, in_dw03, in_dw04,
        in_dw10, in_dw11, in_dw12, in_dw13, in_dw14,
        in_dw20, in_dw21, in_dw22, in_dw23, in_dw24,
        in_dw30, in_dw31, in_dw32, in_dw33, in_dw34,
        in_dw40, in_dw41, in_dw42, in_dw43, in_dw44,
        in_rw0, in_rw1, in_rw2, in_rw3,
        in_rthres0, in_rthres1, in_rthres2,
        in_clip
    ):
        pass
```

The `process(...)` method returns one dictionary:
`{"out_pixel": ...}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_pixel`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_aux_bnf`: top-level bilateral noise filter for one 5×5 pixel window.

## Functional Behavior

All input pixels, distance weights, range weights, thresholds, and the clip value are unsigned 16-bit integers. `out_pixel` is an unsigned 16-bit integer.

For one accepted input transaction, define the 5×5 pixel matrix `P[i][j]` from `in_pij`, with `i` and `j` in the range 0..4, and define the 5×5 distance-weight matrix `DW[i][j]` from `in_dwij`. The center pixel is `C = P[2][2]`.

For each tap `(i, j)`, compute the unsigned absolute difference
`D[i][j] = abs(P[i][j] - C)`.
Because both operands are 16-bit unsigned, `D[i][j]` is in the range 0..65535.

For each tap `(i, j)`, select a range weight `RWSEL[i][j]` from the four programmable range-weight inputs using this ordered comparison sequence:

1. If `D[i][j] >= in_rthres0`, then `RWSEL[i][j] = in_rw0`.
2. Else, if `D[i][j] < in_rthres0` and `D[i][j] >= in_rthres1`, then `RWSEL[i][j] = in_rw1`.
3. Else, if `D[i][j] < in_rthres1` and `D[i][j] >= in_rthres2`, then `RWSEL[i][j] = in_rw2`.
4. Else, `RWSEL[i][j] = in_rw3`.

The thresholds are not sorted or validated by the block. The comparisons above are the complete definition. If the thresholds are not monotonic, the result is still defined by this exact priority order.

For each tap `(i, j)`, compute the final tap weight
`W[i][j] = RWSEL[i][j] * DW[i][j]`.
This multiplication is exact unsigned integer multiplication with no saturation. Implementations shall preserve the full mathematical product for the purpose of the subsequent sums.

Compute the weighted pixel sum
`NUM = sum over all 25 taps of (P[i][j] * W[i][j])`.

Compute the weight sum
`DEN = sum over all 25 taps of W[i][j]`.

If `DEN == 0`, the filtered pixel before clipping is defined as `0`.

If `DEN != 0`, the filtered pixel before clipping is
`F = NUM / DEN`
using unsigned integer division with truncation toward zero. Since `NUM` and `DEN` are non-negative, this is the same as floor division.

Apply output clipping:
- If `F > in_clip`, `out_pixel = in_clip`.
- Else, `out_pixel = F`.

`out_pixel` is then truncated to 16 bits for the output port. For legal 16-bit inputs and clipping as defined above, `out_pixel` is already in the range 0..65535.

There is exactly one output transaction for each accepted input transaction.
Transfers occur on rising clock edges when the respective valid and ready
signals are both high. Output valid and payload remain stable under
backpressure, and responses retain command order.

## Reset Behavior

The block has active-high asynchronous reset `rst`. Asserting reset cancels
any pending response; no pixel history is retained between transactions.

After reset:
- `out_valid = 0`
- `out_pixel = 16'h0000`
- the block is ready to accept the first input transaction with no retained history from before reset

Because the function is stateless across transactions, reset initializes no internal memories, accumulators, or counters beyond whatever control state is required to present `out_valid = 0` and accept a new transaction.
