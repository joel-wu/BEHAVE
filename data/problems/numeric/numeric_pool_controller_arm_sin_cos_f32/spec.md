# SinCosDegreesF32

Compute sine and cosine of a binary32 angle in degrees using the CMSIS-DSP
512-interval sine table and cubic Hermite interpolation. One input produces
one output containing both results. This is a rounded table-based algorithm,
not a correctly rounded implementation of mathematical sine and cosine.

## Interface

### RTL interface

```systemverilog
module sin_cos_degrees_f32 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_theta,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_sin,
    output logic [31:0] out_cos
);
```

### Behavior interface

```python
class SinCosDegreesF32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_theta): ...
```

All three data fields carry raw IEEE-754 binary32 bits in unsigned integers.
`process` returns `{"out_sin": bits, "out_cos": bits}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_cos`, `out_sin`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
FP32 values are raw integer encodings, not host floating-point values.

### Transaction protocol

Inputs transfer on a
rising edge with `in_valid && in_ready`; outputs transfer with
`out_valid && out_ready`. A pending output and its valid signal remain stable
while blocked. Results preserve input order; latency is not fixed.

## Modules

`sin_cos_degrees_f32` contains the sine ROM, binary32 arithmetic and output
holding register. It has no cross-transaction mathematical state.

## Functional Behavior

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.

Every arithmetic operation below rounds separately to binary32, nearest-even;
no multiply-add is fused. Subnormal operands retain their values and
underflow is gradual. Comparisons treat both zeros
as equal. Negation reverses the sign bit. NaN or infinity at the input returns
`0x7FC00000` in both output fields without interpolation.

Constants are exact bit patterns: `I=0x3B360B61` (rounded 1/360),
`D=0x3C490FDB` (rounded 2*pi/512), and the exactly represented integers
2, 3 and 512. The table `T[k]`, `0 <= k <= 512`, is the CMSIS-DSP
`sinTable_f32`: round `sin(2*pi*k/512)` to eight decimal places, then round
that decimal value to binary32. Zero entries are `T[0]=T[256]=0x00000000`
and `T[512]=0x80000000`. Adjacent reads may access entry 512; they do not
wrap to entry 0. In particular `T[1]=0x3C490E92`, `T[128]=0x3F800000`
and `T[384]=0xBF800000`.

For a finite input `theta`:

1. Compute `a = theta * I`, then negate `a` if it compares less than zero.
   If `|a| >= 2**23`, set `r=+0`: binary32 values at that magnitude are
   already integral. Otherwise set `r = a - float32(trunc(a))`.
   Here truncation is toward zero and its result is below `2**23`, so the
   integer-to-binary32 conversion is exact. This defines the fractional part
   of the **rounded turn count**, including magnitudes beyond the source C
   integer-conversion range; it is not high-precision angle reduction.
2. Compute `b=512*r`, `s=trunc(b) & 511`, `c=(s+128) & 511`, and
   `f=b-float32(s)`.
3. Define the following interpolation operation in the displayed order:

   ```text
   delta = v1 - v0
   t = D*(d0+d1) - 2*delta
   t = f*t + (3*delta - (d1+2*d0)*D)
   t = f*t + d0*D
   H(v0,v1,d0,d1) = f*t + v0
   ```

   Compute `cos=H(T[c],T[c+1],-T[s],-T[s+1])` and
   `sin=H(T[s],T[s+1],T[c],T[c+1])`.
4. Negate `sin` if `theta < 0`; leave `cos` unchanged. Return their bits.

Both input zeros produce `(out_sin,out_cos)=(0x00000000,0x3F800000)`.
At 90 degrees the pair is `(0x3F800000,0x00000000)`.
For very large angles, early binary32 scaling loses the fractional turn;
the algorithm then returns signed zero for sine and one for cosine, rather
than claiming accurate trigonometric range reduction at that magnitude.

## Reset Behavior

Reset is synchronous active-high. A reset edge discards any pending result
and clears `out_valid`, `out_sin` and `out_cos` to zero. No pre-reset result
may subsequently transfer. After reset deasserts the module accepts new
inputs.
