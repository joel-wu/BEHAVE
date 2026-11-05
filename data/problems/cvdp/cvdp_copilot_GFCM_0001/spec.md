# cvdp_copilot_GFCM_0001

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module glitch_free_mux(
    input logic clk1_level, clk2_level, sel, rst_n, output wire clkout
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self): ...
    def reset(self): ...
    def process(self, clk1_level, clk2_level, sel, rst_n): ...
```

Each call applies one input-level record and returns the post-settle outputs.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `clkout`: 1.

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

- `glitch_free_mux`: the stateful digital circuit below.

## Functional Behavior

Switch clkout between clk1_level (sel=0) and clk2_level (sel=1), with
break-before-make clock gating. rst_n is asynchronous active low and clears both
path enables. Both path enables initially equal zero. clkout is the OR of each
physical clock level AND its path enable.

At a falling edge of clk1_level, set enable1 to (!sel AND !old enable2).
At a falling edge of clk2_level, set enable2 to (sel AND !old enable1).
No enable changes at a rising edge or merely because sel changes. If both clocks
fall in the same input record, BOTH updates read the same old enable pair.
Reset dominates all updates. Only one enable can be set at a time.

A selected source therefore completes its high pulse before being disabled.
The other source cannot be enabled until its own falling edge observes the old
source disabled; the first following rising edge is forwarded in full. Either
direction can have an all-low gap. A stopped-high old clock delays a handoff
until it falls; there is no invented progress clock. Changing sel during high
or repeating a high sample must not truncate a pulse or create a new pulse.
Reset is the exception: asserting it forces clkout low immediately.

### Source conflict resolution

The source asks for glitch-free gating but also requires gate changes on
positive edges with a fixed handoff latency. Positive-edge changes of a direct
clock AND gate can produce short pulses; its TB checks settled levels rather
than proving pulse integrity. No answer RTL is supplied. This task prioritizes
glitch-free digital handoff and explicitly chooses falling-edge, cross-inhibited
gate updates. This CHANGES the stated positive-edge switching latency; it is
not unchanged-source equivalence. The old transaction-counting surrogate is
removed. Clock pins are renamed clk1_level/clk2_level solely to make the supplied
level-sequence boundary explicit.

### Explicit digital observation

Clock pins are ordinary physical levels supplied in every input record, not
automatically toggled domains. The RTL still contains real edge-sensitive and
clock-gating logic. All record fields change together and outputs are observed
after settling. A repeated high level is not another edge. Every relevant level
or data change must be supplied; there are no hidden clock pulses between calls.
Both clocks/history event levels initially equal zero. Internal state initially
equals reset state even when the first record releases reset: an explicit
completion of unspecified power-up state. Reset assertion takes effect without
any clock edge. Releasing reset at a held high level is not a new edge.

These rules define all digital input records, including coincident events; the
guarantees do not include analog metastability, propagation-delay glitches or
minimum pulse widths in seconds. No ready/valid wrapper may stop the underlying
clock-driven or history-triggered state updates.


## Reset Behavior

The physical active-low reset is an ordinary input and asynchronously restores the stated reset state. Initial state is the same as reset, with clock/history levels initially zero.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
