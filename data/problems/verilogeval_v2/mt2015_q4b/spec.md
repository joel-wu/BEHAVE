# mt2015_q4b

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

The module can be described by the following simulation waveform:

  time  x  y  z
  0ns   0  0  1
  5ns   0  0  1
  10ns  0  0  1
  15ns  0  0  1
  20ns  0  0  1
  25ns  1  0  0
  30ns  1  0  0
  35ns  0  1  0
  40ns  0  1  0
  45ns  1  1  1
  50ns  1  1  1
  55ns  0  0  1
  60ns  0  1  0
  65ns  0  1  0
  70ns  1  1  1
  75ns  0  1  0
  80ns  0  1  0
  85ns  0  1  0
  90ns  1  0  0

## Reset Behavior

There is no RTL reset or persistent state.
