# RecodedFloatResize

Convert recFN(8,24), the recoded binary32 format, to recFN(5,11), the recoded binary16 format. The conversion retains all six rounding modes, gradual underflow, signed zero, infinities, quiet/signaling NaNs and exception flags. The widths are fixed; there are no configuration parameters.

## Interface

### RTL interface

```systemverilog
module recoded_float_resize (
    input logic clk, input logic rst,
    input logic in_valid, output logic in_ready,
    input logic [32:0] in_in,
    input logic [2:0] in_roundingMode,
    input logic in_detectTininess,
    output logic out_valid, input logic out_ready,
    output logic [16:0] out_out,
    output logic [4:0] out_exceptionFlags
);
```

### Behavior interface

```python
class RecodedFloatResize:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_in, in_roundingMode, in_detectTininess): ...
```

Each call returns one mapping with unsigned fields `out_out` and `out_exceptionFlags`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_exceptionFlags`: 5.
- `out_out`: 17.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

Each input transfer, sampled on a rising edge with `in_valid && in_ready`, produces one ordered output transfer. While `out_valid && !out_ready`, valid and both output fields remain unchanged. No fixed arithmetic latency is required.

## Modules

- `recoded_float_resize`: conversion and output holding.
- `RecodedFloatResize`: untimed conversion.

## Functional Behavior

The input fields are sign `s=in_in[32]`, unsigned exponent `E=in_in[31:23]`, and fraction `F=in_in[22:0]`. Its class is `E>>6`: 0 denotes zero, 6 infinity, 7 NaN; other classes are finite and nonzero with value
`(-1)^s * (2^23+F) * 2^(E-279)`.
These rules also define the interpretation of otherwise noncanonical input bit patterns. For NaNs, fraction bit 22 distinguishes quiet (1) from signaling (0).

The output fields are sign bit 16, **six-bit** exponent `out_out[15:10]`, and ten-bit fraction `out_out[9:0]`. A finite nonzero output represents
`(-1)^s * (2^10+fraction) * 2^(exponent-42)`.
Normal exponents are 18..47. Subnormal exponents are 8..17; their fraction has the trailing zeros implied by the binary16 subnormal spacing. Zero has exponent and fraction zero. Infinity has exponent 48 and fraction zero. Every NaN result is the positive quiet NaN `0x0E200`; payload is not propagated.

Rounding modes are 0 nearest/ties-to-even, 1 toward zero, 2 toward negative infinity, 3 toward positive infinity, 4 nearest/ties-away-from-zero, and 6 round-to-odd (truncate, then set the retained least-significant bit if inexact). Reserved codes 5 and 7 use toward-zero. Round once to binary16 precision and spacing: eleven significant bits for normal values, spacing `2^-24` for subnormal values. In particular, do not round to eleven bits and then round again to subnormal spacing.

Flag bits 4..0 are invalid, infinite, overflow, underflow, inexact:

- Signaling NaN raises invalid; quiet NaN does not. Infinity preserves its sign. Zero preserves its sign. None of these cases raises the other flags; infinite is always zero.
- Overflow is tested on the precision-rounded result with an unbounded exponent range. An exponent above 15 raises overflow and inexact. Nearest modes return signed infinity; directed modes return infinity only when directed away from zero. All remaining cases, including round-to-odd, return signed maximum finite (`0x0BFFF` or `0x1BFFF`).
- Otherwise, inexact means the rounded numerical value differs from the input.
- Underflow requires inexact and tininess. With `in_detectTininess=0`, test whether the exact nonzero magnitude is below `2^-14`. With 1, test the result rounded to eleven significant bits **with an unbounded exponent range**, before imposing subnormal spacing. Thus a final minimum-normal output does not by itself imply that underflow is clear. An inexact result rounded to zero is tiny.

## Reset Behavior

Reset is synchronous active-high and takes priority over transfers. A reset edge discards pending output and clears `out_valid`, `out_out`, and `out_exceptionFlags`; input is ready after that edge. There is no arithmetic state between transactions.
