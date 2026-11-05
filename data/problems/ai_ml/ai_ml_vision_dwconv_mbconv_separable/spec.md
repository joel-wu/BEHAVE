# DwConvMbConvSeparable

DwConvMbConvSeparable is a single-stage mobile-vision block derived from the depthwise-separable and MBConv families used in EfficientNet-class networks. For one accepted transaction, the block consumes one 3x3 spatial patch from each input channel together with per-channel depthwise weights, optional squeeze-excitation scale factors, and pointwise projection weights, then produces one output feature vector. The computation order is fixed: depthwise 3x3 convolution per input channel, optional squeeze-excitation scaling of each depthwise result, and 1x1 pointwise mixing across channels. All arithmetic is signed integer with exact wrap/truncate points defined below.

This finite sub-operator keeps the depthwise, externally supplied channel scale, and pointwise data path. It does not claim the source network's expansion, normalization, nonlinear activation, SE scale-generation network, or residual path. Spatial windows are supplied, not generated internally.

## Interface

### RTL interface

```systemverilog
module DwConvMbConvSeparable (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [71:0]  in_patch0,
    input  logic [71:0]  in_patch1,
    input  logic [71:0]  in_patch2,
    input  logic [71:0]  in_patch3,
    input  logic [71:0]  in_dw_w0,
    input  logic [71:0]  in_dw_w1,
    input  logic [71:0]  in_dw_w2,
    input  logic [71:0]  in_dw_w3,
    input  logic [7:0]   in_se0,
    input  logic [7:0]   in_se1,
    input  logic [7:0]   in_se2,
    input  logic [7:0]   in_se3,
    input  logic [31:0]  in_pw_row0,
    input  logic [31:0]  in_pw_row1,
    input  logic [31:0]  in_pw_row2,
    input  logic [31:0]  in_pw_row3,
    input  logic         in_use_se,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y0,
    output logic [15:0]  out_y1,
    output logic [15:0]  out_y2,
    output logic [15:0]  out_y3
);
```

### Behavior interface

```python
class DwConvMbConvSeparable:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_patch0,
        in_patch1,
        in_patch2,
        in_patch3,
        in_dw_w0,
        in_dw_w1,
        in_dw_w2,
        in_dw_w3,
        in_se0,
        in_se1,
        in_se2,
        in_se3,
        in_pw_row0,
        in_pw_row1,
        in_pw_row2,
        in_pw_row3,
        in_use_se,
    ): ...
```

The returned dict contains keys `out_y0`, `out_y1`, `out_y2`, and `out_y3`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `DwConvMbConvSeparable`: top-level module implementing the complete block.

## Functional Behavior

Each accepted input transaction is independent. The block has no architectural state affecting arithmetic from one transaction to the next.

### Input packing

`in_patch0` through `in_patch3` each contain one 3x3 patch for one input channel, packed as nine signed 8-bit elements. Element `k` occupies bits `[8*k +: 8]` with `k = 0..8`. Each element is interpreted as two's-complement signed 8-bit.

`in_dw_w0` through `in_dw_w3` each contain one 3x3 depthwise kernel for one input channel, packed identically to the patch ports as nine signed 8-bit elements.

`in_pw_row0` through `in_pw_row3` each contain one row of the 1x1 pointwise projection matrix, packed as four signed 8-bit coefficients. Coefficient `k` occupies bits `[8*k +: 8]` with `k = 0..3`. Each coefficient is interpreted as two's-complement signed 8-bit.

`in_se0` through `in_se3` are unsigned 8-bit scale factors.

`in_use_se` is a 1-bit control. `0` disables squeeze-excitation scaling. `1` enables it.

### Step 1: depthwise 3x3 convolution

For each channel `c = 0..3`, let `patch[c][k]` be the signed 8-bit patch elements and `dw_w[c][k]` be the signed 8-bit depthwise weights.

The depthwise sum for channel `c` is

`dw_sum[c] = Σ_{k=0..8} (patch[c][k] * dw_w[c][k])`

Each product is signed integer multiplication of two 8-bit signed values, producing an exact mathematical signed product. The nine products are summed with exact mathematical signed addition. The range is [-146304,147456], requiring 19 signed bits; retain the exact sum without narrowing to 16 bits.

### Step 2: optional squeeze-excitation scaling

If `in_use_se == 0`, then `scaled[c] = dw_sum[c]`.

If `in_use_se == 1`, then for each channel `c`:

- form the signed product `se_prod[c] = dw_sum[c] * unsigned(in_se[c])`
- compute `scaled[c] = trunc_toward_zero(se_prod[c] / 256)`

`trunc_toward_zero` means integer division rounded toward zero, not floor. Examples:
- `se_prod = 511` gives `1`
- `se_prod = 255` gives `0`
- `se_prod = -511` gives `-1`
- `se_prod = -255` gives `0`

No saturation is applied. `scaled[c]` is the mathematical quotient after truncation toward zero.

### Step 3: pointwise 1x1 projection

For each output `r = 0..3`, let `pw[r][k]` be the four signed 8-bit coefficients unpacked from `in_pw_rowr`.

Compute

`pw_sum[r] = Σ_{k=0..3} (scaled[k] * pw[r][k])`

Each multiplication is exact signed integer multiplication. The four products are added exactly with no intermediate truncation.

### Step 4: output narrowing

Each `pw_sum[r]` is converted to the 16-bit output port by modulo-2^16 wrap, i.e.

`out_yr = pw_sum[r] mod 2^16`

The returned/output bit pattern is the low 16 bits of the two's-complement representation of `pw_sum[r]`.

Worked example: if `pw_sum[r] = -1`, `out_yr = 16'hFFFF`; if `pw_sum[r] = 65536`, `out_yr = 16'h0000`.

### Edge cases

- Negative packed 8-bit elements and coefficients are always interpreted in two's-complement signed form.
- `in_se*` are always interpreted as unsigned values in `[0, 255]`.
- With `in_use_se == 1` and `in_se[c] == 0`, `scaled[c] = 0` for all `dw_sum[c]`.
- With `in_use_se == 0`, the `in_se*` ports have no effect on the outputs.
- There is exactly one output transaction for each accepted input transaction.

## Reset Behavior

The block uses asynchronous active-high reset `rst`, which discards pending output.

Architectural state after reset:
- no stored arithmetic state
- no buffered input data
- no buffered output data

Output/interface state after reset:
- `out_valid = 0`
- `out_y0 = 16'h0000`
- `out_y1 = 16'h0000`
- `out_y2 = 16'h0000`
- `out_y3 = 16'h0000`

The block is ready to accept a new input transaction after reset deasserts, so `in_ready` shall be high whenever the implementation is not internally stalling for its chosen latency.
