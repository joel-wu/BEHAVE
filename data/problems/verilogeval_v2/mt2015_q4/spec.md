# mt2015_q4

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input x,
  input y,
  output z
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, x, y): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `z`: 1.

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

Submodule A computes `(x ^ y) & x`. Submodule B has the truth table
`xy=00,01,10,11 -> z=1,0,0,1`.

The module is implemented with two A submodules and two B submodules. The
first input of all four submodules is connect to input 'x', and the
second input of all four submodules is connected to 'y'. The output of
the first A submodule is connected to a two-input OR, along with the
output of the first B submodule. The second pair of A and B submodules is
similarly connected to an AND gate. The output of the OR and the AND is
connected to an XOR, whose output is 'z'.

## Reset Behavior

There is no RTL reset or persistent state.
