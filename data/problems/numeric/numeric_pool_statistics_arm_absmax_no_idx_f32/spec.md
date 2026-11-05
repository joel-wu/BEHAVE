# AbsMaxF32x16

AbsMaxF32x16 computes the absolute maximum of 16 binary32 samples, using the scalar left-to-right
ordered-comparison recurrence. Input transport and memory boundaries are
explicit below; no transaction changes the numerical state of another.

## Interface

### RTL interface

```systemverilog
module abs_max_f32x16 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,
    input  logic [31:0] in_x4,
    input  logic [31:0] in_x5,
    input  logic [31:0] in_x6,
    input  logic [31:0] in_x7,
    input  logic [31:0] in_x8,
    input  logic [31:0] in_x9,
    input  logic [31:0] in_x10,
    input  logic [31:0] in_x11,
    input  logic [31:0] in_x12,
    input  logic [31:0] in_x13,
    input  logic [31:0] in_x14,
    input  logic [31:0] in_x15,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_absmax
);
```

### Behavior interface

```python
class AbsMaxF32x16:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_x0, in_x1, in_x2, in_x3,
            in_x4, in_x5, in_x6, in_x7,
            in_x8, in_x9, in_x10, in_x11,
            in_x12, in_x13, in_x14, in_x15):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_absmax`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `abs_max_f32x16`: fixed-length absolute-maximum reducer.

## Functional Behavior

Every accepted input supplies 16 input words and produces exactly one
ordered output. Each word is an IEEE-754 binary32 bit pattern carried as an
unsigned 32-bit integer. The scan order is `in_x0` through `in_x15`.

For each scanned word, clear bit 31 to obtain its absolute magnitude. Initialize
`best` to the first magnitude and `index` to zero. For each subsequent position
`i`, replace both with `(magnitude,i)` only when that magnitude is strictly
greater than `best` under ordered binary32 comparison.
The output `out_absmax` is the final magnitude. Ties retain
the earliest position.

The comparison is false if either operand is NaN. Thus a NaN in position zero
remains the result; later NaNs never replace the current result. Absolute
value clears only the sign bit, preserving NaN payload and quiet/signaling
bits. Either signed zero becomes positive zero, either infinity becomes
positive infinity, and subnormals retain their magnitudes without flushing.
For non-NaN, sign-cleared words, unsigned bit-pattern ordering is exactly
numeric ordering.

Input and output transfers occur on rising edges with valid and ready high.
An offered input remains stable until accepted. Pending output validity and
payload remain stable while `out_ready` is low. Internal latency is not fixed.
Reset discards pending commands and outputs.

## Reset Behavior

Reset is synchronous active-high. On a cycle with `rst == 1`, the module returns to its idle state.

Architectural state after reset:
- no stored input vector
- no pending output transaction

Output port values after reset:
- `out_valid = 0`
- `out_absmax = 32'h00000000`

The module may accept a new input transaction after reset is deasserted.
