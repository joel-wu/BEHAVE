# Quaternion Normalize F32

This module accepts one quaternion per input transaction, where each quaternion element is a 32-bit IEEE-754 single-precision value carried as a raw `uint32` bit pattern, and produces one normalized quaternion per output transaction. The normalization algorithm is the scalar CMSIS-DSP formulation: compute the squared Euclidean norm as a left-associated sum of four self-products, compute its square root, then divide each original component by that norm. Floating-point arithmetic is defined by `fp_lib` operations and therefore uses FP32 round-to-nearest-even semantics with `fp_lib` special-case behavior for NaN, infinities, zeros, and subnormals.

## Interface

### RTL interface

```systemverilog
module QuaternionNormalizeF32 (
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
    output logic [31:0] out_n0,
    output logic [31:0] out_n1,
    output logic [31:0] out_n2,
    output logic [31:0] out_n3
);
```

### Behavior interface

```python
class QuaternionNormalizeF32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_q0, in_q1, in_q2, in_q3):
        pass
```

The `process(...)` method returns one dict per accepted input transaction with keys:
`out_n0`, `out_n1`, `out_n2`, `out_n3`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_n0`, `out_n1`, `out_n2`, `out_n3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `QuaternionNormalizeF32`: top-level quaternion normalization kernel.

## Functional Behavior

Let `q = [in_q0, in_q1, in_q2, in_q3]`. Compute each
`s[i] = fp32_mul(q[i], q[i])`, then
`norm_sq = fp32_add(fp32_add(fp32_add(s[0], s[1]), s[2]), s[3])` and
`norm = fp32_sqrt(norm_sq)`. Set `out_n0` through `out_n3` to `fp32_div(q[i], norm)` respectively.
The divisions are separate operations, not multiplication by a rounded
reciprocal. An all-zero input produces four canonical NaNs; no identity
quaternion or other fallback is substituted.

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

Reset is synchronous active-high reset.

Architectural state consists only of the registered output payload and output-valid state required by the handshake shell.

On reset:
- `out_valid = 0`
- `out_n0 = 0x00000000`
- `out_n1 = 0x00000000`
- `out_n2 = 0x00000000`
- `out_n3 = 0x00000000`

There is no additional architectural state.
