# m2014_q4a

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module TopModule(input logic d, ena, output logic q);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, d, ena): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `q`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

reset() starts a fresh zero-state execution, not a reset between ordinary input
observations.

### Input-level observations

Inputs are applied as explicit physical levels, then all settled output pins
are observed. Repeated identical inputs create separate observations but no
new signal transition. No automatic periodic clock is inserted between
observations. All input-level sequences are defined.

## Modules

- `TopModule`: the actual latch or edge-triggered circuit below.

## Functional Behavior

This is an actual level-sensitive D latch, not a flip-flop or a
transaction-gated register. While ena=1, q follows d, including multiple data
changes without an enable edge. When ena=0, q holds the last value captured
while enabled. Merely sampling a held disabled input cannot change q.

The source does not specify the initial latch value. This task explicitly
completes it as zero. No clock, reset, ready or valid port is added. The latch
equation and native d/ena/q pins are unchanged.

## Reset Behavior

There is no RTL reset pin. Each fresh RTL execution starts with the stated zero
initialization.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
