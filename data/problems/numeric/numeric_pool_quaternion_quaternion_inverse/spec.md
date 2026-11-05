# Quaternion Inverse FP32

This module accepts one quaternion per input transaction and produces its inverse as four IEEE-754 single-precision values. The computation is the quaternion conjugate divided by the sum of squares of all four components, using single-precision floating-point operations in a fixed left-associated order: first form the squared norm with three sequential additions, then divide each conjugated component by that norm.

## Interface

### RTL interface

```systemverilog
module quaternion_inverse_f32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_q0,
    input  logic [31:0] in_q1,
    input  logic [31:0] in_q2,
    input  logic [31:0] in_q3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_q0,
    output logic [31:0] out_q1,
    output logic [31:0] out_q2,
    output logic [31:0] out_q3
);
```

### Behavior interface

```python
class QuaternionInverseF32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_q0, in_q1, in_q2, in_q3):
        pass
```

The `process()` method returns one dictionary with keys `out_q0`, `out_q1`, `out_q2`, and `out_q3` for every accepted input transaction.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_q0`, `out_q1`, `out_q2`, `out_q3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `quaternion_inverse_f32`: top-level module implementing one quaternion inverse per transaction.

## Functional Behavior

Inputs `in_q0` through `in_q3` give a quaternion in scalar-first
order. Form `s[i] = fp32_mul(in_qi, in_qi)`, then
`norm_sq = fp32_add(fp32_add(fp32_add(s[0], s[1]), s[2]), s[3])`.
The output components are:

```text
out_q0 = fp32_div(in_q0, norm_sq)
out_q1 = fp32_div(fp32_neg(in_q1), norm_sq)
out_q2 = fp32_div(fp32_neg(in_q2), norm_sq)
out_q3 = fp32_div(fp32_neg(in_q3), norm_sq)
```

Do not replace the four divisions with multiplication by a rounded reciprocal.
The all-zero quaternion produces four canonical NaNs; there is no identity
quaternion fallback.

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

Reset is synchronous and active-high.

On any cycle with `rst = 1`, the architectural state is set to its initial value:

- no input transaction is buffered,
- `out_valid` is deasserted,
- `out_q0 = 32'h00000000`
- `out_q1 = 32'h00000000`
- `out_q2 = 32'h00000000`
- `out_q3 = 32'h00000000`

There is no other architectural state.
