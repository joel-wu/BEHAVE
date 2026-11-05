# WinogradF43Full

One supplied 6x6 input tile and 3x3 kernel produce a 4x4 stride-one cross-correlation tile. This is a finite integer specialization of the archived timm convolution source. The supplied tile includes any required zero-padded halo; the block does not infer image boundaries. The legacy Winograd name denotes the tile geometry, not a required transform implementation.

## Interface

### RTL interface

```systemverilog
module WinogradF43Full (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_d00,
    input  logic [15:0]  in_d01,
    input  logic [15:0]  in_d02,
    input  logic [15:0]  in_d03,
    input  logic [15:0]  in_d04,
    input  logic [15:0]  in_d05,
    input  logic [15:0]  in_d10,
    input  logic [15:0]  in_d11,
    input  logic [15:0]  in_d12,
    input  logic [15:0]  in_d13,
    input  logic [15:0]  in_d14,
    input  logic [15:0]  in_d15,
    input  logic [15:0]  in_d20,
    input  logic [15:0]  in_d21,
    input  logic [15:0]  in_d22,
    input  logic [15:0]  in_d23,
    input  logic [15:0]  in_d24,
    input  logic [15:0]  in_d25,
    input  logic [15:0]  in_d30,
    input  logic [15:0]  in_d31,
    input  logic [15:0]  in_d32,
    input  logic [15:0]  in_d33,
    input  logic [15:0]  in_d34,
    input  logic [15:0]  in_d35,
    input  logic [15:0]  in_d40,
    input  logic [15:0]  in_d41,
    input  logic [15:0]  in_d42,
    input  logic [15:0]  in_d43,
    input  logic [15:0]  in_d44,
    input  logic [15:0]  in_d45,
    input  logic [15:0]  in_d50,
    input  logic [15:0]  in_d51,
    input  logic [15:0]  in_d52,
    input  logic [15:0]  in_d53,
    input  logic [15:0]  in_d54,
    input  logic [15:0]  in_d55,

    input  logic [15:0]  in_g00,
    input  logic [15:0]  in_g01,
    input  logic [15:0]  in_g02,
    input  logic [15:0]  in_g10,
    input  logic [15:0]  in_g11,
    input  logic [15:0]  in_g12,
    input  logic [15:0]  in_g20,
    input  logic [15:0]  in_g21,
    input  logic [15:0]  in_g22,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_y00,
    output logic [31:0]  out_y01,
    output logic [31:0]  out_y02,
    output logic [31:0]  out_y03,
    output logic [31:0]  out_y10,
    output logic [31:0]  out_y11,
    output logic [31:0]  out_y12,
    output logic [31:0]  out_y13,
    output logic [31:0]  out_y20,
    output logic [31:0]  out_y21,
    output logic [31:0]  out_y22,
    output logic [31:0]  out_y23,
    output logic [31:0]  out_y30,
    output logic [31:0]  out_y31,
    output logic [31:0]  out_y32,
    output logic [31:0]  out_y33
);
```

### Behavior interface

```python
class WinogradF43Full:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_d00, in_d01, in_d02, in_d03, in_d04, in_d05,
        in_d10, in_d11, in_d12, in_d13, in_d14, in_d15,
        in_d20, in_d21, in_d22, in_d23, in_d24, in_d25,
        in_d30, in_d31, in_d32, in_d33, in_d34, in_d35,
        in_d40, in_d41, in_d42, in_d43, in_d44, in_d45,
        in_d50, in_d51, in_d52, in_d53, in_d54, in_d55,
        in_g00, in_g01, in_g02,
        in_g10, in_g11, in_g12,
        in_g20, in_g21, in_g22
    ): ...
```

The returned dict contains these keys: `out_y00`, `out_y01`, `out_y02`, `out_y03`, `out_y10`, `out_y11`, `out_y12`, `out_y13`, `out_y20`, `out_y21`, `out_y22`, `out_y23`, `out_y30`, `out_y31`, `out_y32`, `out_y33`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y00`, `out_y01`, `out_y02`, `out_y03`, `out_y10`, `out_y11`, `out_y12`,
  `out_y13`, `out_y20`, `out_y21`, `out_y22`, `out_y23`, `out_y30`, `out_y31`,
  `out_y32`, `out_y33`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `WinogradF43Full`: top-level single-tile 3x3 cross-correlation block.

## Functional Behavior

All `in_dij` and `in_gij` ports are signed 16-bit integers in two's-complement format. Each input value is interpreted as an integer, not as fixed-point. Each output `out_yij` is a signed 32-bit two's-complement integer bit pattern.

The block performs one 3x3 convolution over a 6x6 input tile and returns the 4x4 output tile corresponding to stride-1 convolution over that tile. The 6x6 input tile is the already-padded receptive field tile for those 4x4 outputs. Any SAME-padding effect is therefore represented by zeros in the supplied `in_dij` values at positions that lie outside the original image. No additional boundary logic exists inside the block.

Let `d[r][c]` for `r,c in [0,5]` be the signed integer values from `in_drc`. Let `g[r][c]` for `r,c in [0,2]` be the signed integer values from `in_grc`.

For r,c=0..3, the exact integer result is `Y[r][c] = sum(kr=0..2, kc=0..2, d[r+kr][c+kc]*g[kr][kc])`. The kernel is not reversed. Any architecture yielding these results is permitted, including direct MACs or an exact Winograd transform.

The final output conversion is:
- compute each `Y[r][c]` as a mathematical integer,
- wrap modulo `2^32`,
- drive the wrapped 32-bit result on `out_yrc`.

Equivalent statement in signed form: if the exact integer result lies outside `[-2^31, 2^31-1]`, the output wraps in two's-complement modulo `2^32`.

There is no bias term, no activation, no channel summation across multiple input channels, no grouping, no dilation, and no stride other than 1.

## Reset Behavior

The design uses asynchronous active-high reset `rst`. Reset initializes all architectural state to zero. This block has no algorithmic state between transactions. After reset, `out_valid` is 0. While no output transaction is pending, all output data ports `out_y00` through `out_y33` are 0. `in_ready` may be 1 immediately after reset deassertion.
