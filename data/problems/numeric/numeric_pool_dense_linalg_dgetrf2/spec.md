# Q16.16 two-by-two pivoted LU

Factor one two-by-two matrix using partial pivoting and a fractional elimination multiplier. All four matrix inputs and all four numerical outputs are signed Q16.16 encodings. This is a quantized LAPACK-style LU kernel, not integer division with a usually-zero multiplier.

## Interface

### RTL interface

```systemverilog
module lu_factor2x2 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_a00,
    input  logic [31:0] in_a01,
    input  logic [31:0] in_a10,
    input  logic [31:0] in_a11,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_u00,
    output logic [31:0] out_u01,
    output logic [31:0] out_l10,
    output logic [31:0] out_u11,
    output logic [0:0]  out_pivot_row0,
    output logic [1:0]  out_info
);
```

### Behavior interface

```python
class LuFactor2x2:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a00, in_a01, in_a10, in_a11): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_info`: 2.
- `out_l10`, `out_u00`, `out_u01`, `out_u11`: 32.
- `out_pivot_row0`: 1.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `lu_factor2x2`: the complete task kernel.

## Functional Behavior

Factor one two-by-two matrix using partial pivoting and a fractional elimination multiplier. All four matrix inputs and all four numerical outputs are signed Q16.16 encodings. This is a quantized LAPACK-style LU kernel, not integer division with a usually-zero multiplier.

Choose the first row when abs(a00)>=abs(a10), otherwise swap the two complete rows; abs(-2^31)=2^31 for this comparison. Output out_pivot_row0 is 0 or 1 respectively. Denote the pivoted raw integer matrix [[p00,p01],[p10,p11]].

If p00=0, return (p00,p01,p10,p11), out_info=1. Otherwise compute the widened values

```text
l10 = trunc_toward_zero((p10*2^16)/p00)
u11 = p11-floor((l10*p01)/2^16)
```

Return (out_u00,out_u01,out_l10,out_u11)=(p00,p01,l10,u11), each modulo 2^32. out_info=2 if the widened u11 is zero, otherwise 0. These are zero-pivot reports for the quantized recurrence, not a universal singularity test on the unquantized real matrix. L has implicit unit diagonal (encoded value 0x00010000), U is upper triangular; non-overflowing exactly representable cases satisfy P*A=L*U. One input produces one output.

## Reset Behavior

Reset is active-high. It cancels pending work, clears output-valid and resets the output registers to zero. No pre-reset transaction may reappear. Inputs transfer only when in_valid and in_ready are both high; outputs transfer only when out_valid and out_ready are both high. A pending output remains valid with unchanged payload until accepted. The task does not prescribe internal latency or microarchitecture.
