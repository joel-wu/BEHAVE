# Q31 normalizing divider

Compute the signed ratio of two Q1.31 operands as a normalized Q1.31 quotient and a nonnegative exponent. This follows the CMSIS Q31 division kernel, including saturating absolute value.

## Interface

### RTL interface

```systemverilog
module q31_normalizing_divider (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_numerator,
    input  logic [31:0]  in_denominator,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_quotient,
    output logic [15:0]  out_shift,
    output logic         out_status_naninf
);
```

### Behavior interface

```python
class Q31NormalizingDivider:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_numerator, in_denominator): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_quotient`: 32.
- `out_shift`: 16.
- `out_status_naninf`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q31_normalizing_divider`: the complete task kernel.

## Functional Behavior

Compute the signed ratio of two Q1.31 operands as a normalized Q1.31 quotient and a nonnegative exponent. This follows the CMSIS Q31 division kernel, including saturating absolute value.

Interpret both inputs as signed 32-bit integers n,d. Let negative=(n<0) XOR (d<0). If d=0, return quotient=0x80000000 when negative and 0x7fffffff otherwise, shift=0 and status_naninf=1.

Otherwise let a=min(abs(n),2^31-1), b=min(abs(d),2^31-1), q=floor((a*2^31)/b). Choose the smallest nonnegative shift s for which (q>>s)<2^31. Return the signed value -(q>>s) when negative, +(q>>s) otherwise, encoded in 32 bits; out_shift=s and status_naninf=0. In particular, abs(-2^31) saturates to 2^31-1, not 2^31.

The exponent reconstructs the quotient scale; discarded low bits are not restored. There is one output per input, including divide-by-zero.

## Reset Behavior

Reset is active-high. It cancels pending work, clears output-valid and resets the output registers to zero. No pre-reset transaction may reappear. Inputs transfer only when in_valid and in_ready are both high; outputs transfer only when out_valid and out_ready are both high. A pending output remains valid with unchanged payload until accepted. The task does not prescribe internal latency or microarchitecture.
