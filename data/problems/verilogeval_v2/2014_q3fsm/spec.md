# TopModule

Wait in A for an edge with s=1; its w is not counted.

## Interface

### RTL interface

```systemverilog
module TopModule (
  input clk,
  input reset,
  input s,
  input w,
  output reg z
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, s, w): ...
```

Call `process(s, w)` on every non-reset rising edge, including idle cycles,
with both arguments equal to integer `0` or `1`. Each call returns the settled
output immediately **before** that edge, then advances the edge:

```python
{"z": ...}
```

This is one dictionary with exactly the key `z` and integer value `0` or `1`,
not `None` or a list. Return it even before startup or between pulses. Startup
and partial-group state persist between calls. Reset is controlled separately:
`reset()` restores the idle state and returns `None`, without producing a pin
sample or advancing a clock edge.

## Modules

- `TopModule`: the complete native module above.

## Functional Behavior

Wait in A for an edge with s=1; its w is not counted. Thereafter ignore s and examine nonoverlapping groups of three w samples. z pulses in the cycle immediately FOLLOWING a group containing exactly two ones. That output cycle also samples the first bit of the next group. Reset cancels any partial group.

Every non-reset rising edge is processed; there is no ready/valid wrapper or output backpressure.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. Idle; no partial group; z=0.
After reset release, resume the edge-by-edge behavior above.
