# Complex FP32 Multiply

This module accepts one complex number from operand A and one complex number from operand B, each represented as separate IEEE-754 single-precision real and imaginary components, and produces their complex product. The computation is the scalar complex multiply used for each element of an interleaved complex-vector multiply: real output is `a_real*b_real - a_imag*b_imag`, imaginary output is `a_real*b_imag + a_imag*b_real`. Arithmetic is defined in terms of `fp32_mul`, `fp32_sub`, and `fp32_add`, in that order, so the rounding and exceptional-value behavior are fully determined.

## Interface

### RTL interface

```systemverilog
module complex_fp32_multiply (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a_real,
    input  logic [31:0] in_a_imag,
    input  logic [31:0] in_b_real,
    input  logic [31:0] in_b_imag,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_real,
    output logic [31:0] out_imag
);
```

### Behavior interface

```python
class ComplexFp32Multiply:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a_real, in_a_imag, in_b_real, in_b_imag):
        pass
```

Each call represents one accepted operand pair and returns exactly
`{"out_real": real_bits, "out_imag": imag_bits}`. All four arguments and
both returned fields are unsigned 32-bit FP32 encodings, not Python float or
complex values. The return is never `None` or a list. The model is stateless;
`reset()` returns `None` without emitting a result.

## Modules

- `complex_fp32_multiply`: top-level single-transaction complex-by-complex FP32 multiplier.

## Functional Behavior

The input is two complex values `(in_a_real, in_a_imag)` and
`(in_b_real, in_b_imag)`. Compute the two output fields:

```text
out_real = fp32_sub(fp32_mul(in_a_real, in_b_real), fp32_mul(in_a_imag, in_b_imag))
out_imag = fp32_add(fp32_mul(in_a_real, in_b_imag), fp32_mul(in_a_imag, in_b_real))
```

The second operand is not conjugated. Each of the four products rounds
before its final addition or subtraction.

All numerical words are raw IEEE-754 binary32 encodings. `fp32_add`,
`fp32_sub`, `fp32_mul`, `fp32_div`, and `fp32_sqrt` denote the corresponding
operations with a separate round-to-nearest, ties-to-even step at each call;
there is no fused multiply-add or reassociation. Subnormal operands retain
their numerical value. Underflow is gradual: round to the nearest binary32
value, choosing an even significand at a midpoint, including the zero/subnormal
and subnormal/normal boundaries. Overflow produces signed infinity.
Compare the final output encodings exactly, without an error tolerance.

Any NaN operand or invalid arithmetic operation produces canonical NaN `0x7FC00000`; this sign/payload is a project convention.
Invalid operations include opposite infinities in addition, equal infinities
in subtraction, zero times infinity, zero divided by zero, infinity divided
by infinity, and square root of a negative nonzero value. Multiplication and
division use the XOR of operand signs for zeros and infinities; a nonzero
finite value divided by zero gives signed infinity, and a finite value divided
by infinity gives signed zero. Addition of two negative zeros gives negative
zero; other exact cancellation gives positive zero. Subtraction is addition
after flipping the second sign. Square root preserves signed zero and positive
infinity. `fp32_abs` clears only the sign bit; `fp32_neg` flips only that bit.
Apply these rules in the stated order, without a final clamp or special-case
override.

Each input transfers on a rising edge with `in_valid && in_ready`;
each output transfers with `out_valid && out_ready`. Every accepted input
produces exactly one output in input order. The input producer holds valid
and payload stable while stalled; the module does the same for its output.
Latency is implementation-dependent, and accepted work must finish when the
environment continues servicing it. Reset cancels unfinished work and pending
outputs.

## Reset Behavior

Reset is synchronous active-high.

On any rising clock edge with `rst == 1`:
- `out_valid` shall be `0`.
- `out_real` shall be `32'h00000000`.
- `out_imag` shall be `32'h00000000`.

Architectural state after reset:
- no input transaction is pending,
- no output transaction is pending,
- the module is ready to accept a new input transaction on the first cycle after reset in which it chooses to assert `in_ready`.
