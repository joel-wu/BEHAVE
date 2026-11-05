# FP32 radix-4 butterfly with programmable twiddles

One transaction transforms four complex inputs, applies three independently supplied complex twiddles, and returns four complex branches. Forward mode is unscaled; inverse mode multiplies every branch by 1/4 after twiddle multiplication. This is a single butterfly kernel, not a full FFT.

## Interface

### RTL interface

```systemverilog
module radix4_fft_butterfly_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_inverse,

    input  logic [31:0] in_xa_re,
    input  logic [31:0] in_xa_im,
    input  logic [31:0] in_xb_re,
    input  logic [31:0] in_xb_im,
    input  logic [31:0] in_xc_re,
    input  logic [31:0] in_xc_im,
    input  logic [31:0] in_xd_re,
    input  logic [31:0] in_xd_im,

    input  logic [31:0] in_w1_re,
    input  logic [31:0] in_w1_im,
    input  logic [31:0] in_w2_re,
    input  logic [31:0] in_w2_im,
    input  logic [31:0] in_w3_re,
    input  logic [31:0] in_w3_im,

    output logic        out_valid,
    input  logic        out_ready,

    output logic [31:0] out_y0_re,
    output logic [31:0] out_y0_im,
    output logic [31:0] out_y1_re,
    output logic [31:0] out_y1_im,
    output logic [31:0] out_y2_re,
    output logic [31:0] out_y2_im,
    output logic [31:0] out_y3_re,
    output logic [31:0] out_y3_im
);
```

### Behavior interface

```python
class Radix4FftButterflyF32:
    def __init__(self):
        ...

    def reset(self):
        ...

    def process(
        self,
        in_inverse,
        in_xa_re,
        in_xa_im,
        in_xb_re,
        in_xb_im,
        in_xc_re,
        in_xc_im,
        in_xd_re,
        in_xd_im,
        in_w1_re,
        in_w1_im,
        in_w2_re,
        in_w2_im,
        in_w3_re,
        in_w3_im,
    ):
        ...
```

`process` returns one dictionary containing the real and imaginary ports of
out_y0..out_y3.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0_im`, `out_y0_re`, `out_y1_im`, `out_y1_re`, `out_y2_im`, `out_y2_re`,
  `out_y3_im`, `out_y3_re`: 32.

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

- `radix4_fft_butterfly_f32`: the complete numerical kernel and ordered transaction interface.

## Functional Behavior

All sample and twiddle ports are IEEE-754 binary32 bit patterns. Each scalar
addition, subtraction and multiplication rounds separately to nearest, ties to
even; fused operations and reassociation are not permitted. Subnormal operands
retain their numerical value. Underflow is gradual, including nearest-even
rounding at the zero/subnormal and subnormal/normal boundaries. Compare final
output encodings exactly, without a tolerance. Overflow gives signed infinity;
any NaN operand, opposite-infinity addition or zero-times-infinity gives the
canonical quiet NaN `0x7fc00000` (a project sign/payload convention). Multiplication XORs signs, including zero;
exact additive cancellation gives +0, except `-0 + -0 = -0`.
Subtraction means addition after flipping the second operand's sign.

Write the four input pairs as `A,B,C,D`. With componentwise rounded addition
and subtraction, compute `P=A+C`, `Q=B+D`, `R=A-C`, `S=B-D`. The pre-twiddle
branches are:

| Branch | Forward (`in_inverse=0`) | Inverse (`in_inverse=1`) |
| --- | --- | --- |
| `Z0` | `P+Q` | same |
| `Z1` | `(R.re+S.im, R.im-S.re)` | `(R.re-S.im, R.im+S.re)` |
| `Z2` | `P-Q` | same |
| `Z3` | `(R.re-S.im, R.im+S.re)` | `(R.re+S.im, R.im-S.re)` |

Every operation in this table uses the scalar rounding rules above. For
`k=1,2,3`, let `Zk=(r,i)` and the supplied `wk=(c,s)`. Compute four separately
rounded products `a=r*c`, `b=i*s`, `d=i*c`, `e=r*s`. The twiddled branch is
`(a+b,d-e)` forward, or `(a-b,d+e)` inverse. Thus the stored positive sine term
is subtracted in the forward complex product; no constraint ties the three
twiddles together, and all their bit patterns are legal.

Output lanes use the in-place butterfly order **Z0, twiddled Z2, twiddled Z1,
twiddled Z3**, not natural frequency-bin order. In inverse mode only, multiply
each of the eight resulting scalar components by exactly `0x3e800000` (1/4),
rounding once more. `out_yj_re/im` return the resulting bit patterns.

## Reset Behavior

`rst` is synchronous active-high and takes priority over transfers. Reset
cancels pending transactions, clears output-valid and all output payloads to
zero, and returns the module to its input-ready state. No numerical history
persists between transactions. Deasserting reset starts a fresh sequence.
