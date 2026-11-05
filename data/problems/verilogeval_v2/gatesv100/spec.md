# gatesv100

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input  [99:0] value,
  output [99:0] out_both,
  output [99:0] out_any,
  output [99:0] out_different
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, value): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_any`, `out_both`, `out_different`: 100.

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

For i=0 through 98, `out_both[i]=value[i] & value[i+1]`; `out_both[99]=0`. For i=1 through 99, `out_any[i]=value[i] | value[i-1]`; `out_any[0]=0`. For i=0 through 99, `out_different[i]=value[i] ^ value[(i+1) mod 100]`.

## Reset Behavior

There is no RTL reset or persistent state.
