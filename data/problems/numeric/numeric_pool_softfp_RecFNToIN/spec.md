# Recoded Floating-Point to Integer Converter

Convert a 33-bit HardFloat recoded binary32 operand to a signed or unsigned
32-bit integer. Each transaction selects a rounding mode and destination
signedness. The result includes invalid, overflow and inexact flags.

## Interface

### RTL interface

```systemverilog
module recfn_to_int_i32 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [32:0] in_in,
    input  logic [2:0]  in_roundingMode,
    input  logic        in_signedOut,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_out,
    output logic [2:0]  out_intExceptionFlags
);
```

### Behavior interface

```python
class RecfnToIntI32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_in, in_roundingMode, in_signedOut): ...
```

Each call returns one dictionary with uint32 `out_out` and uint3
`out_intExceptionFlags`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_intExceptionFlags`: 3.
- `out_out`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `recfn_to_int_i32`: integer conversion with ordered ready/valid transport.

## Functional Behavior

The input has sign `s = in_in[32]`, unsigned recoded exponent
`E = in_in[31:23]`, and fraction `F = in_in[22:0]`.
Decode the top three exponent bits `C = E >> 6`:

- C = 0 denotes signed zero, independently of unused trailing bits.
- C = 6 denotes infinity.
- C = 7 denotes NaN.
- Otherwise the finite value is
  `v = (-1)^s * (2^23 + F) * 2^(E - 279)`.

For example, E = 256 and F = 0 represents magnitude 1, not a negative
exponent or an overflow. Recoded subnormals are already normalized; they do not
need a second leading-zero normalization. Every input bit pattern uses the
rules above, including noncanonical encodings.

Round a finite value to an integer r using:

| in_roundingMode | Rule |
| --- | --- |
| 000 | Nearest integer, ties to even |
| 001 | Toward zero |
| 010 | Toward negative infinity |
| 011 | Toward positive infinity |
| 100 | Nearest integer, ties away from zero |
| 110 | Toward zero, then make the magnitude odd if inexact |
| 101 or 111 | Toward zero (explicit reserved-code behavior) |

For round-to-odd, an exact integer is unchanged. Otherwise truncate the
magnitude, set its least-significant bit to one, and restore the sign.
In particular, an inexact negative value with magnitude below one rounds to -1.

The destination range is [-2^31, 2^31-1] when in_signedOut = 1 and
[0, 2^32-1] otherwise. If r is in range, out_out is its 32-bit representation.
If r is outside the range, return the nearest endpoint and assert overflow.
A negative finite value that rounds to zero fits an unsigned destination.

NaN and infinity assert invalid instead of overflow. NaN returns the positive
endpoint regardless of its sign; infinity returns the endpoint selected by
its sign:

| Input | Signed destination | Unsigned destination |
| --- | --- | --- |
| NaN or +infinity | 0x7FFFFFFF | 0xFFFFFFFF |
| -infinity | 0x80000000 | 0x00000000 |

`out_intExceptionFlags = (invalid << 2) | (overflow << 1) | inexact`.
Invalid and overflow suppress inexact. Otherwise inexact is one exactly when
the finite input differs from the returned integer. Zero, including negative
zero, returns integer zero with no flags.

Examples:

| Value | Mode | Destination | Result | Flags |
| --- | --- | --- | --- | --- |
| +1 | any | either | 1 | 0 |
| +1.5 | nearest-even | either | 2 | 1 |
| -1.5 | nearest-even | signed | 0xFFFFFFFE | 1 |
| +2.5 | nearest-even | either | 2 | 1 |
| +2.5 | nearest-away or odd | either | 3 | 1 |
| -0.25 | toward-zero | unsigned | 0 | 1 |
| -0.25 | toward-negative | unsigned | 0 | 2 |
| +2^31 | any | signed | 0x7FFFFFFF | 2 |
| -2^31 | any | signed | 0x80000000 | 0 |

Each input transfer produces exactly one output in order. Transfers occur on
rising edges when valid and ready are both high, outside reset. A stalled
response holds both its data and flags stable, with out_valid asserted.
There is no fixed latency requirement.

## Reset Behavior

Reset is synchronous active-high. At a rising edge with rst asserted, discard
pending transactions and clear out_valid, out_out and out_intExceptionFlags.
After reset the transport is empty and in_ready = 1. No transfer occurs during
reset. There is no persistent numerical state.
