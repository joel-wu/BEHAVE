# Signal Processing ISP Auxiliary FCS

This block performs false-color suppression on one three-channel pixel using a co-supplied edge-map sample. For each accepted input transaction, it selects a gain from three edge regions, scales each channel by that gain divided by 256, adds neutral offset 128, and quantizes and clips the result to [0,255]. The middle region uses a programmable linear gain.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_aux_fcs (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_img_r,
    input  logic [7:0]  in_img_g,
    input  logic [7:0]  in_img_b,
    input  logic signed [15:0] in_edgemap,
    input  logic [7:0]  in_fcs_edge_lo,
    input  logic [7:0]  in_fcs_edge_hi,
    input  logic signed [15:0] in_gain,
    input  logic signed [15:0] in_intercept,
    input  logic signed [15:0] in_slope,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_img_r,
    output logic [7:0]  out_img_g,
    output logic [7:0]  out_img_b
);
```

### Behavior interface

```python
class SignalProcessingIspAuxFcs:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_img_r,
        in_img_g,
        in_img_b,
        in_edgemap,
        in_fcs_edge_lo,
        in_fcs_edge_hi,
        in_gain,
        in_intercept,
        in_slope,
    ): ...
```

The returned dict has keys `out_img_r`, `out_img_g`, and `out_img_b`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_img_b`, `out_img_g`, `out_img_r`: 8.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_aux_fcs`: top-level false-color suppression block operating on one 3-channel pixel per accepted input transaction.

## Functional Behavior

All arithmetic is integer arithmetic.

`in_img_r`, `in_img_g`, and `in_img_b` are unsigned 8-bit channel samples in the range 0 to 255.

`in_edgemap` is a signed 16-bit edge-map sample interpreted as two's-complement.

`in_fcs_edge_lo` and `in_fcs_edge_hi` are unsigned 8-bit threshold values. They are compared against the absolute value of `in_edgemap`.

`in_gain`, `in_intercept`, and `in_slope` are signed 16-bit coefficients interpreted as two's-complement.

For each accepted input transaction, the block computes:

1. `edge_abs = abs(in_edgemap)` as a non-negative integer magnitude.
   - If `in_edgemap == -32768`, then `edge_abs = 32768`.
   - No truncation is applied to `edge_abs` before comparisons or later arithmetic.

2. A signed gain term `uvgain`:
   - If `edge_abs <= in_fcs_edge_lo`, then `uvgain = in_gain`.
   - Else if `edge_abs > in_fcs_edge_lo` and `edge_abs < in_fcs_edge_hi`, then `uvgain = in_intercept - (in_slope * in_edgemap)`.
   - Else `uvgain = 0`.

The middle-region formula uses the signed value of `in_edgemap`, not `edge_abs`.

For each channel `c` in `{in_img_r, in_img_g, in_img_b}`, the block computes:

3. `product_c = uvgain * c`, using the full signed product.

4. `biased_c = floor(product_c / 256) + 128`.
   - Floor division rounds toward negative infinity, before clipping.
   - Products 511, -511 and -255 give biased values 129, 126 and 127 respectively.
   - Do not truncate a negative fractional product toward zero before adding the offset.

5. `out_img_* = clip(biased_c, 0, 255)`, where clipping is inclusive:
   - if `biased_c < 0`, output `0`
   - else if `biased_c > 255`, output `255`
   - else output `biased_c`

The three channels are processed independently with the same `uvgain`.

No intermediate wraps or saturates. Equivalently, clip the real-valued expression `128 + uvgain*c/256` to [0,255], then truncate the non-negative result to an integer. This avoids intermediate signed-16 storage wrap for large coefficients.

Output mapping:
- `out_img_r` is computed from `in_img_r`
- `out_img_g` is computed from `in_img_g`
- `out_img_b` is computed from `in_img_b`

The block is a 1:1 transaction mapping: each accepted input transaction produces exactly one output transaction.

Transfers occur on `in_valid && in_ready` and `out_valid && out_ready`. Output transactions remain ordered, and an unconsumed valid output must retain its data.

## Reset Behavior

Reset is asynchronous active-high and discards any pending output. The block has no inter-transaction algorithmic state.

While reset is asserted, the output data ports and output-valid state are cleared:
- `out_valid = 0`
- `out_img_r = 0`
- `out_img_g = 0`
- `out_img_b = 0`

After reset is deasserted, the next accepted input transaction is processed independently of all prior transactions. `in_ready` may be held high whenever the implementation is able to accept a transaction.
