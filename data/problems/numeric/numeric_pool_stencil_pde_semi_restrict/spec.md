# SemiRestrict3Tap1D

SemiRestrict3Tap1D computes one coarse-grid residual sample from a fine-grid residual stream using the 1D three-point semi-restriction operator implemented as `center + left_weight*left_neighbor + right_weight*right_neighbor`. The kernel accepts one fine-grid center sample and its two neighboring fine-grid samples per transaction, with either per-sample variable coefficients or block-constant coefficients selected by `in_constant_coefficient`. The arithmetic uses signed two's-complement integers and produces one signed coarse-grid sample per accepted input.

## Interface

### RTL interface

```systemverilog
module semi_restrict_3tap_1d (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_constant_coefficient,
    input  logic signed [31:0] in_r_center,
    input  logic signed [31:0] in_r_left,
    input  logic signed [31:0] in_r_right,
    input  logic signed [31:0] in_Rp0,
    input  logic signed [31:0] in_Rp1,

    output logic         out_valid,
    input  logic         out_ready,
    output logic signed [31:0] out_rc
);
```

### Behavior interface

```python
class SemiRestrict3Tap1D:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_constant_coefficient, in_r_center, in_r_left, in_r_right, in_Rp0, in_Rp1): ...
```

The `process(...)` method returns a dict with key `out_rc`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_rc`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `semi_restrict_3tap_1d`: top-level kernel that performs one 1D semi-restriction output transaction.

## Functional Behavior

Each accepted input transaction computes exactly one output transaction.

Input and output data ports are 32-bit signed two's-complement integers:
- `in_r_center`: fine-grid residual at the center point.
- `in_r_left`: fine-grid residual at the stencil point indexed by stencil entry 0.
- `in_r_right`: fine-grid residual at the stencil point indexed by stencil entry 1.
- `in_Rp0`: coefficient associated with `in_r_left`.
- `in_Rp1`: coefficient associated with `in_r_right`.
- `out_rc`: coarse-grid restricted residual.

`in_constant_coefficient` is a 1-bit mode control:
- `0`: variable-coefficient mode.
- `1`: constant-coefficient mode.

For this scoped kernel transaction, both modes use the same input coefficient values carried on `in_Rp0` and `in_Rp1`; the distinction is architectural only and does not change the numerical formula.

The output value is computed in this order:

1. Interpret all five data inputs as signed 32-bit integers.
2. Form the left product  
   `left_product = in_Rp0 * in_r_left`.
3. Form the right product  
   `right_product = in_Rp1 * in_r_right`.
4. Form the sum  
   `full_sum = in_r_center + left_product + right_product`.
5. Truncate `full_sum` to 32 bits by taking its low 32 bits.
6. Interpret those 32 bits as a signed two's-complement value and drive `out_rc` with that bit pattern.

This is wraparound arithmetic modulo `2^32` at the output. There is no saturation, rounding, exception signaling, or special handling for overflow. Multiplication is exact mathematical signed multiplication before final truncation to 32 output bits.

The kernel has no dependence on prior transactions. No state from one accepted input affects the arithmetic result of any later accepted input.

## Reset Behavior

Reset is synchronous active-high.

Architectural state:
- No persistent architectural state.

Output port state after reset and before the first accepted input:
- `out_valid = 0`
- `out_rc = 0`

Input-side readiness after reset:
- `in_ready = 1`

Because the kernel is stateless and 1:1, reset does not alter any stored coefficients or samples.
