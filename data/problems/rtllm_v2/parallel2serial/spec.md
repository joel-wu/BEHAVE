# Four-bit Periodic Parallel-to-serial Converter

Capture a four-bit input every fourth rising edge and serialize it MSB first over the next four clock periods. The native valid_out pin marks the first bit of a frame, not the validity of every serial bit.

## Interface

### RTL interface

```systemverilog
module parallel2serial (
    input logic clk,
    input logic rst_n,
    input logic [3:0] d,
    output logic valid_out,
    output wire dout
);
```

### Behavior interface

```python
class Parallel2Serial:
    def __init__(self): ...
    def reset(self): ...
    def process(self, d): ...
```

One call represents one non-reset rising edge. It returns `{"valid_out": old_valid_out, "dout": old_dout}` sampled immediately before that edge, then updates the state. Reset edges produce no observation records.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `dout`, `valid_out`: 1.

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

- `parallel2serial`: the native clocked top module shown above.

## Functional Behavior

A two-bit counter and four-bit data register start at zero. On each rising edge, use their old values:

- If the counter is 3, capture `d`, clear the counter and set `valid_out` to 1.
- Otherwise increment the counter, rotate the data register left by one bit, and set `valid_out` to 0.

`dout` continuously equals the data register's most significant bit. For a captured word, the next four samples report bits 3, 2, 1 and 0, with `valid_out` respectively `1,0,0,0`. **The other three bits must still be observed when valid_out is zero.** Input values on non-capture edges have no effect. There is no input-valid pin, ready pin or output backpressure.

The first four non-reset samples have `valid_out=0` and `dout=0`. The fourth edge captures the first word; its MSB is observed at the fifth sample. Subsequent words follow without gaps, regardless of whether their values change.

## Reset Behavior

`rst_n` is asynchronous and active low. It clears the counter, data register and frame-start marker. A partially emitted frame is discarded.
