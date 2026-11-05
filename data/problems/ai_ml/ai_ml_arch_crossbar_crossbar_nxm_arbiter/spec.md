# Crossbar3x2

Route three independent 16-bit input streams to two independent output streams, with per-output round-robin arbitration and one buffered word per output. This is a finite, single-beat stream-switch derivative, not a request-mask priority encoder.

## Interface

### RTL interface

```systemverilog
module crossbar_3x2 (
    input logic clk, rst,
    input logic s0_valid, s0_dest,
    input logic [15:0] s0_data,
    output logic s0_ready,
    input logic s1_valid, s1_dest,
    input logic [15:0] s1_data,
    output logic s1_ready,
    input logic s2_valid, s2_dest,
    input logic [15:0] s2_data,
    output logic s2_ready,
    input logic m0_ready,
    output logic m0_valid,
    output logic [15:0] m0_data,
    output logic [1:0] m0_source,
    input logic m1_ready,
    output logic m1_valid,
    output logic [15:0] m1_data,
    output logic [1:0] m1_source
);
```

### Behavior interface

```python
class Crossbar3x2:
    def __init__(self): ...
    def reset(self): ...
    def process(self, s0_valid, s0_dest, s0_data, s1_valid, s1_dest, s1_data,
                s2_valid, s2_dest, s2_data, m0_ready, m1_ready): ...
```

One call samples all input pins immediately before a non-reset rising edge,
returns a dictionary of all nine output pins at that edge, then updates state.
This sampled interface observes the actual concurrent stream protocols; it
does not add a handshake around the complete pin record. Reset edges have no
transaction record. The dictionary has exactly these keys: `s0_ready`,
`s1_ready`, `s2_ready`, `m0_valid`, `m0_data`, `m0_source`, `m1_valid`,
`m1_data`, and `m1_source`. All values are unsigned Python integers: ready/valid
flags are 0 or 1, data words are in [0, 65535], and source numbers are 0, 1 or 2.
Return the complete dictionary even when idle, not `None` or a list. Input
arguments encode the unsigned pins at their declared widths. `reset()` restores
empty buffers and source-0 priorities and returns no response.

## Modules

- `crossbar_3x2`: two independent arbiters, data-routing paths and output buffers.

## Functional Behavior

Each source holds valid, destination and payload while stalled; each sink
independently controls its ready input.

Each source's one-bit destination selects output 0 or 1. Each output stores
valid, 16-bit data and a two-bit source number (0, 1 or 2). When invalid its
data and source pins are zero. Its initial priority is source 0.

For each output independently, room exists when its buffer is empty or the
sink is accepting the old buffered word on this edge. If room exists, examine
sources in circular order starting at that output's priority. Select the first
valid source addressed to it; assert ready only for that source. Otherwise no
source is selected. A source addresses only one output and cannot be accepted
twice on one edge. Thus two differently addressed words may transfer together.

On each edge, retire any valid output with ready=1. Then load each selected
source into its destination buffer, possibly replacing the word just retired.
The loaded word becomes visible after the edge. Advance that output's priority
to the source after the accepted source, modulo three. No accepted source means
no priority advance. A full stalled output holds valid, data and source and
accepts nothing; it does not block the other output. Under continuous
contention and a continuously ready sink, each requester is served once per
round of at most three accepted words.

For completeness, nonconforming input chatter is interpreted by the same
sampled equations: only an edge with both source valid and ready accepts data.
The switch does not remember unaccepted offers. This does not relax the sender
hold rule for compliant traffic.

Source provenance is the routing, per-output arbitration, and registered-output
structure of verilog-axis `axis_switch`/`arbiter`. This derivative fixes 3x2
ports, 16-bit payloads, single-beat packets and a one-word output buffer; omits
optional keep/user/ID fields and configurable route tables; and specifies the
above accept-driven arbitration without upstream decode/grant pipeline stages.
It does not claim cycle equivalence to the full AXI-stream implementation.
The legacy source selector pointed to a clock-crossing helper, not a crossbar;
that selector is superseded in the accompanying audit, not silently reused.

## Reset Behavior

`rst` is synchronous active high. It clears both buffers and restores both
priorities to zero. Source ready is zero during reset. Buffered words are
discarded; pre-reset words do not reappear after reset.
