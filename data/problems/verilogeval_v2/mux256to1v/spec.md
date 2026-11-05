# mux256to1v

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input [1023:0] value,
  input [7:0] sel,
  output [3:0] out
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, value, sel): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out`: 4.

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

- `TopModule`: the complete combinational circuit.

## Functional Behavior

The module should implement a 4-bit wide, 256-to-1 multiplexer. The 256
4-bit inputs are all packed into a single 1024-bit input vector. sel=0
should select bits value[3:0], sel=1 selects bits value[7:4], sel=2 selects
bits value[11:8], etc.

## Reset Behavior

There is no RTL reset or persistent state.
