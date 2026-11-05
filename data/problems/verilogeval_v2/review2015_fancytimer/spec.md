# TopModule

Search data for 1101 with overlapping prefixes.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input wire clk,
  input wire reset,
  input wire data,
  output wire [3:0] count,
  output reg counting,
  output reg done,
  input wire ack
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data, ack): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `count`: 4.
- `counting`, `done`: 1.

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

- `TopModule`: the complete native module above.

## Functional Behavior

Search data for 1101 with overlapping prefixes. Capture the NEXT four bits MSB first as delay (0..15). Count for exactly (delay+1)*1000 cycles; counting remains high throughout. During each group of 1000 cycles, count is delay, delay-1, ..., 0. Then hold done=1 until ack is sampled, returning to search. Ignore ack except while done and data except while searching/capturing. Reset cancels the operation. Explicitly define count=0 outside counting, completing the source don't-care. Do not replace 1000 with a small test constant.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. Search state; count=counting=done=0. Hidden capture/counter state is overwritten before the next counting interval.
After reset release, resume the edge-by-edge behavior above.
