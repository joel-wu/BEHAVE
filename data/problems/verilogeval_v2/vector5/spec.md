# vector5

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input a,
  input b,
  input c,
  input d,
  input e,
  output [24:0] out
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b, c, d, e): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out`: 25.

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

Let `v=(a,b,c,d,e)`. For row r and column c in 0 through 4,
`out[24-5*r-c]` is 1 exactly when `v[r]=v[c]`. The diagonal bits are
always 1. All 25 ordered comparisons, including both halves of the matrix,
are present.

## Reset Behavior

There is no RTL reset or persistent state.
