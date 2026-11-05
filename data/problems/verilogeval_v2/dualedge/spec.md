# dualedge

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module TopModule(input logic clock_level, d, output logic q);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, clock_level, d): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `q`: 1.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Input-level observations

Inputs are applied as explicit physical levels, then all settled output pins
are observed. Repeated identical inputs create separate observations but no
new signal transition. No automatic periodic clock is inserted between
observations. All input-level sequences are defined.

## Modules

- `TopModule`: the actual latch or edge-triggered circuit below.

## Functional Behavior

Implement a dual-edge triggered flip-flop using distinct rising-edge
and falling-edge storage and a clock-level output selector, as requested by
the source FPGA exercise. A combined both-edge always block is not required.
On a 0-to-1 transition of clock_level, capture the current d in the positive
edge register. On a 1-to-0 transition, capture current d in the negative edge
register. At settled observation, q selects the positive register while high
and the negative register while low. Changing d while clock_level is held
constant does not change q. Both transition directions are equally observable.

### Explicit observation and initialization completion

The original clk pin is renamed clock_level and supplied as a sequence of
physical levels, not as an automatically toggled Sim clock. The RTL still uses
real posedge/negedge sensitivity on that input. Every level change and every
data change of interest must occur in a supplied observation; held-high and
held-low observations are legal and are not new clock edges. Input fields in
one observation change together, then all resulting digital events settle.
When clock and d change together, the new d is captured.

The initial physical clock level and both storage registers are zero. The
source left startup values undefined; this is a documented completion. There
is no added reset, handshake, output buffer or phase-counting surrogate. Delta
cycle transients before settling and analog glitches are outside this interface.

## Reset Behavior

There is no RTL reset pin. Each fresh RTL execution starts with the stated zero
initialization.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
