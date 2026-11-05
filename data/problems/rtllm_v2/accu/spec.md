# Four-Sample Accumulator

Sum consecutive groups of four enabled unsigned 8-bit samples. The native interface is valid-only; it has no ready signal or output backpressure.

## Interface

### RTL interface

```systemverilog
module accu (
    input logic clk,
    input logic rst_n,
    input logic [7:0] data_in,
    input logic valid_in,
    output logic valid_out,
    output logic [9:0] data_out
);
```

### Behavior interface

```python
class Accu:
    def __init__(self): ...
    def reset(self): ...
    def process(self, data_in): ...
```

Each call represents an enabled sample, with `data_in` a Python integer in
[0, 255]. The first three calls in each group return `None`; the fourth returns
exactly one dictionary with key `data_out`, an integer in [0, 1020]. A completed
all-zero group returns `{"data_out": 0}`, not `None`. Do not return a list or
include the RTL valid flag. Calls do not represent disabled clock edges.
The partial group persists between calls. `reset()` discards it and returns
no response. No truncation or saturation of the group sum is needed.

## Modules

- `accu`: four-sample accumulator.

## Functional Behavior

At every rising edge with `valid_in = 1`, add `data_in` to the current group. A fourth enabled sample completes the group and produces its exact 10-bit sum, in the range 0 through 1020. Groups do not overlap.

If the fourth sample is captured at edge k, `valid_out = 1` and its sum are visible after that edge and sampled at edge k+1. The pulse lasts one clock. Cycles with `valid_in = 0` do not advance the count or change the partial sum; in particular, idle clocks cannot create outputs. Gaps preserve a partial group, and an incomplete final group is legal and emits nothing. `data_out` need only be interpreted while `valid_out = 1`, except that reset clears it.

## Reset Behavior

`rst_n` is asynchronous and active low. Assertion clears the partial sum, sample count, `valid_out`, and `data_out`. Partially accumulated groups are discarded.
