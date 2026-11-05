# freq_divbyodd

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module freq_divbyodd #(parameter int NUM_DIV = 5)(
    input logic clock_level, rst_n, output wire clk_div
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self, NUM_DIV=5): ...
    def reset(self): ...
    def process(self, clock_level, rst_n): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `clk_div`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Input-level observations

Inputs are applied as explicit physical levels, then all settled output pins
are observed. Repeated identical inputs create separate observations but no
new signal transition. No automatic periodic clock is inserted between
observations. All input-level sequences are defined.

## Modules

- `freq_divbyodd`: the actual latch or edge-triggered circuit below.

## Functional Behavior

NUM_DIV is an odd integer at least 3, default 5. Two independent
modulo-NUM_DIV counters count rising and falling input edges. Each has an output
phase bit. Reset sets both counters to zero and BOTH phase bits to one, so
clk_div is one throughout reset.

At a released rising edge, set the positive phase bit to (old positive counter
< NUM_DIV//2), then increment that counter modulo NUM_DIV. Do not touch the
negative counter/bit. At a falling edge perform the symmetric operation on the
negative pair. clk_div is always the OR of the two phase bits. An ordinary held
input level cannot advance either counter. Use the OLD counter in each test.

This is the source's two-half-cycle construction, including its startup phase.
For a regular 50%-duty input clock, the steady output period is NUM_DIV input
periods with 50% duty. Preserve the actual phase waveform, not merely the number
of ones in a transaction stream.

### Phase-level observation and startup

The source clk input is renamed clock_level. It is still the actual event
source of the RTL posedge/negedge blocks; it is supplied as explicit physical
levels instead of an automatically toggled Sim clock. Each input record applies
clock_level and rst_n together, then observes clk_div after digital settling.
Repeated high/low levels do NOT create extra edges. Supply every clock/reset
change of interest; no hidden periodic edges occur between records. There is
no handshake, enable, result queue or token counter replacing the divider.

rst_n remains asynchronous and active low. A low level resets all divider
registers regardless of clock transitions. Deasserting reset while the clock
stays high does not create a rising edge. If deassertion coincides with a real
edge, process that edge using the released reset and the old initialized state.
The initial observed clock level is zero; all stored values are explicitly
initialized to the reset state even if the first observation has rst_n=1.
That deterministic startup is a completion of the source's unspecified startup,
not a claim that the upstream implementation guarantees power-up values.

The original fixed counter width is replaced by ceil(log2(divisor)), retaining
its recurrence while making the declared parameters executable. No analog
metastability or pre-settle glitch behavior is specified. Changes in absolute
clock period scale the output waveform; they do not change these edge rules.


## Reset Behavior

The native rst_n is an ordinary observed control input: low asynchronously restores the stated divider state, without requiring a clock edge. Each fresh execution also starts in that state as an explicit startup completion.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
