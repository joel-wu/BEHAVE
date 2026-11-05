# Nuttall3 Window Sample

Generate one periodic three-term Nuttall window coefficient for a length
from 1 through 16. The output is its IEEE-754 binary32 encoding, not an
integer amplitude. A ROM or equivalent arithmetic may implement the mapping.

## Interface

### RTL interface

```systemverilog
module nuttall3_window_sample (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [5:0]  in_block_size,
    input  logic [5:0]  in_index,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_value
);
```

All ports carry unsigned bit patterns; signed numerical interpretation is
specified below. Transfers occur on rising clock edges with valid and ready
both high, outside reset. Valid and payload must remain stable while stalled.
Every accepted input produces one ordered output. An output may be consumed
on the same edge as the next input is accepted; no fixed latency is required.

### Behavior interface

```python
class Nuttall3WindowSample:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_block_size, in_index): ...
```

Each call represents an accepted input and returns a dictionary with keys
`out_value` containing output bit patterns.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_value`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `nuttall3_window_sample`: scalar arithmetic and ordered ready/valid transport.

## Functional Behavior

Let N = `in_block_size` and i = `in_index`, both unsigned 6-bit values.
For `1 <= N <= 16` and `i < N`, set theta = 2*pi*i/N and compute

`w = 0.375 - 0.5*cos(theta) + 0.125*cos(2*theta)`.

Evaluate this expression over real numbers, then round once to binary32 using
round-to-nearest, ties-to-even; exact zero is positive zero. There is no
intermediate binary32 rounding. This is a periodic window (denominator N,
not N-1), equivalently sin(pi*i/N)^4. Coefficients lie in [0,1].
All other input pairs return positive zero. The exact output encodings are:

| N | FP32 words for indices 0 through N-1 |
|---:|:---|
| 1 | `00000000` |
| 2 | `00000000` `3F800000` |
| 3 | `00000000` `3F100000` `3F100000` |
| 4 | `00000000` `3E800000` `3F800000` `3E800000` |
| 5 | `00000000` `3DF47550` `3F517156` `3F517156` `3DF47550` |
| 6 | `00000000` `3D800000` `3F100000` `3F800000` `3F100000` `3D800000` |
| 7 | `00000000` `3D112984` `3EBF4DA8` `3F674694` `3F674694` `3EBF4DA8` `3D112984` |
| 8 | `00000000` `3CAFB0CD` `3E800000` `3F3A827A` `3F800000` `3F3A827A` `3E800000` `3CAFB0CD` |
| 9 | `00000000` `3C603205` `3E2ECFBD` `3F100000` `3F70CB49` `3F70CB49` `3F100000` `3E2ECFBD` `3C603205` |
| 10 | `00000000` `3C15664B` `3DF47550` `3EDB54CE` `3F517156` `3F800000` `3F517156` `3EDB54CE` `3DF47550` `3C15664B` |
| 11 | `00000000` `3BCE7128` `3DAEF87E` `3EA7066A` `3F2F44A2` `3F75BC37` `3F75BC37` `3F2F44A2` `3EA7066A` `3DAEF87E` `3BCE7128` |
| 12 | `00000000` `3B930A2F` `3D800000` `3E800000` `3F100000` `3F5ED9EC` `3F800000` `3F5ED9EC` `3F100000` `3E800000` `3D800000` `3B930A2F` |
| 13 | `00000000` `3B56F69F` `3D3F0BB3` `3E460132` `3EEADFC9` `3F43AAA3` `3F789D7A` `3F789D7A` `3F43AAA3` `3EEADFC9` `3E460132` `3D3F0BB3` `3B56F69F` |
| 14 | `00000000` `3B20AE3B` `3D112984` `3E1ABECA` `3EBF4DA8` `3F28AF9F` `3F674694` `3F800000` `3F674694` `3F28AF9F` `3EBF4DA8` `3E1ABECA` `3D112984` `3B20AE3B` |
| 15 | `00000000` `3AF4EBB6` `3CE03432` `3DF47550` `3E9C2868` `3F100000` `3F517156` `3F7A6FB4` `3F7A6FB4` `3F517156` `3F100000` `3E9C2868` `3DF47550` `3CE03432` `3AF4EBB6` |
| 16 | `00000000` `3ABDDE53` `3CAFB0CD` `3DC31CE2` `3E800000` `3EF4B64E` `3F3A827A` `3F6CE24E` `3F800000` `3F6CE24E` `3F3A827A` `3EF4B64E` `3E800000` `3DC31CE2` `3CAFB0CD` `3ABDDE53` |


## Reset Behavior

Reset `rst` is active high and asynchronous.
It cancels pending responses and clears all output payloads to zero, with
`out_valid=0` and `in_ready=1`. No pre-reset output may reappear. There is no
persistent algorithmic state; subsequent results do not depend on history.
