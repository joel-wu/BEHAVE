# Up Down Counter

Maintain a 16-bit counter that increments or decrements on every rising edge of `clk`, without a handshake or count enable.

## Interface

### RTL interface

```systemverilog
module up_down_counter (
    input logic clk,
    input logic reset,
    input logic up_down,
    output logic [15:0] count
);
```

### Behavior interface

```python
class UpDownCounter:
    def __init__(self): ...
    def reset(self): ...
    def process(self, up_down): ...
```

Each call represents one non-reset clock edge and returns `{"count": old_count}` before applying that edge's direction. Both directions are meaningful inputs; a zero direction is not an idle cycle.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `count`: 16.

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

- `up_down_counter`: up down counter.

## Functional Behavior

`up_down = 1` increments the count and `up_down = 0` decrements it. Arithmetic wraps modulo 65536: incrementing 65535 yields 0, and decrementing 0 yields 65535. The unsigned output `count` continuously reflects the stored state. Changing direction never resets the state.

## Reset Behavior

`reset` is asynchronous and active high, matching the original RTL's event sensitivity. Assertion immediately clears the count to zero; no pre-reset state is retained.
