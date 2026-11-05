# 2012_q2b

A stateless combinational circuit with the interface below.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input [5:0] y,
  input w,
  output Y1,
  output Y3
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, y, w): ...
```

Each call returns one dictionary of all output ports. Arguments and
results are unsigned bit patterns of the declared widths. All such inputs are
valid. Outputs are observed after combinational settling, including repeated
identical inputs; there is no clock or handshake.

The return shape is exactly `{"Y1": next_b, "Y3": next_d}`, where both values
are integers 0 or 1. Zero-valued outputs still produce both keys, not `None`.
`reset()` has no effect.

## Modules

- `TopModule`: the complete combinational circuit.

## Functional Behavior

Consider the following finite-state machine:

  A (0) --1--> B
  A (0) --0--> A
  B (0) --1--> C
  B (0) --0--> D
  C (0) --1--> E
  C (0) --0--> D
  D (0) --1--> F
  D (0) --0--> A
  E (1) --1--> E
  E (1) --0--> D
  F (1) --1--> C
  F (1) --0--> D

Assume that a one-hot code is used with the state assignment y[5:0] =
000001(A), 000010(B), 000100(C), 001000(D), 010000(E), 100000(F)

The module should implement the state output logic for this finite-state
machine. The output signal Y1 should be the input of state flip-flop
y[1]. The output signal Y3 should be the input of state flip-flop y[3].
Derive the implementation by inspection assuming the above one-hot
encoding.

For zero-hot or multi-hot inputs, OR the contributions of every active state, without priority selection. The block is combinational; the current-state input is not an internal register.

## Reset Behavior

There is no RTL reset or persistent state.
