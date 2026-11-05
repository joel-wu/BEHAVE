# ComplexRealMulF32

ComplexRealMulF32 multiplies one complex FP32 sample by one real FP32 sample and produces one complex FP32 result sample. The complex input is presented as separate real and imaginary FP32 operands, and both components are multiplied by the same real operand. The arithmetic contract is IEEE-754 single-precision multiplication with round-to-nearest-even, the canonical arithmetic NaN, infinity handling, and gradual underflow defined below.

## Interface

### RTL interface

```systemverilog
module ComplexRealMulF32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_cmplx_real,
    input  logic [31:0] in_cmplx_imag,
    input  logic [31:0] in_real,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_real,
    output logic [31:0] out_imag
);
```

### Behavior interface

```python
class ComplexRealMulF32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_cmplx_real, in_cmplx_imag, in_real):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_imag`, `out_real`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
FP32 values are raw integer encodings, not host floating-point values.

## Modules

- `ComplexRealMulF32`: top-level single-sample complex-by-real FP32 multiplier.

## Functional Behavior

The input is a complex value `(in_cmplx_real, in_cmplx_imag)` and
a real scalar `in_real`. Return one dict with independent products:

```text
out_real = fp32_mul(in_cmplx_real, in_real)
out_imag = fp32_mul(in_cmplx_imag, in_real)
```

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

Reset is synchronous and active high.

On any cycle with `rst = 1` at the rising edge:
- `out_valid` shall be deasserted.
- `out_real` shall be `32'h00000000`.
- `out_imag` shall be `32'h00000000`.

The module contains no other architectural state.
