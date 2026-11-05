# dig_stopwatch

A pausable stopwatch that retains partial seconds and saturates at one hour.

## Interface

### RTL interface

```systemverilog
module dig_stopwatch #(
    parameter int CLK_FREQ = 50000000
) (
    input logic clk, input logic reset, input logic start_stop,
    output logic [5:0] seconds, minutes, output logic hour
);
```

### Behavior interface

```python
class DigStopwatch:
    def __init__(self, CLK_FREQ=50000000): ...
    def reset(self): ...
    def process(self, start_stop): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `hour`: 1.
- `minutes`, `seconds`: 6.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `dig_stopwatch`: the complete native module with the ports and parameters above.

## Functional Behavior

`CLK_FREQ` is a SystemVerilog positive int in 2..2147483647; its
default is 50,000,000. Every rising edge with `start_stop=1` advances the partial
second by one input-clock period. Every CLK_FREQ such edges increments seconds;
seconds carry at 60, then minutes carry at 60. On reaching one hour, hold
`hour=1, minutes=0, seconds=0` until reset. Do not wrap beyond one hour.

`start_stop=0` pauses both displayed time and partial-second progress. Resume
from exactly that partial count. A one-input-clock-period internal second pulse
marks each increment; it may be implemented as a clock enable, without adding
a derived external clock. All counters update together on the corresponding
input edge.

## Reset Behavior

`reset` is asynchronous and active high. Assertion immediately clears all state and output pins to zero.
Reset discards incomplete work. The next released edge resumes from zero.
