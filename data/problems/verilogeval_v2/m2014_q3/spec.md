# m2014_q3

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input [3:0] x,
  output logic f
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, x): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `f`: 1.

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

The module should implement the function f shown in the Karnaugh map
below. Cells marked d are completed as specified below.

                x[0]x[1]
  x[2]x[3]   00  01  11  10
        00 | d | 0 | d | d |
        01 | 0 | d | 1 | 0 |
        11 | 1 | 1 | d | d |
        10 | 1 | 1 | 0 | d |

Fix every cell marked `d` to zero. The map uses zero-based indices `x[0]` through `x[3]`. Thus the only inputs with `f=1` are 4,6,11,12,14.

## Reset Behavior

There is no RTL reset or persistent state.
