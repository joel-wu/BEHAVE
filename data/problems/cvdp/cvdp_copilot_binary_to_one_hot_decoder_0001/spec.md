# binary_to_one_hot_decoder

Decode a binary index into a configurable-width one-hot word, returning zero when the index is outside the output width.

## Interface

### RTL interface

```systemverilog
module binary_to_one_hot_decoder #(
    parameter int BINARY_WIDTH = 5, parameter int OUTPUT_WIDTH = 32
) (
    input logic [BINARY_WIDTH-1:0] binary_in,
    output logic [OUTPUT_WIDTH-1:0] one_hot_out
);
```

### Behavior interface

```python
class BinaryToOneHotDecoder:
    def __init__(self, BINARY_WIDTH=5, OUTPUT_WIDTH=32): ...
    def reset(self): ...
    def process(self, binary_in): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `one_hot_out`: OUTPUT_WIDTH.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Combinational observations

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs. There is
no clock, handshake or retained state.

## Modules

- `binary_to_one_hot_decoder`: the native combinational module shown above.

## Functional Behavior

`BINARY_WIDTH` and `OUTPUT_WIDTH` are independently positive integers.
If `binary_in < OUTPUT_WIDTH`, only output bit `binary_in` is 1. Otherwise
the complete output is zero. Bit 0 is least significant. Output width need
not equal or exceed `2**BINARY_WIDTH`; unreachable high bits remain zero.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.
