# 64-bit Johnson Counter

Generate the 128-state Johnson sequence with a 64-bit register. The counter advances on every rising clock edge; it has no data input or handshake.

## Interface

### RTL interface

```systemverilog
module JC_counter (
    input logic clk,
    input logic rst_n,
    output logic [63:0] Q
);
```

### Behavior interface

```python
class JohnsonCounter:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

One call represents one non-reset rising edge. It returns `{"Q": old_Q}` sampled immediately before that edge, then updates the state. The updated state is observed on the next call. Reset edges produce no observation records.

The result is one dictionary, with `Q` a Python integer in
0..18446744073709551615 carrying the 64 output bits. The initial zero state
still produces `{"Q": 0}`. `reset()` restores that state and returns `None`.

## Modules

- `JC_counter`: the top-level native clocked module shown above.

## Functional Behavior

Let `q` be the current unsigned 64-bit state. At each rising edge, shift it right by one and put the inverse of its old bit 0 into bit 63:

`q_next = (q >> 1) | ((1 - (q & 1)) << 63)`.

`Q` continuously exposes `q`. Starting at zero, successive states fill with ones from the most significant bit: after 64 edges all bits are one. Zeros then enter from the same end, returning to zero after 128 edges. This is a 64-bit counter, not a six-bit counter or a 64-state sequence.

## Reset Behavior

`rst_n` is asynchronous and active low. It immediately sets all 64 bits of `Q` to zero.
