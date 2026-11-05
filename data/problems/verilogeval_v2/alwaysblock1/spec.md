# alwaysblock1

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input a,
  input b,
  output out_assign,
  output reg out_alwaysblock
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b): ...
```

Each `process(a, b)` call returns one dictionary with exactly the keys
`out_assign` and `out_alwaysblock`. Both arguments and both results are unsigned
one-bit port patterns. Return both fields on every call, including repeated
identical inputs; do not return `None` or a list. The instance is stateless.
`reset()` has no effect and returns `None` without an output record.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All declared input bit patterns are valid. Outputs are observed after
combinational settling, including repeated identical inputs; there is no clock
or handshake.

Both outputs equal `a & b`. Implement `out_assign` with a continuous assignment and `out_alwaysblock` with a combinational always block.

## Reset Behavior

There is no RTL reset or persistent state.
