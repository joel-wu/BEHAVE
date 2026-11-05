# 5-bit Triangle Wave Generator

Generate an unsigned triangle wave from 0 to 31 and back, advancing on every rising clock edge.

## Interface

### RTL interface

```systemverilog
module signal_generator (
    input logic clk,
    input logic rst_n,
    output logic [4:0] wave
);
```

### Behavior interface

```python
class SignalGenerator:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

One call represents one non-reset rising edge. It returns `{"wave": old_wave}` sampled immediately before that edge, then updates the state. The updated state is observed on the next call. Reset edges produce no observation records.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `wave`: 5.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `signal_generator`: the top-level native clocked module shown above.

## Functional Behavior

The internal direction starts upward. While going up, increment `wave` unless it is 31; at 31, hold the value for that edge and change direction downward. While going down, decrement unless the value is 0; at 0, hold the value for that edge and change direction upward.

The sampled sequence is `0,1,...,30,31,31,30,...,1,0,0,1,...`, with period 64. Both extrema occupy two successive samples. There is no data input, backpressure, or valid/ready handshake.

## Reset Behavior

`rst_n` is asynchronous and active low. It immediately clears `wave` to zero and restores the upward direction.
