# fsm_onehot

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input value,
  input [9:0] state,
  output [9:0] next_state,
  output out1,
  output out2
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

- `next_state`: 10.
- `out1`, `out2`: 1.

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

Given the follow state machine with 1 input and 2 outputs (the outputs
are given as "(out1, out2)"):

  S0 (0, 0) --0--> S0
  S0 (0, 0) --1--> S1
  S1 (0, 0) --0--> S0
  S1 (0, 0) --1--> S2
  S2 (0, 0) --0--> S0
  S2 (0, 0) --1--> S3
  S3 (0, 0) --0--> S0
  S3 (0, 0) --1--> S4
  S4 (0, 0) --0--> S0
  S4 (0, 0) --1--> S5
  S5 (0, 0) --0--> S8
  S5 (0, 0) --1--> S6
  S6 (0, 0) --0--> S9
  S6 (0, 0) --1--> S7
  S7 (0, 1) --0--> S0
  S7 (0, 1) --1--> S7
  S8 (1, 0) --0--> S0
  S8 (1, 0) --1--> S1
  S9 (1, 1) --0--> S0
  S9 (1, 1) --1--> S1

Suppose this state machine uses one-hot encoding, where state[0] through
state[9] correspond to the states S0 though S9, respectively. The outputs
are zero unless otherwise specified. The next_state[0] through next_state[9]
correspond to the transition to next states S0 though S9. For example, The
next_state[1] is set to 1 when the next state is S1 , otherwise, it is set to 0.

Here, the input state[9:0] can be a combinational of multiple states, and
the TopModule is expected to response.
For example:
When the state[9:0] = 10'b0000010100, state[4] == 1, and state[2] == 1, the
states includes S4, and S2 states.

The module should implement the state transition logic and output logic
portions of the state machine (but not the state flip-flops). You are
given the current state in state[9:0] and must implement next_state[9:0]
and the two outputs.

## Reset Behavior

There is no RTL reset or persistent state.
