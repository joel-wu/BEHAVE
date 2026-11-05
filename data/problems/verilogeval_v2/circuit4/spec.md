# circuit4

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input a,
  input b,
  input c,
  input d,
  output q
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a, b, c, d): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `q`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
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

The module should implement a combinational circuit. Read the simulation
waveforms to determine what the circuit does, then implement it.

  time  a  b  c  d  q
  0ns   0  0  0  0  0
  5ns   0  0  0  0  0
  10ns  0  0  0  0  0
  15ns  0  0  0  0  0
  20ns  0  0  0  1  0
  25ns  0  0  1  0  1
  30ns  0  0  1  1  1
  35ns  0  1  0  0  1
  40ns  0  1  0  1  1
  45ns  0  1  1  0  1
  50ns  0  1  1  1  1
  55ns  1  0  0  0  0
  60ns  1  0  0  1  0
  65ns  1  0  1  0  1
  70ns  1  0  1  1  1
  75ns  1  1  0  0  1
  80ns  1  1  0  1  1
  85ns  1  1  1  0  1
  90ns  1  1  1  1  1

## Reset Behavior

There is no RTL reset or persistent state.
