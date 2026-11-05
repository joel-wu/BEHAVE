# review2015_fsmonehot

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input d,
  input done_counting,
  input ack,
  input [9:0] state, // 10-bit one-hot current state
  output B3_next,
  output S_next,
  output S1_next,
  output Count_next,
  output Wait_next,
  output done,
  output counting,
  output shift_ena
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, d, done_counting, ack, state): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `B3_next`, `Count_next`, `S1_next`, `S_next`, `Wait_next`, `counting`, `done`,
  `shift_ena`: 1.

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

The module should implement the following Moore state machine with 3
input (d, done_counting, ack) and 3 outputs (shift_ena, counting, done).
Unless otherwise stated in the diagram below, assume outputs are 0 and
inputs are don't cares.

state   (output)      --input--> next state
-------------------------------------------
  S     ()            --d=0--> S
  S     ()            --d=1--> S1
  S1    ()            --d=0--> S
  S1    ()            --d=1--> S11
  S11   ()            --d=0--> S110
  S11   ()            --d=1--> S11
  S110  ()            --d=0--> S
  S110  ()            --d=1--> B0
  B0    (shift_ena=1) --(always go to next cycle)--> B1
  B1    (shift_ena=1) --(always go to next cycle)--> B2
  B2    (shift_ena=1) --(always go to next cycle)--> B3
  B3    (shift_ena=1) --(always go to next cycle)--> Count
  Count (counting=1)  --done_counting=0--> Count
  Count (counting=1)  --done_counting=1--> Wait
  Wait  (done=1)      --ack=0--> Wait
  Wait  (done=1)      --ack=1--> S

At reset, the state machine starts in state "S". Derive next-state logic
equations and output logic equations by inspection assuming the following
one-hot encoding is used: (S, S1, S11, S110, B0, B1, B2, B3, Count, Wait)
= (10'b0000000001, 10'b0000000010, 10'b0000000100, ... , 10'b1000000000)

Derive state transition and output logic equations by inspection assuming
a one-hot encoding. Implement only the state transition logic and output
logic (the combinational logic portion) for this state machine.

Write code that generates the following signals:

 - B3_next -- Assert when next-state is B3 state
 - S_next -- Assert when next-state is S state
 - S1_next -- Assert when next-state is S1 state
 - Count_next -- Assert when next-state is Count state
 - Wait_next -- Assert when next-state is Wait state
 - done -- output logic
 - counting -- output logic
 - shift_ena -- output logic

For zero-hot or multi-hot inputs, OR the contributions of every active state, without priority selection. The block is combinational; the current-state input is not an internal register.

## Reset Behavior

There is no RTL reset or persistent state.
