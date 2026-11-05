# RecFNToFN

Convert a 33-bit HardFloat recoded binary32 operand into an IEEE-754 binary32
bit pattern. Exponent width is fixed to 8 and significand width to 24.
Valid recoded values convert exactly, without rounding, exception reporting
or NaN quieting.

## Interface

### RTL interface

```systemverilog
module recfn_to_fn (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [32:0] in_recfn,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_fn
);
```

### Behavior interface

```python
class RecfnToFn:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_recfn): ...
```

Each call returns one dictionary, `{"out_fn": uint32}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_fn`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `recfn_to_fn`: stateless conversion with ordered ready/valid transport.

## Functional Behavior

Let `s = in_recfn[32]`, `E = in_recfn[31:23]`, and
`F = in_recfn[22:0]`. E is unsigned, from 0 through 511.
The top three bits `C = E >> 6` classify zero (C = 0),
infinity (C = 6), and NaN (C = 7).

The raw significand is `S = F` for the zero class and
`S = (1 << 23) | F` otherwise. Zero does not acquire a hidden leading one.

Compute the output exponent e and fraction f:

- If `E < 130`, set `d = (1 - (E & 31)) & 31`,
  `e = 0`, and `f = (S >> 1) >> d`.
- Otherwise set `e = (E - 129) & 255` and `f = F`.
- For infinity or NaN, set `e = 255`. For infinity also set `f = 0`.

Return `(s << 31) | (e << 23) | f`. Right shifts are logical.
Valid recoded subnormals have 107 <= E <= 129; normal finite values have
130 <= E <= 383. Infinity ignores its fraction; NaN preserves its payload,
including the quiet/signaling bit. Sign is always preserved.

All 33-bit patterns are accepted and use these field equations. Invalid recoded
patterns receive this mechanical bit-pattern result; no floating-point meaning
or correction is assigned to them.

| in_recfn | out_fn |
| --- | --- |
| 0x000000000 | 0x00000000 (+0) |
| 0x100000000 | 0x80000000 (-0) |
| 0x035800000 | 0x00000001 |
| 0x041000000 | 0x00800000 |
| 0x080000000 | 0x3F800000 (+1) |
| 0x0C0000000 | 0x7F800000 (+infinity) |
| 0x0E0412345 | 0x7FC12345 (quiet NaN) |

Each input transfer produces exactly one output, in order. A transfer occurs
on a rising edge when valid and ready are both high, outside reset. A stalled
output keeps valid and data unchanged. There is no fixed latency requirement.

## Reset Behavior

Reset is active-high and may be synchronous or asynchronous. On a rising edge
with reset asserted, discard pending transactions, clear out_valid, and set
out_fn = 0. An asynchronous implementation may clear them earlier. After reset
the empty transport has in_ready = 1. No transfer occurs during reset.
There is no persistent numerical state.
