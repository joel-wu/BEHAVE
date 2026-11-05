# RecFNFromFN32

Convert an IEEE-754 binary32 bit pattern into HardFloat's 33-bit recoded format,
with exponent width 8 and significand width 24. Finite values are preserved
exactly, including subnormals. Zero uses a canonical recoded payload of zero;
NaN sign and payload are preserved without quieting. There is no rounding or
exception reporting.

## Interface

### RTL interface

```systemverilog
module rec_fn_from_fn32 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_fn,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [32:0] out_recfn
);
```

### Behavior interface

```python
class RecFNFromFN32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_fn): ...
```

Each call returns one dictionary, `{"out_recfn": uint33}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_recfn`: 33.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `rec_fn_from_fn32`: stateless conversion with ordered ready/valid transport.

## Functional Behavior

All 32-bit input patterns are allowed. Let `s = in_fn[31]`,
`e = in_fn[30:23]`, and `f = in_fn[22:0]`. Pack the output as
`(s << 32) | (E << 23) | F`, with unsigned 9-bit exponent E and
23-bit fraction F:

| Input class | Condition | E | F |
| --- | --- | --- | --- |
| Zero | e = 0, f = 0 | 0 | 0 |
| Subnormal | e = 0, f != 0 | 129 - k | (f << (k + 1)) & 0x7FFFFF |
| Normal | 1 <= e <= 254 | e + 129 | f |
| Infinity | e = 255, f = 0 | 384 | 0 |
| NaN | e = 255, f != 0 | 448 | f |

For a subnormal, k is the number of leading zeros in the 23-bit fraction,
from 0 through 22. Normalization restores an implicit leading one at bit 23,
which is omitted from F. Subnormal recoded exponents range from 107 through
129; normal exponents range from 130 through 383. The top three exponent bits
classify zero (000), infinity (110), and NaN (111).

Examples, written as integer bit patterns:

| in_fn | out_recfn |
| --- | --- |
| 0x00000000 (+0) | 0x000000000 |
| 0x80000000 (-0) | 0x100000000 |
| 0x00000001 (smallest positive subnormal) | 0x035800000 |
| 0x00800000 (smallest positive normal) | 0x041000000 |
| 0x3F800000 (+1) | 0x080000000 |
| 0x7F800000 (+infinity) | 0x0C0000000 |
| 0x7FC12345 (quiet NaN) | 0x0E0412345 |

Each input transfer produces exactly one output, in order. A transfer occurs
on a rising edge when valid and ready are both high, outside reset. A stalled
output keeps its valid and data unchanged. Implementations may choose latency
and buffering without losing, duplicating or reordering transactions.

## Reset Behavior

Reset is synchronous active-high. On a rising edge with rst = 1, discard
pending transactions, clear out_valid, and set out_recfn = 0.
After reset the transport is empty and in_ready = 1. No transfer occurs during
reset. There is no persistent numerical state.
