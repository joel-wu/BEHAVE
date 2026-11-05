# ModifiedGivensRotmStep

This module executes one element-update step of the BLAS modified Givens transformation used by `DROTM`. Each accepted input transaction supplies one pair of source elements `x` and `y` together with the transformation parameter vector `dparam[0..4]`. The module computes one updated pair `(x', y')` according to the `dflag`-selected 2×2 matrix form defined by the BLAS routine. This is a signed-integer adaptation with no implicit fractional rescaling; the transaction computes one step only and does not iterate over vector length or strides.

## Interface

### RTL interface

```systemverilog
module modified_givens_rotm_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_x,
    input  logic [63:0]  in_y,
    input  logic [63:0]  in_dflag,
    input  logic [63:0]  in_dh11,
    input  logic [63:0]  in_dh21,
    input  logic [63:0]  in_dh12,
    input  logic [63:0]  in_dh22,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [63:0]  out_x,
    output logic [63:0]  out_y
);
```

### Behavior interface

```python
class ModifiedGivensRotmStep:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x, in_y, in_dflag, in_dh11, in_dh21, in_dh12, in_dh22): ...
```

The `process(...)` method returns a dict with keys `out_x` and `out_y`, including the unchanged input pair for the identity case.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_x`, `out_y`: 64.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `modified_givens_rotm_step`: top-level single-step modified Givens transform kernel.

## Functional Behavior

All inputs and outputs are 64-bit two's-complement signed integers encoded as raw 64-bit words. The module does not interpret them as IEEE-754 floating-point numbers. Arithmetic is signed integer arithmetic with wraparound to 64 bits after every multiply result truncation and after every add/subtract result.

Let:

- `x  = signed(in_x)`
- `y  = signed(in_y)`
- `dflag = signed(in_dflag)`
- `dh11 = signed(in_dh11)`
- `dh21 = signed(in_dh21)`
- `dh12 = signed(in_dh12)`
- `dh22 = signed(in_dh22)`

where `signed(.)` means two's-complement interpretation of the 64-bit input word.

The module implements one BLAS `DROTM` element update using the following `dflag` decode:

1. If `dflag == -2`, the transformation matrix is the identity:
   - `x' = x`
   - `y' = y`

2. Else if `dflag < 0`, use the full 2×2 matrix:
   - `x' = x*dh11 + y*dh12`
   - `y' = x*dh21 + y*dh22`

3. Else if `dflag == 0`, use the form:
   - `x' = x + y*dh12`
   - `y' = x*dh21 + y`

4. Else, use the form:
   - `x' = x*dh11 + y`
   - `y' = -x + dh22*y`

The operation order is exactly the order written above: each product is formed from the original input values `x` and `y`, not from any partially updated result, and `x'` and `y'` are computed from the same original pair.

Signed multiplication uses the full mathematical product, then truncates to the low 64 bits in two's-complement form before participation in the following addition or subtraction. Signed addition and subtraction also truncate to the low 64 bits. Equivalently, every intermediate assignment is reduced modulo `2^64`, then reinterpreted as signed when used by the next signed operation.

For the identity case `dflag == -2`, one output transaction is still produced and equals the input pair unchanged.

No other `dflag` values are treated as invalid. Any value other than exactly `-2` and `0` follows the ordered comparisons above.

The module is a 1:1 transaction mapping: every accepted input produces exactly one output transaction.

## Reset Behavior

Reset is synchronous active-high.

On any cycle with `rst == 1`, the architectural state is set to:
- `out_valid = 0`
- `out_x = 64'h0000000000000000`
- `out_y = 64'h0000000000000000`

The module contains no other architectural state.

After reset, the module may accept a new input transaction whenever it is not holding an unconsumed output. If an output transaction is buffered and `out_ready` is low, `out_valid` remains high and `out_x`/`out_y` remain unchanged until the output is accepted.
