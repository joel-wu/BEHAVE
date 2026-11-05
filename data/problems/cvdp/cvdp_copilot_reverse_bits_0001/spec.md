# reverse_bits

Reverse the order of all thirty-two bits in an input word without an additional byte-order transformation.

## Interface

### RTL interface

```systemverilog
module reverse_bits (
    input logic [31:0] num_in,
    output logic [31:0] num_out
);
```

### Behavior interface

```python
class ReverseBits:
    def __init__(self): ...
    def reset(self): ...
    def process(self, num_in): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `num_out`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Combinational observations

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs. There is
no clock, handshake or retained state.

## Modules

- `reverse_bits`: the native combinational module shown above.

## Functional Behavior

For every i from 0 through 31, `num_out[i]=num_in[31-i]`.
All 32-bit patterns are valid, including zero. Applying this operation twice
returns the original word. There is no byte-order conversion in addition to
the complete bit reversal.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.
