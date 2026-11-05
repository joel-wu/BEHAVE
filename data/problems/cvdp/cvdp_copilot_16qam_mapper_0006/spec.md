# qam16_demapper_interpolated

Demap packed 16-QAM I/Q samples and check the interpolated midpoints against a configurable error threshold.

## Interface

### RTL interface

```systemverilog
module qam16_demapper_interpolated #(
    parameter int N = 4, parameter int IN_WIDTH = 3,
    parameter int OUT_WIDTH = 4, parameter int ERROR_THRESHOLD = 1
) (
    input logic [(N+N/2)*IN_WIDTH-1:0] I, Q,
    output logic [N*OUT_WIDTH-1:0] bits,
    output logic error_flag
);
```

### Behavior interface

```python
class Qam16Demapper:
    def __init__(self, N=4, IN_WIDTH=3, OUT_WIDTH=4, ERROR_THRESHOLD=1): ...
    def reset(self): ...
    def process(self, I, Q): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `bits`: N*OUT_WIDTH.
- `error_flag`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Combinational observations

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs. There is
no clock, handshake or retained state.

## Modules

- `qam16_demapper_interpolated`: the native combinational module shown above.

## Functional Behavior

`N` is even and at least 2; `IN_WIDTH=3` and `OUT_WIDTH=4` are fixed.
`ERROR_THRESHOLD` is a nonnegative integer, default 1. Read signed 3-bit I/Q lanes
most significant first, in groups (first mapped sample, interpolation, second
mapped sample). Demap the first and third lanes using `-3:00, -1:01, 1:10, 3:11`.
Each nibble is `{I_code,Q_code}`; pack the N nibbles most significant first.

For each axis and group, compute the widened signed midpoint
`floor((first+second)/2)`. Set `error_flag` if any interpolation differs from
that midpoint by **strictly more than** `ERROR_THRESHOLD`. Equal deviation is
accepted. The flag concerns interpolation only: other mapped amplitudes map to
`00`; they do not independently assert it. All input bit patterns have defined
results, including -4. This completes the source's unspecified invalid-amplitude
case; on legal constellation levels the midpoint is integral.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.
