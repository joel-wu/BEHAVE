# gates4

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input [3:0] value,
  output out_and,
  output out_or,
  output out_xor
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

- `out_and`, `out_or`, `out_xor`: 1.

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

out_and is 1 exactly when all four bits are 1; out_or is 1 when any bit is 1; out_xor is 1 when an odd number of bits are 1.

## Reset Behavior

There is no RTL reset or persistent state.
