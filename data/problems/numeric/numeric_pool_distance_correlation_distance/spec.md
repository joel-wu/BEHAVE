# CorrelationDistanceF32Vec4

CorrelationDistanceF32Vec4 computes the correlation distance between two length-4 FP32 vectors and returns one FP32 result per accepted input transaction. The computation follows the centered-vector algorithm: compute the mean of each input vector, subtract the corresponding mean from each element, compute the average dot product and average powers of the centered vectors, form the denominator as `fp32_sqrt(pwra_avg * pwrb_avg)`, then return `fp32_sub(0x3F800000, fp32_div(dot_avg, denominator))`. All floating-point operations use FP32 semantics as defined below.

## Interface

### RTL interface

```systemverilog
module CorrelationDistanceF32Vec4 (
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
class CorrelationDistanceF32Vec4:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_a0, in_a1, in_a2, in_a3,
        in_b0, in_b1, in_b2, in_b3
    ): ...
```

The `process(...)` method returns one dict per accepted input transaction:

```python
{"out_distance": ...}
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

- `CorrelationDistanceF32Vec4`: top-level single-clock kernel that accepts one pair of 4-element FP32 vectors and produces one FP32 correlation-distance result.
- `fp32_add`: FP32 addition primitive.
- `fp32_sub`: FP32 subtraction primitive.
- `fp32_mul`: FP32 multiplication primitive.
- `fp32_div`: FP32 division primitive.
- `fp32_sqrt`: FP32 square-root primitive.

## Functional Behavior

One input supplies `A = [in_a0, ..., in_a3]` and
`B = [in_b0, ..., in_b3]`. Define `sum4(x)` as
`fp32_add(fp32_add(fp32_add(x[0], x[1]), x[2]), x[3])`.
Use the binary32 constants `ONE = 0x3F800000` and `FOUR = 0x40800000`:

```text
mean_a = fp32_div(sum4(A), FOUR)
mean_b = fp32_div(sum4(B), FOUR)
ca[i] = fp32_sub(A[i], mean_a)                 for i = 0, 1, 2, 3
cb[i] = fp32_sub(B[i], mean_b)                 for i = 0, 1, 2, 3
dot_avg = fp32_div(sum4([fp32_mul(ca[i], cb[i]) for i]), FOUR)
pwra_avg = fp32_div(sum4([fp32_mul(ca[i], ca[i]) for i]), FOUR)
pwrb_avg = fp32_div(sum4([fp32_mul(cb[i], cb[i]) for i]), FOUR)
denominator = fp32_sqrt(fp32_mul(pwra_avg, pwrb_avg))
out_distance = fp32_sub(ONE, fp32_div(dot_avg, denominator))
```

Return `{"out_distance": out_distance}`. The input vectors are not modified,
and transactions do not share numerical state. Do not clamp the result to
`[0, 2]` or bypass a zero denominator: constant vectors produce canonical NaN.

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

Architectural state after reset:

- No retained computation state.

Output port values after reset and before an accepted input has produced an output:

- `out_valid = 0`
- `out_distance = 0x00000000`

The module shall be able to accept a new input transaction whenever it is not holding an unconsumed output transaction. Each accepted input transaction eventually produces exactly one output transaction.
