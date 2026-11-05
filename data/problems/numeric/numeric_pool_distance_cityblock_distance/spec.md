# CityblockDistanceF32x4

CityblockDistanceF32x4 computes the Manhattan distance between two 4-element FP32 vectors. One accepted input transaction supplies four elements of vector A and four elements of vector B, and the module produces one FP32 output equal to the left-folded sum of the four elementwise absolute differences. The arithmetic contract is bit-pattern FP32 using `fp32_sub`, `fp32_abs`, and `fp32_add` semantics, with preserved subnormal inputs and gradual underflow as defined below.

## Interface

### RTL interface

```systemverilog
module CityblockDistanceF32x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a0,
    input  logic [31:0] in_a1,
    input  logic [31:0] in_a2,
    input  logic [31:0] in_a3,
    input  logic [31:0] in_b0,
    input  logic [31:0] in_b1,
    input  logic [31:0] in_b2,
    input  logic [31:0] in_b3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_distance
);
```

### Behavior interface

```python
class CityblockDistanceF32x4:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_a0, in_a1, in_a2, in_a3,
        in_b0, in_b1, in_b2, in_b3,
    ):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_distance`: 32.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `CityblockDistanceF32x4`: top-level single-transaction kernel that accepts one 4-lane vector pair and emits one FP32 Manhattan distance result.

## Functional Behavior

The input supplies two four-element vectors `A = [in_a0, ..., in_a3]`
and `B = [in_b0, ..., in_b3]`. Compute
`d[i] = fp32_abs(fp32_sub(A[i], B[i]))` and produce
`out_distance = fp32_add(fp32_add(fp32_add(d[0], d[1]), d[2]), d[3])`.
This is an ordered Manhattan-distance sum, not a maximum or a balanced
reduction. A NaN difference yields canonical NaN; signed-zero differences
become positive zero before summation.

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

Reset is active-high and may be implemented synchronously or asynchronously. After reset, there is no architectural state. The module shall be able to accept an input transaction after reset according to the handshake protocol. `out_valid` shall be deasserted after reset. `out_distance` shall drive `32'h00000000` whenever no output transaction is being presented.
