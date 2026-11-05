# Winograd F(2,3) One-Dimensional Output Transform

Transform four Winograd-domain products into two spatial results. This is the one-dimensional output transform, not a full 4x4-to-2x2 convolution.

## Interface

### RTL interface

```systemverilog
module ai_ml_vision_winograd_winograd_f23_output_xform (
    input  logic         clk,
    input  logic         rst,
    input  logic         in_valid,
    output logic         in_ready,
    input  logic signed [15:0] in_m0,
    input  logic signed [15:0] in_m1,
    input  logic signed [15:0] in_m2,
    input  logic signed [15:0] in_m3,
    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [17:0] out_y0,
    output logic signed [17:0] out_y1
);
```

### Behavior interface

```python
class AiMlVisionWinogradWinogradF23OutputXform:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_m0, in_m1, in_m2, in_m3): ...
```

The `process()` return value is one dictionary with keys `out_y0` and `out_y1`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`: 18.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Protocol

Transfers occur on rising clock edges with `valid && ready` and reset inactive. Each accepted input produces exactly one output, in acceptance order. The source holds its input record and valid until accepted; the implementation holds output valid and the complete payload while `out_ready=0`. Gaps, stalls and simultaneous input/output transfers do not change the result. Latency and internal topology are not prescribed.

## Modules

The declared top-level module implements this transaction transform.

## Functional Behavior

Interpret the four input words as signed 16-bit two's-complement values. Compute exact integers `y0=m0+m1+m2` and `y1=m1-m2-m3`. Return their 18-bit two's-complement encodings on `out_y0` and `out_y1`. Both sums fit signed 18 bits, so no numerical truncation or saturation occurs. The equivalent matrix is `A^T=[[1,1,1,0],[0,1,-1,-1]]`. A two-dimensional inverse transform applies the corresponding operation along both axes; that composition and the kernel products are outside this task.

## Reset Behavior

Asynchronous active-high `rst` clears pending output validity and all output payload fields to zero. While reset is asserted, no transaction is accepted or emitted. An unfinished pre-reset output is cancelled. `in_ready=1` after the reset state is established. There is no persistent algorithmic state; each post-reset input is independent of earlier transactions.
