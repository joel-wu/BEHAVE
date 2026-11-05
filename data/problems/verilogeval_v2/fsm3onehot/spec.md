# fsm3onehot

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input value,
  input [3:0] state,
  output reg [3:0] next_state,
  output out
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, value, state): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `next_state`: 4.
- `out`: 1.

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

The module should implement the state transition table for a Moore state
machine with one input, one output, and four states. Use the following
one-hot state encoding: A=4'b0001, B=4'b0010, C=4'b0100, D=4'b1000.
Derive state transition and output logic equations by inspection assuming
a one-hot encoding. Implement only the state transition logic and output
logic (the combinational logic portion) for this state machine.

  State | Next state value=0, Next state value=1 | Output
  A     | A, B                             | 0
  B     | C, B                             | 0
  C     | A, D                             | 0
  D     | C, B                             | 1

For zero-hot or multi-hot inputs, OR the contributions of every active state, without priority selection. The block is combinational; the current-state input is not an internal register.

## Reset Behavior

There is no RTL reset or persistent state.
