# Signal Processing ISP Auxiliary BCC

This block performs per-pixel Brightness Contrast Control (BCC) on a grayscale image stream. For each accepted input pixel, it computes a signed affine transform using the current pixel value, a signed brightness offset, and a signed contrast gain, then clips the result into the unsigned output range. The computation is stateless across pixels and is applied independently to every transaction.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_aux_bcc (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_img,
    input  logic [7:0]  in_brightness,
    input  logic [7:0]  in_contrast,
    input  logic [7:0]  in_clip,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_img
);
```

### Behavior interface

```python
class SignalProcessingIspAuxBcc:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_img, in_brightness, in_contrast, in_clip): ...
```

The `process(...)` method returns:
- `{"out_img": ...}` for one output transaction.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_img`: 8.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_aux_bcc`: top-level BCC pixel processor.

## Functional Behavior

All input and output ports are 8-bit integer bit patterns.

Interpretation of ports:
- `in_img` is an unsigned 8-bit pixel value in the range `[0, 255]`.
- `in_brightness` is a signed 8-bit two's-complement integer in the range `[-128, 127]`.
- `in_contrast` is a signed 8-bit two's-complement integer in the range `[-128, 127]`.
- `in_clip` is an unsigned 8-bit clipping limit in the range `[0, 255]`.
- `out_img` is an unsigned 8-bit clipped pixel value.

For each accepted input transaction, compute the output in this exact order:

1. Convert `in_img` to unsigned integer `img`.
2. Convert `in_brightness` to signed integer `brightness`.
3. Convert `in_contrast` to signed integer `contrast`.
4. Convert `in_clip` to unsigned integer `clip`.
5. Compute signed intermediate
   `temp = img + brightness`.
6. Compute signed intermediate
   `delta = img - 127`.
7. Compute signed intermediate
   `scaled = delta * contrast`.
8. Compute signed intermediate
   `bcc = temp + scaled`.

No truncation is applied to `temp`, `delta`, `scaled`, or `bcc`; they are mathematical integers.

The final output is clipping of `bcc` into the closed interval `[0, clip]`:
- if `bcc < 0`, then `out_img = 0`;
- else if `bcc > clip`, then `out_img = clip`;
- else `out_img = bcc`.

`out_img` is returned as an 8-bit unsigned value.

Equivalent single-expression form:
`out_img = clamp(img + brightness + (img - 127) * contrast, 0, clip)`,
where `clamp(x, lo, hi)` returns `lo` when `x < lo`, returns `hi` when `x > hi`, and otherwise returns `x`.

Boundary behavior:
- `bcc == 0` produces `out_img = 0`.
- `bcc == clip` produces `out_img = clip`.
- `in_clip == 0` forces `out_img = 0` for all inputs.
- Negative `contrast` values are legal and invert the sign of the `(img - 127)` term.
- No state is retained between pixels; each transaction is independent.

## Reset Behavior

Reset is active-high synchronous reset.

Architectural state:
- This block has no architectural state.

Output port values during reset and immediately after reset, before any accepted input:
- `out_valid = 0`
- `out_img = 0`

The block may accept a new input transaction immediately after reset is deasserted.
