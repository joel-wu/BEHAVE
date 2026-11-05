# Q31 Square Root

Compute a Q31 square-root approximation using a normalized operand, a
reciprocal-square-root seed and three Newton refinements. Negative operands
return zero with an error flag; zero returns zero successfully.

## Interface

### RTL interface

```systemverilog
module q31_sqrt (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_value,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_root,
    output logic        out_status
);
```

All ports carry unsigned bit patterns; signed numerical interpretation is
specified below. Transfers occur on rising clock edges with valid and ready
both high, outside reset. Valid and payload must remain stable while stalled.
Every accepted input produces one ordered output. An output may be consumed
on the same edge as the next input is accepted; no fixed latency is required.

### Behavior interface

```python
class Q31Sqrt:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_value): ...
```

Each call represents an accepted input and returns a dictionary with keys
`out_root`, `out_status` containing output bit patterns.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_root`: 32.
- `out_status`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `q31_sqrt`: scalar arithmetic and ordered ready/valid transport.

## Functional Behavior

Interpret `in_value` as a signed 32-bit Q31 value x/2^31. If x < 0,
return `out_root=0, out_status=1`. If x=0, return both fields zero.

For positive x, choose the largest even e >= 0 for which n=x*2^e < 2^31.
Then 2^29 <= n < 2^31. Set v=Q[floor(n/2^26)-8], using the 24 seeds below.
Apply the following three times, in order:

`t = floor(v*v/2^28)`

`t = floor(n*t/2^31)`

`v = floor(v*(3*2^28 - t)/2^29)`.

Return `out_root = floor(floor(n*v/2^28)/2^(e/2))` and `out_status=0`.
Products are evaluated at full precision before each stated division; all
reachable intermediates are nonnegative and fit signed 32 bits after division.
The result is a Q31 approximation with these exact truncations, not an
integer square root or a correctly rounded floating-point square root.

| Index | Signed integer | Index | Signed integer |
|---:|---:|---:|---:|
| 0 | 536870912 | 1 | 506166750 |
| 2 | 480191942 | 3 | 457845052 |
| 4 | 438353264 | 5 | 421156193 |
| 6 | 405836263 | 7 | 392075079 |
| 8 | 379625062 | 9 | 368290407 |
| 10 | 357913941 | 11 | 348367849 |
| 12 | 339546978 | 13 | 331363921 |
| 14 | 323745341 | 15 | 316629190 |
| 16 | 309962566 | 17 | 303700050 |
| 18 | 297802400 | 19 | 292235509 |
| 20 | 286969573 | 21 | 281978417 |
| 22 | 277238947 | 23 | 272730696 |


## Reset Behavior

Reset `rst` is active high and synchronous.
It cancels pending responses and clears all output payloads to zero, with
`out_valid=0` and `in_ready=1`. No pre-reset output may reappear. There is no
persistent algorithmic state; subsequent results do not depend on history.
