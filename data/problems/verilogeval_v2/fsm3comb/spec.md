# fsm3comb

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input value,
  input [1:0] state,
  output reg [1:0] next_state,
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

- `next_state`: 2.
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

The module should implement the following is the state transition table
for a Moore state machine with one input, one output, and four states.
Use the following state encoding: A=2'b00, B=2'b01, C=2'b10,
D=2'b11.Implement only the state transition logic and output logic (the
combinational logic portion) for this state machine. Given the current
state (state), compute the next_state and output (out) based on the state
transition table.

  State | Next state value=0, Next state value=1 | Output
  A     | A, B                             | 0
  B     | C, B                             | 0
  C     | A, D                             | 0
  D     | C, B                             | 1

## Reset Behavior

There is no RTL reset or persistent state.
