# always_if2

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input cpu_overheated,
  output reg shut_off_computer,
  input arrived,
  input gas_tank_empty,
  output reg keep_driving
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, cpu_overheated, arrived, gas_tank_empty): ...
```

Each `process(...)` call evaluates the supplied input combination and returns
exactly `{"shut_off_computer": shut_off, "keep_driving": drive}`. Arguments and
both returned values are unsigned one-bit integers. Every call returns both
fields, including for repeated inputs or zero outputs; it never returns `None`
or a list. Construction and `reset()` leave no persistent state. `reset()`
returns `None` and produces no record.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

All declared input bit patterns are valid. Outputs are observed after
combinational settling, including repeated identical inputs; there is no clock
or handshake.

`shut_off_computer=cpu_overheated`. `keep_driving` is 1 exactly when `arrived=0` and `gas_tank_empty=0`. Assign both outputs on every combinational path; no value is retained from an earlier input.

## Reset Behavior

There is no RTL reset or persistent state.
