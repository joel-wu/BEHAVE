# 2012_q1g

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

Each call returns one dictionary `{"f": value}`, where `value` is the Python
integer 0 or 1. `x` is a Python integer in 0..15 carrying the four input bits.
All such inputs are valid. Outputs are observed after combinational settling,
including repeated identical inputs; there is no clock or handshake.
`reset()` has no effect and returns `None`.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

The module should implement the function f shown in the Karnaugh map
below.

             x[0]x[1]
x[2]x[3]  00  01  11  10
  00     | 1 | 0 | 0 | 1 |
  01     | 0 | 0 | 0 | 0 |
  11     | 1 | 1 | 1 | 0 |
  10     | 1 | 1 | 0 | 1 |

## Reset Behavior

There is no RTL reset or persistent state.
