# PVT-v2 Spatial-Reduction Attention, 4x4 by Four Channels

Compute one non-linear-attention PVT-v2 attention operator: queries retain all
16 positions, while a learned stride-two convolution and channel normalization
reduce the key/value sequence to four positions. This task keeps the spatial
reduction, normalization, projections and normalized weighted values. It is not
a complete PVT block or a floating-point bit-equivalent implementation. Every
request supplies its feature map and parameters; nothing is implicitly zero.

## Interface

### RTL interface

```systemverilog
module pvt_spatial_reduction_attn (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [1023:0] in_x,
    input logic [1023:0] in_sr_weight,
    input logic [63:0] in_sr_bias,
    input logic [63:0] in_norm_weight,
    input logic [63:0] in_norm_bias,
    input logic [255:0] in_q_weight,
    input logic [63:0] in_q_bias,
    input logic [511:0] in_kv_weight,
    input logic [127:0] in_kv_bias,
    input logic [255:0] in_proj_weight,
    input logic [63:0] in_proj_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [1023:0] out_data
);
```

### Behavior interface

```python
class AiMlVisionAttn2dPvtSpatialReductionAttn:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_sr_weight, in_sr_bias, in_norm_weight, in_norm_bias, in_q_weight, in_q_bias, in_kv_weight, in_kv_bias, in_proj_weight, in_proj_bias): ...
```

One accepted request produces one dictionary `{"out_data": packed_result}`
and one ordered RTL response.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_data`: 1024.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

The frame and all parameters are captured at
acceptance. All lanes are signed 16-bit Q8.8; lane i occupies bits `[16*i+15:16*i]`.
The output contains 64 lanes in the same position/channel layout as `in_x`.
There are no undefined operand words, optional parameters or retained weights.

| Port | Tensor shape / order, fastest index last |
| --- | --- |
| in_x | [row=4, column=4, channel=4] |
| in_sr_weight | [output_channel=4, input_channel=4, dy=2, dx=2] |
| in_sr_bias | [output_channel=4] |
| in_norm_weight, in_norm_bias | [channel=4] |
| in_q_weight, in_proj_weight | [output_channel=4, input_channel=4] |
| in_q_bias, in_proj_bias | [output_channel=4] |
| in_kv_weight | [output_channel=8, input_channel=4]; rows 0..3 are K, 4..7 are V |
| in_kv_bias | [output_channel=8] |

## Modules

`pvt_spatial_reduction_attn` is the top-level operator. The internal architecture
and latency are unrestricted provided requests/responses obey ready/valid and
the stated numerical function. A response and its data must remain stable
while `out_valid && !out_ready`; stalled input values are not repeatedly consumed.

## Functional Behavior

All quantities below are signed integers representing the specified lanes.
`floor(a/b)` rounds toward negative infinity; intermediate sums/products have
mathematical precision. `sat(v)=max(-32768,min(32767,v))`. A linear projection
is `L(z,W,b)[o]=sat(floor(sum_i W[o,i]*z[i]/256)+b[o])`.
There is one attention head with dimension four, hence score scale 1/2.

1. **Spatial reduction.** For `r,c in 0..1` and output channel `o in 0..3`,
   form `Z[r,c,o]=sat(floor(sum_(i,dy,dx) X[2*r+dy,2*c+dx,i]*Wsr[o,i,dy,dx]/256)+bsr[o])`,
   with `i in 0..3` and `dy,dx in 0..1`. There is no padding. Flatten the four
   reduced positions in row-major order p=2*r+c.
2. **Channel normalization.** Independently at each reduced position,
   `mu=floor(sum_o Z[p,o]/4)`, `v=floor(sum_o (Z[p,o]-mu)^2/4)`, and
   `d=floor(sqrt(v+1))`. Then `n[o]=floor(256*(Z[p,o]-mu)/d)` and
   `U[p,o]=sat(floor(n[o]*in_norm_weight[o]/256)+in_norm_bias[o])`.
   The integer square root is exact. The positive +1 corresponds to epsilon
   1/65536 in real squared-value units and guarantees d>=1, including constant
   channels. This specifies a finite normalization rather than library float rounding.
3. **Q/K/V projection.** `Q[t]=L(X[t],Wq,bq)` for all 16 full-resolution
   positions. Project each of the four U rows using `Wkv,bkv`; split its eight
   outputs into four K lanes followed by four V lanes.
4. **Attention weights.** For each t and reduced position p,
   `s[p]=floor(sum_i Q[t,i]*K[p,i]/512)` and `smax=max_p s[p]`.
   Set `j[p]=floor((smax-s[p]+8)/16)`. Use E[j[p]] below for j<=128 and zero
   otherwise. The largest score has weight E[0]=65536, so the weight sum is
   never zero. This is an explicitly quantized exponential, not a top-k mask.
5. **Mix and project.** `C[t,o]=floor(sum_p E_p*V[p,o]/sum_p E_p)`.
   The output row is `Y[t]=L(C[t],Wproj,bproj)`. Pack all sixteen rows in
   row-major/channel order. There is no residual, MLP, dropout or extra activation.

The exact E[0..128] integer words (round(65536*exp(-j/16)) with ties upward) are:

```text
65536, 61565, 57835, 54331, 51039, 47947, 45042, 42313, 39750, 37341, 35079, 32954
30957, 29081, 27319, 25664, 24109, 22649, 21276, 19987, 18776, 17639, 16570, 15566
14623, 13737, 12905, 12123, 11388, 10698, 10050, 9441, 8869, 8332, 7827, 7353
6907, 6489, 6096, 5726, 5380, 5054, 4747, 4460, 4190, 3936, 3697, 3473
3263, 3065, 2879, 2705, 2541, 2387, 2243, 2107, 1979, 1859, 1746, 1641
1541, 1448, 1360, 1278, 1200, 1128, 1059, 995, 935, 878, 825, 775
728, 684, 642, 604, 567, 533, 500, 470, 442, 415, 390, 366
344, 323, 303, 285, 268, 252, 236, 222, 209, 196, 184, 173
162, 153, 143, 135, 127, 119, 112, 105, 99, 93, 87, 82
77, 72, 68, 64, 60, 56, 53, 50, 47, 44, 41, 39
36, 34, 32, 30, 28, 27, 25, 23, 22
```

As a simple calibration, take identical channel vector `[256,-256,256,-256]`
at every input position, let the reduction select each channel at dy=dx=0,
set normalization weights to 256 and biases to zero, set Q weights/biases to
zero, make both K and V projections identities, and use an identity output
projection. Every output position is `[256,-256,256,-256]`: queries give uniform
weights over four equal normalized values. Neither the input nor the output is zero.

## Reset Behavior

`rst` is active-high and synchronous. While reset is asserted `in_ready=0`;
at a reset edge pending work is discarded, `out_valid=0` and `out_data=0`.
After reset release there is no architectural history; the next accepted
frame uses only its own data and parameters.
