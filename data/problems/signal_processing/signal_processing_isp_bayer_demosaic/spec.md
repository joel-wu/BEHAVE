# Bayer Demosaic

Reconstruct one RGB pixel from a complete 5×5 Bayer neighborhood using the Malvar-He-Cutler linear interpolation kernels. The Bayer pattern and center-coordinate parity determine the measured channel and the orientation of each missing-channel filter.

## Interface

### RTL interface

```systemverilog
module signal_processing_isp_bayer_demosaic (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [1:0]   in_pattern,
    input  logic [4:0]   in_bit_depth,
    input  logic         in_row_lsb,
    input  logic         in_col_lsb,
    input  logic [15:0]  in_p00,
    input  logic [15:0]  in_p01,
    input  logic [15:0]  in_p02,
    input  logic [15:0]  in_p03,
    input  logic [15:0]  in_p04,
    input  logic [15:0]  in_p10,
    input  logic [15:0]  in_p11,
    input  logic [15:0]  in_p12,
    input  logic [15:0]  in_p13,
    input  logic [15:0]  in_p14,
    input  logic [15:0]  in_p20,
    input  logic [15:0]  in_p21,
    input  logic [15:0]  in_p22,
    input  logic [15:0]  in_p23,
    input  logic [15:0]  in_p24,
    input  logic [15:0]  in_p30,
    input  logic [15:0]  in_p31,
    input  logic [15:0]  in_p32,
    input  logic [15:0]  in_p33,
    input  logic [15:0]  in_p34,
    input  logic [15:0]  in_p40,
    input  logic [15:0]  in_p41,
    input  logic [15:0]  in_p42,
    input  logic [15:0]  in_p43,
    input  logic [15:0]  in_p44,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_r,
    output logic [15:0]  out_g,
    output logic [15:0]  out_b
);
```

Each input accepted on a rising clock edge with `in_valid && in_ready` produces one ordered RGB response. Output transfer uses `out_valid && out_ready`; an asserted output is held unchanged until accepted. Latency is not fixed.

### Behavior interface

```python
class BayerDemosaic:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_pattern, in_bit_depth, in_row_lsb, in_col_lsb,
                in_p00, in_p01, in_p02, in_p03, in_p04,
                in_p10, in_p11, in_p12, in_p13, in_p14,
                in_p20, in_p21, in_p22, in_p23, in_p24,
                in_p30, in_p31, in_p32, in_p33, in_p34,
                in_p40, in_p41, in_p42, in_p43, in_p44,
                ): ...
```

A call returns one dictionary with `out_r`, `out_g`, and `out_b`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_b`, `out_g`, `out_r`: 16.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `signal_processing_isp_bayer_demosaic`: one-pixel Bayer reconstruction and clipping.

## Functional Behavior

The 25 unsigned samples `P[y,x] = in_pyx` form a 5×5 window centered at `P[2,2]`. The caller supplies any symmetric image-boundary extension. The center's full-image row and column parities are `in_row_lsb` and `in_col_lsb`.

The 2×2 Bayer tiles at even row/even column are:

| in_pattern | row 0 | row 1 |
| --- | --- | --- |
| 0 | R G | G B |
| 1 | B G | G R |
| 2 | G R | B G |
| 3 | G B | R G |

Define `C=P[2,2]`, `H1=P[2,1]+P[2,3]`, `V1=P[1,2]+P[3,2]`, `H2=P[2,0]+P[2,4]`, `V2=P[0,2]+P[4,2]`, and `D=P[1,1]+P[1,3]+P[3,1]+P[3,3]`. The four reconstructed values are:

- `G = floor((8*C + 4*(H1+V1) - 2*(H2+V2))/16)`
- `H = floor((10*C + 8*H1 - 2*H2 + V2 - 2*D)/16)`
- `V = floor((10*C + 8*V1 - 2*V2 + H2 - 2*D)/16)`
- `X = floor((12*C + 4*D - 3*(H2+V2))/16)`

No intermediate truncation or overflow is applied before these divisions. Signed floor division followed by lower clipping is equivalent to clipping the original fractional reconstruction and converting to an unsigned integer.

Choose the un-clipped RGB result from the center's Bayer role:

| Center role | R | G | B |
| --- | --- | --- | --- |
| Red | C | G | X |
| Blue | X | G | C |
| Green on a red row | H | C | V |
| Green on a blue row | V | C | H |

Finally clip each channel to `[0, 2**min(in_bit_depth,16)-1]`. All depth codes are defined: depth 0 returns zeros; depths 16..31 use the full 16-bit output range. Input samples remain unsigned 16-bit codes; they are not truncated to the active depth before interpolation.

A flat window of value `C` reconstructs `(C,C,C)` before clipping, for every Bayer phase. The original measured center channel is preserved.

## Reset Behavior

Reset is synchronous, active-high. At the reset edge it cancels pending responses, deasserts `out_valid`, and clears all output data. There is no persistent pixel-history state.
