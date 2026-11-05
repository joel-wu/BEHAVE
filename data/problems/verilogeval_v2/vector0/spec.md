# vector0

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input [2:0] vec,
  output [2:0] outv,
  output o2,
  output o1,
  output o0
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, vec): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `o0`, `o1`, `o2`: 1.
- `outv`: 3.

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

`outv=vec`, `o2=vec[2]`, `o1=vec[1]`, and `o0=vec[0]`.

## Reset Behavior

There is no RTL reset or persistent state.
