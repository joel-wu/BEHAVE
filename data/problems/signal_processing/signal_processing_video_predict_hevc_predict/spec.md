# HEVC Weighted Prediction Core

This block computes one weighted HEVC inter-prediction sample per accepted input transaction. It implements the arithmetic used by uni-prediction weighting and bi-prediction weighting on already-interpolated prediction samples. The block does not perform motion-vector interpolation, neighbour discovery, or frame-memory access; its input samples are the signed intermediate samples produced by the interpolation stage, and its output is one clipped reconstructed prediction pixel.

## Interface

### RTL interface

```systemverilog
module signal_processing_video_predict_hevc_predict (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_bidir,
    input  logic         in_src0,
    input  logic         in_src1,
    input  logic signed [15:0] in_p0,
    input  logic signed [15:0] in_p1,
    input  logic signed [7:0]  in_w0,
    input  logic signed [7:0]  in_w1,
    input  logic signed [8:0]  in_offset0,
    input  logic signed [8:0]  in_offset1,
    input  logic        [3:0]  in_log2_weight_denom,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_pixel
);
```

### Behavior interface

```python
class SignalProcessingVideoPredictHevcPredict:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_bidir, in_src0, in_src1, in_p0, in_p1, in_w0, in_w1, in_offset0, in_offset1, in_log2_weight_denom): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_pixel`: 8.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction count

Every accepted input produces exactly one output transaction.

## Modules

- `signal_processing_video_predict_hevc_predict`: top-level weighted prediction arithmetic block.

## Functional Behavior

All arithmetic is integer arithmetic.

Constants:

- `X265_DEPTH = 8`
- `IF_INTERNAL_PREC = 14`
- `IF_INTERNAL_OFFS = 1 << (IF_INTERNAL_PREC - 1) = 8192`

Port meanings:

- `in_bidir = 0` selects uni-prediction weighting.
- `in_bidir = 1` selects bi-prediction weighting.
- In uni mode, `in_src0=1` selects predictor 0 and `in_src0=0` selects predictor 1, including the corresponding weight and offset. `in_src1` is informational and does not affect selection. In bi mode both predictors are used regardless of these flags. Conventional source calls select exactly one available predictor in uni mode and both in bi mode; the same deterministic rule also defines other flag combinations.
- `in_p0` and `in_p1` are signed 16-bit interpolated prediction samples.
- `in_w0` and `in_w1` are signed 8-bit weights.
- `in_offset0` and `in_offset1` are signed 9-bit offsets in 8-bit pixel units.
- `in_log2_weight_denom` is an unsigned 4-bit denominator exponent.

The output `out_pixel` is an unsigned 8-bit pixel clipped to the inclusive range `[0, 255]`.

### Signed input interpretation

Each signed input port is interpreted as two's-complement with its declared width:

- `p0 = sext16(in_p0)`
- `p1 = sext16(in_p1)`
- `w0 = sext8(in_w0)`
- `w1 = sext8(in_w1)`
- `offset0 = sext9(in_offset0)`
- `offset1 = sext9(in_offset1)`

### Uni-prediction weighting

When `in_bidir = 0`, the block computes a weighted sample from one interpolated predictor. The arithmetic matches:

- `shiftNum = IF_INTERNAL_PREC - X265_DEPTH = 6`
- `shift = in_log2_weight_denom + shiftNum`
- `round = 0` if `shift == 0`, else `1 << (shift - 1)`

Source selection is:

- if `in_src0 = 1`, use `p = p0`, `w = w0`, `offset = offset0`
- else use `p = p1`, `w = w1`, `offset = offset1`

The weighted sample before clipping is:

- `sum = w * (p + IF_INTERNAL_OFFS) + round`
- if `shift == 0`, `base = sum`
- else `base = arithmetic_right_shift(sum, shift)`

The final un-clipped result is:

- `value = base + offset`

`out_pixel` is `value` clipped to `[0, 255]`.

`arithmetic_right_shift(sum, shift)` means signed right shift with sign extension. `shift` is in the range `6..21`, so no shift-count edge case occurs on legal inputs.

### Bi-prediction weighting

When `in_bidir = 1`, the block computes a weighted average of two interpolated predictors. The arithmetic matches:

- `shiftNum = IF_INTERNAL_PREC - X265_DEPTH = 6`
- `shift = in_log2_weight_denom + shiftNum + 1`
- `round = 0` if `shift == 0`, else `1 << (shift - 1)`
- `offset = offset0 + offset1`

The weighted sample before clipping is:

- `sum = w0 * (p0 + IF_INTERNAL_OFFS) + w1 * (p1 + IF_INTERNAL_OFFS) + round + (offset << (shift - 1))`
- if `shift == 0`, `value = sum`
- else `value = arithmetic_right_shift(sum, shift)`

`out_pixel` is `value` clipped to `[0, 255]`.

On legal inputs `shift` is in the range `7..22`, so no shift-count edge case occurs on legal inputs.

### Clipping

For both modes:

- if `value < 0`, `out_pixel = 0`
- else if `value > 255`, `out_pixel = 255`
- else `out_pixel = value`

### Edge cases

- `in_log2_weight_denom = 0` is legal and produces `shift = 6` in uni mode and `shift = 7` in bi mode.
- Negative weights and negative offsets are legal and use signed arithmetic throughout.
- `in_p0` and `in_p1` may be any signed 16-bit values; no saturation is applied before the final output clip.
- If `in_bidir = 0` and both `in_src0` and `in_src1` are 0, the block uses source 1. If both are 1, the block uses source 0.

## Reset Behavior

Reset is synchronous active-high via `rst`.

On reset:

- `out_valid = 0`
- `out_pixel = 0`

The block has no other architectural state.
