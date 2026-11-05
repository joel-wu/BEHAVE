# andgate

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input a,
  input b,
  output out
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b): ...
```

Each call returns exactly one dictionary `{"out": value}`. Arguments and
`value` are unsigned one-bit integers, 0 or 1. A zero result still returns the
one-key dictionary, not `None`. There is no cross-call state. `reset()` has no
effect, returns `None`, and emits no output record.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All declared input bit patterns are valid. Outputs are observed after
combinational settling, including repeated identical inputs; there is no clock
or handshake.

`out=a & b`: the output is 1 exactly when both inputs are 1.

## Reset Behavior

There is no RTL reset or persistent state.
