# Hamming Window Stream

Generate the coefficients of a periodic 16-sample cosine-sum window, represented
as IEEE-754 binary32 bit patterns. The length is fixed; no input samples are
multiplied by the window. Implementations may use a constant table or equivalent
logic producing the specified bits.

## Interface

### RTL interface

```systemverilog
module hamming_window_f32_generator (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_start,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [3:0]  out_index,
    output logic [31:0] out_coeff
);
```

Transfers occur on rising clock edges with valid and ready both high, outside
reset. Valid and payload remain stable while waiting for ready. Output order
is preserved. An input may be accepted on the same edge as the last pending
output; no particular pipeline latency is required.

### Behavior interface

```python
class HammingWindowF32Generator:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_start): ...
```

The call represents an accepted input and returns the corresponding output
dictionary list.

Return a list of exactly sixteen dictionaries on every call, with out_index=0 through
15 in that order. Both values of in_start trigger this result. Never return None or a
bare dictionary.
Every returned dictionary contains exactly these fields (bit widths):

- `out_coeff`: 32.
- `out_index`: 4.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `hamming_window_f32_generator`: fixed-window generator and ordered ready/valid transport.

## Functional Behavior

Every accepted input emits 16 output transactions in order, with `out_index=0..15` and the corresponding `out_coeff`. Both values of `in_start` trigger a window. Output windows may not interleave.

For index `i`, set `theta = 2*pi*i/16` and evaluate

`w[i] = (0.54)*cos(0*theta) + (-0.46)*cos(1*theta)`.

The decimal coefficients and pi denote real numbers. Round the final sum once
to binary32, round-to-nearest with ties to even; exact zero is positive zero.
There is no intermediate binary32 rounding. The denominator is 16, not 15:
this is a periodic window, so index zero is not repeated at index 15.
The following exact table fixes the output bits independently of a host math
library. Bits represent floating-point values, not signed integers; negative
coefficients, where present, are retained without clipping or normalization.

| Index | FP32 bits | Index | FP32 bits |
|---:|:---|---:|:---|
| 0 | `0x3DA3D70A` | 1 | `0x3DEB8D34` |
| 2 | `0x3E5BE269` | 3 | `0x3EBA59B4` |
| 4 | `0x3F0A3D71` | 5 | `0x3F374E07` |
| 6 | `0x3F5D8247` | 7 | `0x3F77093B` |
| 8 | `0x3F800000` | 9 | `0x3F77093B` |
| 10 | `0x3F5D8247` | 11 | `0x3F374E07` |
| 12 | `0x3F0A3D71` | 13 | `0x3EBA59B4` |
| 14 | `0x3E5BE269` | 15 | `0x3DEB8D34` |

All input bit patterns are defined. Coefficients do not depend on transaction
history. Reset affects pending transport, not the constant window.

## Reset Behavior

Reset is active high and synchronous or asynchronous. It cancels pending outputs; after reset,
`in_ready=1`, `out_valid=0`, and `out_index=0`, `out_coeff=0`. The next accepted input starts a
fresh response. No pre-reset output may reappear.
