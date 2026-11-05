# Edge Detect

A synchronous one-bit edge detector samples `a` on every rising edge of `clk` and emits a one-cycle rising or falling pulse. There is no enable, valid signal, or backpressure.

## Interface

### RTL interface

```systemverilog
module edge_detect (
    input logic clk,
    input logic rst_n,
    input logic a,
    output logic rise,
    output logic down
);
```

All data ports are unsigned one-bit values.

### Behavior interface

```python
class EdgeDetect:
    def __init__(self): ...
    def reset(self): ...
    def process(self, a): ...
```

Call `process` at every non-reset rising edge, even when `a` is unchanged.
It returns one dictionary `{"rise": ..., "down": ...}` with the pin values
immediately before that edge, then advances the state. Arguments and returned
fields are Python integers, `0` or `1`. The first observation after reset is
`{"rise": 0, "down": 0}`; a zero-valued observation is still a record, not
`None`. Call `reset` separately; it produces no output record.

## Modules

- `edge_detect`: input history and registered transition indicators.

## Functional Behavior

Let `prev` denote the last sampled input. At each rising edge, use the old `prev` to set `rise = a & ~prev` and `down = ~a & prev`, then set `prev = a`. All values are one bit. The new outputs remain visible until the next edge. A held input clears both pulses on the following edge; pulses are not sticky. Alternating inputs produce alternating rising and falling pulses, never both at once.

## Reset Behavior

`rst_n` is asynchronous and active low. Assertion clears `prev`, `rise`, and `down` to zero. No records are compared during reset. After release, the first edge compares `a` with zero; a high input produces a rising pulse after that edge.
