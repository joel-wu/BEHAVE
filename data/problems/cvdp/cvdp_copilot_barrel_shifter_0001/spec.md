# barrel_shifter_8bit

Shift an eight-bit word left or right by a selected amount, filling vacated positions with zero.

## Interface

### RTL interface

```systemverilog
module barrel_shifter_8bit (
    input logic [7:0] data_in,
    input logic [2:0] shift_bits,
    input logic left_right,
    output logic [7:0] data_out
);
```

### Behavior interface

```python
class BarrelShifter8bit:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_in, shift_bits, left_right): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: 8.

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

- `barrel_shifter_8bit`: the native combinational module shown above.

## Functional Behavior

For `left_right=1`, output `(data_in << shift_bits) & 255`;
otherwise output `data_in >> shift_bits`. Vacated bits are zero. Shift counts
are 0 through 7; this is neither a rotation nor an arithmetic right shift.
Despite the source's introductory “one clock cycle” phrase, its native ports,
explicit combinational requirement and tests define a clockless circuit.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.
