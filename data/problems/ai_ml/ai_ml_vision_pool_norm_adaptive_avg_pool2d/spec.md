# ai_ml_vision_pool_norm_adaptive_avg_pool2d

This block performs 2-dimensional adaptive average pooling on a single-channel signed-integer feature map. Each accepted input transaction supplies an input height, input width, desired output height, desired output width, and a packed 4×4 tensor of 16 signed 16-bit samples in row-major order. The block computes the adaptive average-pooled output over the active `in_h × in_w` region using the standard adaptive bin boundaries `start = floor(i * in / out)` and `end = ceil((i + 1) * in / out)`, with integer averaging rounded toward negative infinity. One accepted input produces exactly one output transaction containing a packed 4×4 tensor whose active `out_h × out_w` region holds the pooled values and whose remaining elements are zero.

## Interface

### RTL interface

```systemverilog
module ai_ml_vision_pool_norm_adaptive_avg_pool2d (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [3:0]   in_h,
    input  logic [3:0]   in_w,
    input  logic [3:0]   out_h,
    input  logic [3:0]   out_w,
    input  logic [255:0] in_data,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [255:0] out_data
);
```

### Behavior interface

```python
class AiMlVisionPoolNormAdaptiveAvgPool2d:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_h, in_w, out_h, out_w, in_data):
        pass
```

The `process(...)` method returns one dict per accepted input transaction:

```python
{"out_data": <uint256>}
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data`: 256.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_vision_pool_norm_adaptive_avg_pool2d`: top-level adaptive average pooling block.

## Functional Behavior

All arithmetic is integer arithmetic.

`in_data` is a packed array of 16 elements, each element a signed 16-bit two's-complement integer. Element `(r, c)` for row `r` and column `c`, with `0 <= r < 4` and `0 <= c < 4`, is stored in bits `16 * (4*r + c) +: 16`. Row-major unpacking is therefore:
- index 0 = `(0,0)`
- index 1 = `(0,1)`
- index 2 = `(0,2)`
- index 3 = `(0,3)`
- index 4 = `(1,0)`
- ...
- index 15 = `(3,3)`

The input dimensions and output dimensions are clamped independently to the inclusive range `[1, 4]` before use:
- `H_in = clamp(in_h, 1, 4)`
- `W_in = clamp(in_w, 1, 4)`
- `H_out = clamp(out_h, 1, 4)`
- `W_out = clamp(out_w, 1, 4)`

Only the active input region with `0 <= r < H_in` and `0 <= c < W_in` participates in pooling. Input elements outside that region are ignored.

The block computes one pooled result for every output coordinate `(orow, ocol)` such that `0 <= orow < H_out` and `0 <= ocol < W_out`.

For each active output row `orow`, the vertical pooling interval is:
- `h_start = floor(orow * H_in / H_out)`
- `h_end = ceil((orow + 1) * H_in / H_out)`

For each active output column `ocol`, the horizontal pooling interval is:
- `w_start = floor(ocol * W_in / W_out)`
- `w_end = ceil((ocol + 1) * W_in / W_out)`

The ceil operation is exact integer ceil:
- `ceil(a / b) = floor((a + b - 1) / b)` for positive integers `a`, `b`

The pooled bin for output `(orow, ocol)` contains all input elements `(r, c)` satisfying:
- `h_start <= r < h_end`
- `w_start <= c < w_end`

The bin population count is:
- `count = (h_end - h_start) * (w_end - w_start)`

For legal clamped dimensions, `count` is always at least 1.

The pooled sum is the signed sum of all signed 16-bit input elements in the bin. The output value is then computed by signed integer division of `sum` by `count`, rounded toward negative infinity:

- `avg = floor(sum / count)`

This rounding rule is mandatory. It is not truncation toward zero.

Because `count` is always positive, the required operation can be written equivalently as:
- if `sum >= 0` or `sum % count == 0`, then `avg = trunc(sum / count)`
- otherwise `avg = trunc(sum / count) - 1`

where `trunc(sum / count)` means signed division rounded toward zero.

The output element at `(orow, ocol)` is `avg` encoded as a signed 16-bit two's-complement value. The valid input range guarantees no overflow of the 16-bit output:
- each bin average lies between the minimum and maximum sample in that bin, inclusive.

All output elements with `orow >= H_out` or `ocol >= W_out` are written as zero.

`out_data` is packed in the same row-major 16×16-bit layout as `in_data`:
- bits `16 * (4*orow + ocol) +: 16` hold output `(orow, ocol)`

Exactly one output transaction is produced for each accepted input transaction.

Worked example, 3×3 input to 2×2 output:

- `H_in = W_in = 3`
- `H_out = W_out = 2`

Then:
- for `orow = 0`: `h_start = floor(0*3/2) = 0`, `h_end = ceil(1*3/2) = 2`
- for `orow = 1`: `h_start = floor(1*3/2) = 1`, `h_end = ceil(2*3/2) = 3`
- for `ocol = 0`: `w_start = 0`, `w_end = 2`
- for `ocol = 1`: `w_start = 1`, `w_end = 3`

So the four bins are rows/cols:
- `(0,0)`: rows `[0,1]`, cols `[0,1]`
- `(0,1)`: rows `[0,1]`, cols `[1,2]`
- `(1,0)`: rows `[1,2]`, cols `[0,1]`
- `(1,1)`: rows `[1,2]`, cols `[1,2]`

If the active 3×3 input is

```text
 1  2  3
 4  5  6
 7  8  9
```

then the output is

```text
floor((1+2+4+5)/4) = 3
floor((2+3+5+6)/4) = 4
floor((4+5+7+8)/4) = 6
floor((5+6+8+9)/4) = 7
```

Negative example:
- `sum = -3`, `count = 2`
- `trunc(sum / count) = -1`
- `sum % count != 0`
- therefore `avg = -2`

## Reset Behavior

Reset is active-high. Reset may be implemented synchronously or asynchronously.

After reset, the block has no queued output transaction pending. `out_valid` is deasserted. `in_ready` is asserted whenever the block is able to accept a new input transaction under the required 1-input-to-1-output handshake behavior. `out_data` is `0x0000000000000000000000000000000000000000000000000000000000000000`.

The block contains no architectural state other than any internal storage required to hold one pending output transaction and any control state required by the handshake.
