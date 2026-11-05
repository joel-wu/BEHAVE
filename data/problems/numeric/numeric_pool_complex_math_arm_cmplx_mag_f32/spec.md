# ComplexMagnitudeFp32

This module computes the magnitude of one complex FP32 sample per accepted input transaction. The input sample is provided as separate IEEE-754 single-precision real and imaginary components. The magnitude is computed as `fp32_sqrt(fp32_add(fp32_mul(a, a), fp32_mul(b, b)))`, where `a` is the real component and `b` is the imaginary component. The kernel is a 1:1 transaction mapper: each accepted input produces exactly one output transaction.

## Interface

### RTL interface

```systemverilog
module complex_magnitude_fp32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_real,
    input  logic [31:0] in_imag,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_mag
);
```

### Behavior interface

```python
class ComplexMagnitudeFp32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_real, in_imag):
        pass
```

Each call represents one accepted sample and returns exactly
`{"out_mag": value}`. Both arguments and `value` are unsigned 32-bit FP32
encodings, not Python floats. The return is never `None` or a list, including
NaNs and infinities. The model is stateless; `reset()` returns `None` and emits
no record.

## Modules

- `complex_magnitude_fp32`: top-level module implementing one complex-sample magnitude computation.

## Functional Behavior

For each `(in_real, in_imag)`, compute:

```text
real_sq = fp32_mul(in_real, in_real)
imag_sq = fp32_mul(in_imag, in_imag)
sum_sq = fp32_add(real_sq, imag_sq)
out_mag = fp32_sqrt(sum_sq)
```

This is the direct sum-of-squares
algorithm, not an overflow-avoiding rescaled hypot. Both zero inputs yield
positive zero; a NaN input yields canonical NaN. For `(1.0, 2.0)`, the output
is `0x400F1BBD` (sqrt(5), rounded to binary32).

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

Reset is active-high and may be implemented synchronously or asynchronously. After reset, the architectural state is empty: there is no buffered accepted input and no pending output transaction. The output data register, if any, shall be initialized to `32'h00000000`. `out_valid` shall be low until an input transaction has been accepted and its corresponding output is available. `in_ready` may be high immediately after reset if the module is able to accept a new input.
