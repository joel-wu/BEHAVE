# cvdp_copilot_perf_counters

Count CPU events and read-and-clear the count through a software request.

## Interface

### RTL interface

```systemverilog
module cvdp_copilot_perf_counters #(parameter int CNT_W = 32) (
    input logic clk, reset, sw_req_i, cpu_trig_i,
    output logic [CNT_W-1:0] p_count_o
);
```

### Behavior interface

```python
class PerformanceCounter:
    def __init__(self, CNT_W=32): ...
    def reset(self): ...
    def process(self, sw_req_i, cpu_trig_i): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `p_count_o`: CNT_W.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
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

- `cvdp_copilot_perf_counters`: the complete native module above.

## Functional Behavior

`CNT_W >= 1`, default 32. Count one event per edge with `cpu_trig_i=1`,
wrapping modulo `2**CNT_W`. The combinational output is the current counter
when `sw_req_i=1`, and zero otherwise. On an edge with a software request,
clear the old count; a simultaneous trigger contributes one to the NEW counting
interval. Precisely, `next_count = ((sw_req_i ? 0 : count) + cpu_trig_i)` modulo
the counter range. A read therefore samples the pre-edge count, never discards
a coincident trigger, and repeated requests repeatedly start fresh intervals.
This fixes the source's otherwise ambiguous simultaneous-event ordering.

## Reset Behavior

`reset` is asynchronous, active high. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
