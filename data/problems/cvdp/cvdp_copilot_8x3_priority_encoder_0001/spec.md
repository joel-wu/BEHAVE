# priority_encoder_8x3

Return the index of the highest asserted bit in an eight-bit input, with zero defined as the result for an all-zero input.

## Interface

### RTL interface

```systemverilog
module priority_encoder_8x3 (
    input logic [7:0] value,
    output logic [2:0] out
);
```

### Behavior interface

```python
class PriorityEncoder8x3:
    def __init__(self): ...
    def reset(self): ...
    def process(self, value): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out`: 3.

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

- `priority_encoder_8x3`: the native combinational module shown above.

## Functional Behavior

`out` is the index of the highest asserted bit in `value` (bit 7 has highest
priority). If `value=0`, output 0. All 256 input vectors are valid. The source port `in` is renamed `value` in the adapted interface. This spelling change does not change any of
the 256 input/output pairs.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.
