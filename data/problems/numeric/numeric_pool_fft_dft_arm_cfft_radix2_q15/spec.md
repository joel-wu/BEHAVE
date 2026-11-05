# Eight-point complex Q15 FFT/IFFT

One transaction transforms eight complex samples into eight natural-order frequency bins. Forward and inverse directions share a three-stage fixed-point radix-2 network, with overall gain approximately 1/8 in either direction; they are not an unscaled forward/inverse pair.

## Interface

### RTL interface

```systemverilog
module radix2_q15_butterfly8 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_ifft,
    input  logic [15:0] in_x0_re,
    input  logic [15:0] in_x0_im,
    input  logic [15:0] in_x1_re,
    input  logic [15:0] in_x1_im,
    input  logic [15:0] in_x2_re,
    input  logic [15:0] in_x2_im,
    input  logic [15:0] in_x3_re,
    input  logic [15:0] in_x3_im,
    input  logic [15:0] in_x4_re,
    input  logic [15:0] in_x4_im,
    input  logic [15:0] in_x5_re,
    input  logic [15:0] in_x5_im,
    input  logic [15:0] in_x6_re,
    input  logic [15:0] in_x6_im,
    input  logic [15:0] in_x7_re,
    input  logic [15:0] in_x7_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_x0_re,
    output logic [15:0] out_x0_im,
    output logic [15:0] out_x1_re,
    output logic [15:0] out_x1_im,
    output logic [15:0] out_x2_re,
    output logic [15:0] out_x2_im,
    output logic [15:0] out_x3_re,
    output logic [15:0] out_x3_im,
    output logic [15:0] out_x4_re,
    output logic [15:0] out_x4_im,
    output logic [15:0] out_x5_re,
    output logic [15:0] out_x5_im,
    output logic [15:0] out_x6_re,
    output logic [15:0] out_x6_im,
    output logic [15:0] out_x7_re,
    output logic [15:0] out_x7_im
);
```

### Behavior interface

```python
class Radix2Q15Butterfly8:
    def __init__(self):
        ...

    def reset(self):
        ...

    def process(
            self,
            in_ifft,
            in_x0_re, in_x0_im,
            in_x1_re, in_x1_im,
            in_x2_re, in_x2_im,
            in_x3_re, in_x3_im,
            in_x4_re, in_x4_im,
            in_x5_re, in_x5_im,
            in_x6_re, in_x6_im,
            in_x7_re, in_x7_im):
        ...
```

`process` returns one dictionary containing the real and imaginary ports of
out_x0..out_x7.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_x0_im`, `out_x0_re`, `out_x1_im`, `out_x1_re`, `out_x2_im`, `out_x2_re`,
  `out_x3_im`, `out_x3_re`, `out_x4_im`, `out_x4_re`, `out_x5_im`, `out_x5_re`,
  `out_x6_im`, `out_x6_re`, `out_x7_im`, `out_x7_re`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

An input transfers on `in_valid && in_ready`, and one corresponding
output transfers on `out_valid && out_ready`. Transactions stay ordered.
While stalled, each producer holds its valid signal and complete payload
stable. Internal latency and pipeline organization are implementation choices.

## Modules

- `radix2_q15_butterfly8`: the complete numerical kernel and ordered transaction interface.

## Functional Behavior

All sample components are signed 16-bit two's-complement integers. Arithmetic
below is over mathematical integers; `x >> n` is floor division by `2^n`.
`wrap(x)` retains the low 16 bits and reinterprets them as signed;
`sat(x) = min(32767,max(-32768,x))`. Operations on complex pairs apply separately
to real and imaginary components unless stated otherwise.

Initialize `v[i] = (in_xi_re,in_xi_im)` for `i=0..7`. The stored cosine/sine
coefficients are:

| Index | Cosine | Sine |
| --- | --- | --- |
| 0 | 32767 | 0 |
| 1 | 23169 | 23169 |
| 2 | 0 | 32767 |
| 3 | -23169 | 23169 |

For each stage length `L` in the order **8, 4**, process each block starting at
`b=0,L,...,8-L`. For `j=0..L/2-1`, read `A=v[b+j]`, `B=v[b+j+L/2]` and
coefficient `(C,S)` at index `8*j/L`. At `L=8` only, first replace **each**
component of `A` and `B` by its arithmetic right shift by one.

Compute `D=wrap(A-B)` and `T=wrap((A+B)>>1)`. With `D=(dr,di)`, compute
`p0=(dr*C)>>16`, `p1=(di*S)>>16`, `p2=(di*C)>>16`, `p3=(dr*S)>>16`.
Each product is shifted separately, before addition or subtraction:

| Mode | Bottom real | Bottom imaginary |
| --- | --- | --- |
| `in_ifft=0` | `wrap(p0+p1)` | `wrap(p2-p3)` |
| `in_ifft=1` | `wrap(p0-p1)` | `wrap(p2+p3)` |

Replace the pair by `T` and the bottom result. In the final length-2 stage,
each adjacent pair `(A,B)` becomes `(sat(A+B),sat(A-B))`; there is no twiddle
multiply or additional scaling. These temporary values are in bit-reversed
order. Output lanes `out_x0..out_x7` select temporary indices
**0,4,2,6,1,5,3,7**, respectively, restoring natural frequency-bin order.
Every output is encoded modulo `2^16`. All input bit patterns are legal.

## Reset Behavior

`rst` is synchronous active-high and takes priority over transfers. Reset
cancels pending transactions, clears output-valid and all output payloads to
zero, and returns the module to its input-ready state. No numerical history
persists between transactions. Deasserting reset starts a fresh sequence.
