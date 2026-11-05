# ViT Patch Embedding — Linear Projection

Project one complete image patch into an embedding vector using a supplied dense weight matrix and optional bias. Each transaction computes every embedding component from all spatial positions and input channels. The patch geometry is fixed at elaboration; this block implements the non-resampled linear projection, not image patch extraction, weight resizing, or post-projection normalization.

## Interface

### RTL interface

```systemverilog
module ai_ml_vision_patch_embed_vit_patch_embed_linear #(
    parameter integer PATCH_H = 2,
    parameter integer PATCH_W = 2,
    parameter integer IN_CHANS = 3,
    parameter integer EMBED_DIM = 4,
    parameter integer DATA_W = 16
) (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [PATCH_H*PATCH_W*IN_CHANS*DATA_W-1:0] in_patch,
    input  logic [EMBED_DIM*PATCH_H*PATCH_W*IN_CHANS*DATA_W-1:0] in_weight,
    input  logic [EMBED_DIM*32-1:0] in_bias,
    input  logic in_use_bias,
    output logic out_valid,
    input  logic out_ready,
    output logic [EMBED_DIM*32-1:0] out_data
);
```

Supported parameter values are `1 <= PATCH_H,PATCH_W <= 4`, `1 <= IN_CHANS <= 4`, `1 <= EMBED_DIM <= 8`, and `8 <= DATA_W <= 16`. Let `K = PATCH_H*PATCH_W*IN_CHANS`. All packed buses are unsigned bit containers; their individual lanes have the signed interpretation below. Lane zero occupies the least significant bits.

- `in_patch`: `K` signed `DATA_W`-bit samples, in `(height, width, channel)` order with channel varying fastest. Sample `(h,w,c)` occupies lane `(h*PATCH_W+w)*IN_CHANS+c`.
- `in_weight`: `EMBED_DIM*K` signed `DATA_W`-bit coefficients. Lane `o*K+k` is the coefficient for embedding component `o` and patch lane `k`.
- `in_bias`: `EMBED_DIM` signed 32-bit biases, one per embedding component.
- `in_use_bias`: include each component's bias when one; ignore all bias bits when zero.
- `out_data`: `EMBED_DIM` two's-complement 32-bit result lanes, in embedding-component order.

Input and output transfers occur on rising clock edges when the corresponding valid and ready are both high and `rst` is low. The producer holds valid and the entire patch, matrix, bias and enable bundle unchanged while stalled. The block holds `out_valid` and `out_data` unchanged while the output is stalled. Every accepted input produces exactly one output vector, in order, unless reset intervenes. Latency and internal pipelining are not fixed by this interface.

### Behavior interface

```python
class PatchEmbedLinear:
    def __init__(self, patch_h=2, patch_w=2, in_chans=3, embed_dim=4, data_w=16): ...
    def reset(self): ...
    def process(self, in_patch, in_weight, in_bias, in_use_bias): ...
```

Constructor arguments correspond to the RTL parameters. Each call represents one accepted input and returns `{"out_data": packed_result}`. Python arguments and the returned value use the same nonnegative packed bit patterns as the RTL buses.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data`: EMBED_DIM*32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings match the uppercase RTL parameters.

## Modules

- `ai_ml_vision_patch_embed_vit_patch_embed_linear`: complete dense patch projection with ready/valid transport.

## Functional Behavior

Decode the packed patch as `x[k]`, the matrix as `W[o,k]`, and the bias as `b[o]`. For every `0 <= o < EMBED_DIM`, compute

```text
sum[o] = (b[o] if in_use_bias else 0) + sum(x[k] * W[o,k], k=0..K-1)
out_data[32*o +: 32] = sum[o] modulo 2^32
```

Products and sums are exact signed integer operations before the final 32-bit wrap. There is no saturation, rounding, or implicit fixed-point scaling. The bias is added once per component, not once per product. Every patch and matrix lane participates, including zero or negative values; all bit patterns of the declared buses are valid. Weights and biases may change between transactions and are not retained across calls. A sequence of image patches with the same matrix performs the corresponding non-overlapping patch projections in order.

## Reset Behavior

Reset is active-high and synchronous to `clk`. A rising edge with `rst=1` discards every pending output and sets `out_valid=0` and `out_data=0`; `in_ready` is then one. No transfer is counted during reset. The mathematical projection has no persistent state, and subsequent transactions are independent of pre-reset inputs.
