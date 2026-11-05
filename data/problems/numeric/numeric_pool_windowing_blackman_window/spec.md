# Blackman-Harris 92 dB Window Coefficient

Generate the coefficients of a periodic 16-sample cosine-sum window, represented
as IEEE-754 binary32 bit patterns. The length is fixed; no input samples are
multiplied by the window. Implementations may use a constant table or equivalent
logic producing the specified bits.

## Interface

### RTL interface

```systemverilog
module blackman_harris92db_window (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [3:0]  in_index,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_value
);
```

Transfers occur on rising clock edges with valid and ready both high, outside
reset. Valid and payload remain stable while waiting for ready. Output order
is preserved. An input may be accepted on the same edge as the last pending
output; no particular pipeline latency is required.

### Behavior interface

```python
class BlackmanHarris92dBWindow:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_index): ...
```

The call represents an accepted input and returns the corresponding output
dictionary.

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

- `blackman_harris92db_window`: fixed-window generator and ordered ready/valid transport.

## Functional Behavior

Every accepted unsigned index `in_index=0..15` emits one transaction whose `out_value` is the coefficient at that index.

For index `i`, set `theta = 2*pi*i/16` and evaluate

`w[i] = (0.35875)*cos(0*theta) + (-0.48829)*cos(1*theta) + (0.14128)*cos(2*theta) + (-0.01168)*cos(3*theta)`.

The decimal coefficients and pi denote real numbers. Round the final sum once
to binary32, round-to-nearest with ties to even; exact zero is positive zero.
There is no intermediate binary32 rounding. The denominator is 16, not 15:
this is a periodic window, so index zero is not repeated at index 15.
The following exact table fixes the output bits independently of a host math
library. Bits represent floating-point values, not signed integers; negative
coefficients, where present, are retained without clipping or normalization.

| Index | FP32 bits | Index | FP32 bits |
|---:|:---|---:|:---|
| 0 | `0x387BA882` | 1 | `0x3B487C4D` |
| 2 | `0x3CB20F5B` | 3 | `0x3DA988C2` |
| 4 | `0x3E5EB075` | 5 | `0x3EDEADC6` |
| 6 | `0x3F321D9A` | 7 | `0x3F6A0BB1` |
| 8 | `0x3F800000` | 9 | `0x3F6A0BB1` |
| 10 | `0x3F321D9A` | 11 | `0x3EDEADC6` |
| 12 | `0x3E5EB075` | 13 | `0x3DA988C2` |
| 14 | `0x3CB20F5B` | 15 | `0x3B487C4D` |

All input bit patterns are defined. Coefficients do not depend on transaction
history. Reset affects pending transport, not the constant window.

## Reset Behavior

Reset is active high and synchronous. It cancels pending outputs; after reset,
`in_ready=1`, `out_valid=0`, and `out_value=0`. The next accepted input starts a
fresh response. No pre-reset output may reappear.
