# moving_average

Continuously average the most recent eight unsigned 12-bit samples.

## Interface

### RTL interface

```systemverilog
module moving_average (
    input logic clk, reset,
    input logic [11:0] data_in,
    output logic [11:0] data_out
);
```

### Behavior interface

```python
class MovingAverage:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: 12.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `moving_average`: the complete native module above.

## Functional Behavior

Capture one unsigned 12-bit sample on every rising edge. The output is the
floor of the sum of the most recently captured eight samples divided by eight.
Before eight samples have arrived, missing history entries count as zero;
do not divide by the number of samples seen. An exact 15-bit sum suffices.
The output reflects the new window immediately after each edge and otherwise
holds. Continuous sampling has no enable, ready, valid or backpressure.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
