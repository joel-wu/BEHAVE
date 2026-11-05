# Swin Patch Merging

This block implements a bounded fixed-point instance of Swin's patch-merging
stage: bottom/right zero padding, 2x2 neighborhood concatenation, layer
normalization, and a bias-free linear reduction. Each input is a complete NHWC
feature map. The affine and reduction coefficients below define a fixed,
non-trained test instance; they are not runtime configuration inputs.

## Interface

### RTL interface

```systemverilog
module ai_ml_vision_patch_embed_swin_shifted_patch_merge #(
    parameter int DATA_W = 16,
    parameter int IN_H = 4,
    parameter int IN_W = 4,
    parameter int DIM = 2,
    parameter int OUT_DIM = 4
) (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [IN_H*IN_W*DIM*DATA_W-1:0] in_x,
    output logic out_valid,
    input  logic out_ready,
    output logic [((IN_H+1)/2)*((IN_W+1)/2)*OUT_DIM*DATA_W-1:0] out_y
);
```

Supported parameters: `DATA_W = 16`, `1 <= IN_H, IN_W <= 8`,
`1 <= DIM <= 4`, and `1 <= OUT_DIM <= 8`. Input and output elements are signed
Q8.8 integers: raw value `v` represents `v / 256`. All 16-bit patterns are legal.
The input shape is `[IN_H, IN_W, DIM]`; the output shape is
`[ceil(IN_H/2), ceil(IN_W/2), OUT_DIM]`. Flatten NHWC with channel fastest;
element index `i` occupies bits `[16*i +: 16]`, starting at the least-significant
end of the packed port.

### Behavior interface

```python
class AiMlVisionPatchEmbedSwinShiftedPatchMerge:
    def __init__(self, data_w=16, in_h=4, in_w=4, dim=2, out_dim=4): ...
    def reset(self): ...
    def process(self, in_x): ...
```

`process()` returns `{"out_y": packed_output}` for each accepted input.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: ceil(IN_H/2)*ceil(IN_W/2)*OUT_DIM*DATA_W.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings match the uppercase RTL parameters.

## Modules

- `ai_ml_vision_patch_embed_swin_shifted_patch_merge`: tensor-to-tensor patch
  merging with one ready/valid endpoint in each direction.

## Functional Behavior

Each input transfer produces exactly one output transfer, in input order.
Transfers occur on a rising clock edge when `valid && ready`. An offered input
remains stable until accepted. A pending output remains valid and unchanged
under backpressure. Latency is implementation-defined, and input/output
transfers may occur together.

For each output position `(oh, ow)`, gather a vector `m` of length `N = 4*DIM`
from the input's 2x2 neighborhood. Concatenate **top-left, bottom-left,
top-right, bottom-right**, each with its channels in order. Input positions
outside the bottom or right edge contribute zero, including to normalization.
There is no cyclic spatial shift in this patch-merging operator.

Let `tz(a,b)` be signed division truncated toward zero, for positive `b`.
Let `sat(v) = min(32767, max(-32768, v))`. All intermediate arithmetic below
uses mathematical integers, without overflow or implicit shifts.

1. **Layer normalization.** Compute
   `mean = tz(sum(m[k]), N)`,
   `d[k] = m[k] - mean`, and `var = sum(d[k]*d[k]) // N`.
   The epsilon is one raw Q16.16 unit, or real `2^-16`.
   Compute `inv = floor(65536 / sqrt(var + 1))` and
   `norm[k] = tz(d[k] * inv, 256)`.
   Equivalently, `inv = isqrt(2^32 // (var + 1))`, where `isqrt` is the
   exact floor square root. No floating-point implementation is required.
2. **Affine transform.** For `0 <= k < N`, use raw Q8.8 coefficients
   `gamma[k] = 256 + 32*((k % 3) - 1)` and
   `beta[k] = 16*((k % 5) - 2)`.
   Set `aff[k] = sat(tz(norm[k]*gamma[k], 256) + beta[k])`.
3. **Linear reduction.** For `0 <= o < OUT_DIM`, the fixed raw Q8.8 matrix is
   `weight[o][k] = 32*((((o+1)*(k+3) + o*k) % 17) - 8)`.
   Compute `y[oh][ow][o] = sat(sum(tz(aff[k]*weight[o][k], 256)))`.
   Truncate each product before summing; there is no projection bias.
4. Pack each signed result as its low 16-bit two's-complement representation
   in the output layout above.

Constant neighborhoods have zero normalized values before the affine bias.
Odd dimensions use the same operations after zero padding; padding is not
excluded from the mean or variance.

## Reset Behavior

Reset is synchronous active-high. It clears `out_valid` and the output holding
register to zero and discards any pending response. `in_ready` is high after
the reset edge. There is no inter-transaction numerical state. Fixed
coefficients do not change on reset. Output data is unspecified when invalid.
