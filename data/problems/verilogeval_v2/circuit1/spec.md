# circuit1

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input a,
  input b,
  output q
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b): ...
```

Each call returns exactly `{"q": value}`, with both arguments and `value`
integers 0 or 1. The return is never `None` or a list. The model has no state;
`reset()` has no effect, returns `None`, and emits no record.

### Combinational observations

All unsigned bit patterns of the declared input widths are valid. Outputs are
observed after combinational settling, including repeated identical inputs;
there is no clock or handshake.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

The module should implement a combinational circuit. Read the simulation
waveforms to determine what the circuit does, then implement it.

  time  a  b  q
  0ns   0  0  0
  5ns   0  0  0
  10ns  0  0  0
  15ns  0  0  0
  20ns  0  0  0
  25ns  0  1  0
  30ns  0  1  0
  35ns  1  0  0
  40ns  1  0  0
  45ns  1  1  1
  50ns  1  1  1
  55ns  0  0  0
  60ns  0  0  0
  65ns  0  1  0
  70ns  0  1  0
  75ns  1  0  0
  80ns  1  0  0
  85ns  1  1  1
  90ns  1  1  1

## Reset Behavior

There is no RTL reset or persistent state.
