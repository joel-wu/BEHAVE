# bcd_to_excess_3

Convert a valid BCD digit to excess-3 and flag invalid four-bit digit encodings.

## Interface

### RTL interface

```systemverilog
module bcd_to_excess_3 (
    input logic [3:0] bcd,
    output logic [3:0] excess3,
    output logic error
);
```

### Behavior interface

```python
class BcdToExcess3:
    def __init__(self): ...
    def reset(self): ...
    def process(self, bcd): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `error`: 1.
- `excess3`: 4.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Combinational observations

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs. There is
no clock, handshake or retained state.

## Modules

- `bcd_to_excess_3`: the native combinational module shown above.

## Functional Behavior

For unsigned `bcd` from 0 through 9, `excess3=bcd+3` and `error=0`.
For 10 through 15, `excess3=0` and `error=1`. All sixteen input bit patterns
are supported; invalid BCD digits are ordinary defined inputs.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.
