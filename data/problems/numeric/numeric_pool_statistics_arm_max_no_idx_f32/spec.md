# FP32 Max No Index 16

This module computes the maximum value of a fixed-length block of 16 IEEE-754 single-precision inputs without returning the index. The block is processed as a streaming reduction across 16 accepted input transactions: each accepted input updates the running maximum, and the 16th accepted input produces one output transaction containing the block maximum. The comparison rule is the ordinary floating-point ordered less-than relation on FP32 bit patterns, with unordered comparisons against NaN evaluating false.

## Interface

### RTL interface

```systemverilog
module fp32_max_no_idx16 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_value,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_max
);
```

### Behavior interface

```python
class Fp32MaxNoIdx16:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_value):
        pass
```

`process()` accepts one input element of the 16-element block. It returns `None` for accepted inputs numbered 1 through 15 within a block and returns `{"out_max": ...}` on accepted input number 16 within that block.

Return None for accepted elements 1 through 15 of each 16-element block, then exactly
one dictionary on element 16. Never return a list. The next call starts the next
block; reset discards a partial block.
Every returned dictionary contains exactly these fields (bit widths):

- `out_max`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `fp32_max_no_idx16`: top-level block maximum reducer for one 16-element FP32 block.

## Functional Behavior

The input port `in_value` carries one FP32 operand per accepted input transaction as a 32-bit IEEE-754 bit pattern.

The module maintains architectural state across input transactions:
- `running_max`: 32-bit FP32 bit pattern.
- `element_count`: integer in the range 0 to 15 inclusive, equal to the number of elements of the current block already consumed.

Each block consists of exactly 16 accepted input transactions. One output transaction is produced for each completed 16-input block, so the input/output cardinality is 16:1.

At the start of each block, `running_max` is initialised to `0xFF7FFFFF`, the FP32 encoding of the largest finite negative value.

For each accepted input transaction with operand `x = in_value`, the module computes an ordered floating-point comparison `running_max < x` using the following rules on the FP32 bit patterns:

- Let `exp(a) = a[30:23]` and `frac(a) = a[22:0]`.
- `a` is NaN iff `exp(a) == 8'hFF` and `frac(a) != 0`.
- `a` is zero iff `a[30:0] == 31'd0`; therefore `+0` (`0x00000000`) and `-0` (`0x80000000`) are both zero.
- If either operand is NaN, the comparison result is false.
- If both operands are zero, the comparison result is false.
- Otherwise, if the sign bits differ, the comparison result is true iff the first operand is negative. The both-zero case has already been excluded.
- Otherwise, if both operands are non-negative, the comparison result is the unsigned relation `(a[30:0] < b[30:0])`.
- Otherwise, both operands are negative, and the comparison result is the unsigned relation `(a[30:0] > b[30:0])`.

This comparison is exactly the predicate used to decide whether the new sample replaces the running maximum.

If `running_max < x` is true, the updated running maximum becomes `x`. Otherwise the updated running maximum remains `running_max`.

For accepted inputs 1 through 15 of a block:
- update `running_max` to the updated running maximum;
- increment `element_count` by 1;
- produce no output transaction.

For accepted input 16 of a block:
- first compute the updated running maximum including that 16th input;
- produce one output transaction with `out_max` equal to that updated running maximum;
- then reset block state so the next accepted input starts a new 16-element block with `running_max = 0xFF7FFFFF` and `element_count = 0`.

The result is therefore the maximum of the 16 inputs under the exact ordered-comparison predicate above, with the initial candidate `0xFF7FFFFF`.

Edge cases:
- If all 16 inputs are NaNs, every comparison is false and the result is `0xFF7FFFFF`.
- A NaN input never replaces the current maximum.
- `+Inf` (`0x7F800000`) is greater than every non-NaN finite value and greater than `-Inf` (`0xFF800000`).
- `-Inf` is less than every non-NaN finite value and less than `+Inf`.
- `+0` and `-0` compare equal, so when one zero is already held in `running_max`, the other zero does not replace it.
- A zero and a nonzero value use ordinary numerical ordering: `-0 < +1` and `-1 < +0` are both true.
- Subnormal numbers participate in comparison using the same sign-aware bit-pattern ordering rules and are not flushed.

Worked examples:
- If the 16 inputs are all NaNs, the output on the 16th accepted input is `0xFF7FFFFF`.
- If the first 15 inputs are NaNs and the 16th input is `0x00000000`, the output is `0x00000000`.
- If the running maximum before a sample is `0x80000000` (`-0`) and the new sample is `0x00000000` (`+0`), the comparison is false and the running maximum remains `0x80000000`.
- If the running maximum is the positive subnormal `0x00000001`, a later `-0` does not replace it: a positive number is greater than either zero.
- If the running maximum before a sample is `0xFF7FFFFF` and the new sample is `0x80000000`, the comparison is true, so the running maximum becomes `0x80000000`.

Valid/ready transfers occur on rising clock edges. An offered input must stay
stable until accepted; a pending output must stay valid and unchanged while
`out_ready` is low. Input gaps and output stalls do not change the block's
16:1 cardinality or ordering. An incomplete block produces no output until its
remaining elements arrive, and reset discards that partial block.

## Reset Behavior

Reset is synchronous and active high.

While `rst` is asserted at a rising clock edge, the architectural state is set to:
- `running_max = 0xFF7FFFFF`
- `element_count = 0`

After reset, before any completed 16-input block, no output transaction is pending. The output datapath state for `out_max` is initialised to `0x00000000`. Any RTL implementation shall ensure `out_valid = 0` after reset until a block completes. `in_ready` may be asserted whenever the module is able to accept the first or subsequent input of a block.
