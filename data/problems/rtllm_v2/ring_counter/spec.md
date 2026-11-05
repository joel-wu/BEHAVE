# 8-bit Ring Counter

Rotate a single asserted bit through an eight-bit register on every rising clock edge, without a data input or handshake.

## Interface

### RTL interface

```systemverilog
module ring_counter (
    input logic clk,
    input logic reset,
    output logic [7:0] out
);
```

### Behavior interface

```python
class RingCounter:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

One call represents one non-reset rising edge. It returns `{"out": old_out}` sampled immediately before that edge, then updates the state. The updated state is observed on the next call. Reset edges produce no observation records.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out`: 8.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `ring_counter`: the top-level native clocked module shown above.

## Functional Behavior

At each rising edge, rotate the current register left by one bit: `out_next = {out[6:0], out[7]}`. From reset the output sequence is `01,02,04,08,10,20,40,80,01,...` in hexadecimal. Exactly one bit is asserted throughout every reset-started execution. The output continuously exposes the register.

## Reset Behavior

`reset` is asynchronous and active high. It immediately sets `out` to `8'h01`.
