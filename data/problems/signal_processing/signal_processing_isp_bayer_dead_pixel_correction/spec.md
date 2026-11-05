# Bayer Dead Pixel Correction

This block performs dead-pixel correction on a Bayer raw stream, one pixel transaction at a time, using a 5×5 same-color neighborhood. A pixel is classified as dead only when both of these conditions hold: it lies outside the open interval formed by the minimum and maximum of its eight same-color neighbors, and its absolute difference from each of those neighbors exceeds a programmable threshold. A detected dead pixel is replaced by the average of the two opposite neighbors taken along the direction with minimum second-derivative magnitude. Direction priority for equal gradients is vertical, then horizontal, then left diagonal, then right diagonal.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_bayer_dead_pixel_correction #(
    parameter int PIXEL_W = 16,
    parameter int THRESH_W = 16
) (
    input  logic                   clk,
    input  logic                   rst,

    input  logic                   in_valid,
    output logic                   in_ready,
    input  logic [PIXEL_W-1:0]     in_center,
    input  logic [PIXEL_W-1:0]     in_top_left,
    input  logic [PIXEL_W-1:0]     in_top_mid,
    input  logic [PIXEL_W-1:0]     in_top_right,
    input  logic [PIXEL_W-1:0]     in_mid_left,
    input  logic [PIXEL_W-1:0]     in_mid_right,
    input  logic [PIXEL_W-1:0]     in_bottom_left,
    input  logic [PIXEL_W-1:0]     in_bottom_mid,
    input  logic [PIXEL_W-1:0]     in_bottom_right,
    input  logic [THRESH_W-1:0]    in_threshold,

    output logic                   out_valid,
    input  logic                   out_ready,
    output logic [PIXEL_W-1:0]     out_pixel,
    output logic                   out_is_dead
);
```

### Behavior interface

```python
class BayerDeadPixelCorrection:
    def __init__(self, PIXEL_W=16, THRESH_W=16): ...

    def reset(self):
        pass

    def process(
        self,
        in_center,
        in_top_left,
        in_top_mid,
        in_top_right,
        in_mid_left,
        in_mid_right,
        in_bottom_left,
        in_bottom_mid,
        in_bottom_right,
        in_threshold,
    ):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_is_dead`: 1.
- `out_pixel`: PIXEL_W.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

## Modules

- `signal_processing_isp_bayer_dead_pixel_correction`: top-level dead-pixel detector and corrector for one Bayer sample and its eight same-color neighbors.

## Functional Behavior

All input pixels and the threshold are unsigned integers. `PIXEL_W` is the pixel bit width. `THRESH_W` is the threshold bit width. All arithmetic is exact integer arithmetic unless a division by 2 is stated; that division produces the floor of the real-valued average.

Both widths are positive integers and may differ. Each accepted input produces one ordered response. Transfers occur on rising clock edges when valid and ready are both high; an asserted output remains valid with unchanged data until accepted.

The eight neighbors occupy offsets (-2,-2), (-2,0), (-2,2), (0,-2), (0,2), (2,-2), (2,0), (2,2) relative to the center in Bayer image coordinates. They are not adjacent mixed-color pixels.

For each accepted input transaction, define the eight neighbors in this fixed order:

1. `top_left      = in_top_left`
2. `top_mid       = in_top_mid`
3. `top_right     = in_top_right`
4. `mid_left      = in_mid_left`
5. `mid_right     = in_mid_right`
6. `bottom_left   = in_bottom_left`
7. `bottom_mid    = in_bottom_mid`
8. `bottom_right  = in_bottom_right`

Let `center = in_center` and `threshold = in_threshold`.

The detector evaluates the following two conditions.

Condition 1, outside-neighborhood-range:
- `neighbor_min = min(top_left, top_mid, top_right, mid_left, mid_right, bottom_left, bottom_mid, bottom_right)`
- `neighbor_max = max(top_left, top_mid, top_right, mid_left, mid_right, bottom_left, bottom_mid, bottom_right)`
- `cond1` is true iff `center <= neighbor_min` or `center >= neighbor_max`.
- Equivalently, a pixel is considered good by this test only when `neighbor_min < center < neighbor_max`.

Condition 2, all neighbor differences exceed threshold:
- Compute the eight absolute differences:
  - `d0 = abs(center - top_left)`
  - `d1 = abs(center - top_mid)`
  - `d2 = abs(center - top_right)`
  - `d3 = abs(center - mid_left)`
  - `d4 = abs(center - mid_right)`
  - `d5 = abs(center - bottom_left)`
  - `d6 = abs(center - bottom_mid)`
  - `d7 = abs(center - bottom_right)`
- `cond2` is true iff every difference is strictly greater than `threshold`:
  - `d0 > threshold`
  - `d1 > threshold`
  - `d2 > threshold`
  - `d3 > threshold`
  - `d4 > threshold`
  - `d5 > threshold`
  - `d6 > threshold`
  - `d7 > threshold`

A pixel is detected as dead iff both conditions hold:
- `is_dead = cond1 and cond2`

If `is_dead` is false:
- `out_pixel = center`
- `out_is_dead = 0`

If `is_dead` is true, compute four directional gradients:
- `vertical_grad       = abs((2 * center) - top_mid - bottom_mid)`
- `horizontal_grad     = abs((2 * center) - mid_left - mid_right)`
- `left_diagonal_grad  = abs((2 * center) - top_left - bottom_right)`
- `right_diagonal_grad = abs((2 * center) - top_right - bottom_left)`

Then compute the replacement candidate in the direction of minimum gradient. The candidate averages are:
- `mean_vertical       = (top_mid + bottom_mid) >> 1`
- `mean_horizontal     = (mid_left + mid_right) >> 1`
- `mean_left_diagonal  = (top_left + bottom_right) >> 1`
- `mean_right_diagonal = (top_right + bottom_left) >> 1`

Direction selection uses the minimum gradient value with this exact priority when multiple gradients are equal to the minimum:
1. vertical
2. horizontal
3. left diagonal
4. right diagonal

Formally:
- if `vertical_grad <= horizontal_grad` and `vertical_grad <= left_diagonal_grad` and `vertical_grad <= right_diagonal_grad`, select vertical
- else if `horizontal_grad <= left_diagonal_grad` and `horizontal_grad <= right_diagonal_grad`, select horizontal
- else if `left_diagonal_grad <= right_diagonal_grad`, select left diagonal
- else select right diagonal

The output pixel is the selected directional mean:
- selected vertical: `out_pixel = mean_vertical`
- selected horizontal: `out_pixel = mean_horizontal`
- selected left diagonal: `out_pixel = mean_left_diagonal`
- selected right diagonal: `out_pixel = mean_right_diagonal`

When `is_dead` is true:
- `out_is_dead = 1`

The output value is always in the range `[0, 2^PIXEL_W - 1]` because every output is either an input pixel or the average of two input pixels of the same width. No additional saturation or clipping is applied.

This block is stateless at the algorithm level. The neighborhood values supplied on the input transaction already include any required image-border handling performed outside the block. Mirror padding, line buffering, and Bayer same-color neighborhood extraction are not part of this block.

## Reset Behavior

Reset is asynchronous and active-high. It cancels pending responses without changing the configured widths. On reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_pixel = 0`
- `out_is_dead = 0`

Architectural state after reset:
- no algorithmic state is stored
- if the implementation contains output holding registers for handshake decoupling, those registers are cleared to `out_pixel = 0`, `out_is_dead = 0`, and marked invalid so that `out_valid = 0`
