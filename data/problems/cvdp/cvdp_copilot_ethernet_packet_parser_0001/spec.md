# ethernet_parser

Native always-accept packet field extraction with EOF-scoped validity.

## Interface

### RTL interface

```systemverilog
module ethernet_parser (
    input logic clk, rst, vld, sof,
    input logic [31:0] data,
    input logic eof,
    output logic ack,
    output logic [15:0] field,
    output logic field_vld
);
```

### Behavior interface

```python
class EthernetPacketParser:
    def __init__(self): ...
    def reset(self): ...
    def process(self, vld, sof, data, eof): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `ack`, `field_vld`: 1.
- `field`: 16.

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

- `ethernet_parser`: the complete native module above.

## Functional Behavior

`ack` is always 1, including reset. There is no downstream ready or
output-consumption event. Every rising edge with `vld=1` accepts a beat; invalid
cycles ignore all markers/data. A valid SOF starts or restarts a packet and is
beat 1, clearing field/field_vld and any pending extraction. On its second valid
beat capture data[31:16]. Publish that captured value and assert field_vld on the
NEXT rising edge, even if vld is then 0. Retain both until packet termination;
later data cannot overwrite the field. Saturate the 4-bit beat counter at 15,
rather than re-extracting after wraparound in a long packet.

A valid EOF ends the packet and clears field_vld/pending extraction. EOF wins
over starting, capturing or publishing validity on that same edge. A second
beat carrying EOF therefore produces no valid field; a one-beat SOF+EOF packet
does not extract. The field value after a valid EOF edge is determined as follows:

- If SOF is also asserted, field is zero, even if an old extraction was pending.
- Otherwise, if an extraction was pending before the edge, field receives that
  previously captured value.
- Otherwise, field retains its previous value.

In all three cases field_vld is zero. A new extraction on a second beat carrying
EOF is canceled and is not published. Markers on cycles with vld=0 are ignored,
but an already pending publication still occurs at that edge. The next valid
SOF or reset clears field. Ignore non-SOF beats while idle. Registered outputs
hold between edges.

Source completion: follow the prose's second beat INCLUDING SOF, not the
testbench's marker-only SOF followed by two data beats. The testbench catches
assertions and prints them, so it is not a passing oracle. Publication latency,
invalid-marker gating, EOF priority and counter saturation are explicit here;
the source's temporary register/DONE phase motivates the one-edge publication.


## Reset Behavior

`rst` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
