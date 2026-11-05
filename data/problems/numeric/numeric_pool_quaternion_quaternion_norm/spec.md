# Quaternion Norm FP32

This module accepts one quaternion per input transaction, where each quaternion component is a 32-bit IEEE-754 single-precision value encoded as a raw `uint32` bit pattern, and produces one output transaction containing the quaternion Euclidean norm. The computation is the fixed operation sequence `sqrtf(a*a + b*b + c*c + d*d)` using FP32 arithmetic at every step. The reduction order is left-folded exactly as `(((a*a) + (b*b)) + (c*c)) + (d*d)`, and the final square root is applied to that sum.

## Interface

### RTL interface

```systemverilog
module quaternion_norm_fp32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a,
    input  logic [31:0] in_b,
    input  logic [31:0] in_c,
    input  logic [31:0] in_d,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_norm
);
```

### Behavior interface

```python
class QuaternionNormFp32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_a, in_b, in_c, in_d):
        pass
```

The `process()` method returns one dictionary per accepted input transaction:

```python
{"out_norm": <uint32>}
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_norm`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `quaternion_norm_fp32`: top-level single-clock quaternion-norm kernel.

## Functional Behavior

Let `q = [in_a, in_b, in_c, in_d]`. Compute each
`s[i] = fp32_mul(q[i], q[i])`, then
`norm_sq = fp32_add(fp32_add(fp32_add(s[0], s[1]), s[2]), s[3])`.
Set `out_norm = fp32_sqrt(norm_sq)`. This direct sum of squares may
overflow even when a rescaled norm algorithm would remain finite.

All numerical words are raw IEEE-754 binary32 encodings. `fp32_add`,
`fp32_sub`, `fp32_mul`, `fp32_div`, and `fp32_sqrt` denote the corresponding
operations with a separate round-to-nearest, ties-to-even step at each call;
there is no fused multiply-add or reassociation. Subnormal operands retain
their numerical values. Each exact result is rounded to binary32, including
at the zero/subnormal and subnormal/normal boundaries. Underflow is gradual;
overflow produces signed infinity. Compare output encodings exactly.

Any NaN operand or invalid operation produces canonical NaN `0x7FC00000`.
Invalid operations include opposite infinities in addition, equal infinities
in subtraction, zero times infinity, zero divided by zero, infinity divided
by infinity, and square root of a negative nonzero value. Multiplication and
division use the XOR of operand signs for zeros and infinities; a nonzero
finite value divided by zero gives signed infinity, and a finite value divided
by infinity gives signed zero. Addition of two negative zeros gives negative
zero; other exact cancellation gives positive zero. Subtraction is addition
after flipping the second sign. Square root preserves signed zero and positive
infinity. `fp32_abs` clears only the sign bit; `fp32_neg` flips only that bit.
The canonical arithmetic NaN is a project convention. Apply these rules in the order above, without a final clamp or special-case
override.

For example, `(in_a,in_b,in_c,in_d)=(0x1A800000,0,0,0)` returns
`0x1A800000`: the square of `2**-74` is the subnormal `0x00000002`.
In contrast, `in_a=0x1A000000` with the other components zero returns zero,
because its square `2**-150` is a midpoint that rounds to zero before sqrt.

Each input transfers on a rising edge with `in_valid && in_ready`;
each output transfers with `out_valid && out_ready`. Every accepted input
produces exactly one output in input order. The input producer holds valid
and payload stable while stalled; the module does the same for its output.
Latency is implementation-dependent, and accepted work must finish when the
environment continues servicing it. Reset cancels unfinished work and pending
outputs.

## Reset Behavior

Reset is synchronous active-high.

On any cycle with `rst == 1`, the architectural state is initialized to:
- no stored transactions
- `out_norm = 32'h00000000`

After reset, the module remains ready to accept an input transaction. No prior input is retained across reset. `out_valid` is deasserted until an output transaction corresponding to a post-reset accepted input is available.
