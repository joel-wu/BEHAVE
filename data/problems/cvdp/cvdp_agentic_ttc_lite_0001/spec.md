# ttc_counter_lite

A continuously clocked 16-bit timer with programmable prescaler, interval/single-shot modes, sticky interrupt and independent native register reads/writes.

## Interface

### RTL interface

```systemverilog
module ttc_counter_lite (
    input logic clk, reset,
    input logic [3:0] axi_addr,
    input logic [31:0] axi_wdata,
    input logic axi_write_en, axi_read_en,
    output logic [31:0] axi_rdata,
    output logic interrupt
);
```

### Behavior interface

```python
class TtcCounterLite:
    def __init__(self): ...
    def reset(self): ...
    def process(self, axi_addr, axi_wdata, axi_write_en, axi_read_en): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `axi_rdata`: 32.
- `interrupt`: 1.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `ttc_counter_lite`: the complete native module above.

## Functional Behavior

The timer advances on physical rising edges, whether or not either register
enable is asserted. This is the source's simplified register interface, NOT
five-channel AXI, and it has no ready/valid or output acknowledgement.

| axi_addr | Register | Read value / write effect |
| --- | --- | --- |
| 0 | COUNT | Current unsigned 16-bit count; writes ignored |
| 1 | MATCH_VALUE | 16-bit target; writes take axi_wdata[15:0] |
| 2 | RELOAD_VALUE | 16-bit reload; writes take axi_wdata[15:0] |
| 3 | CONTROL | Bits 0=enable, 1=interval mode, 2=interrupt enable |
| 4 | STATUS | Bit 0=sticky match; write bit 0 as 1 to clear |
| 5 | PRESCALER | Low four bits; divides the clock by value+1 |
| 6..15 | Reserved | Read zero; writes ignored |

All unused read bits are zero; unused write bits are ignored. At an edge with
axi_read_en=1, axi_rdata registers the selected PRE-edge register value. It holds
when read enable is zero. Independently, axi_write_en=1 writes at that edge.
A simultaneous read and write to one address returns its old value, not the
write data. Reset has priority over both paths.

While disabled, retain count and clear the divider phase. Enabling starts a
fresh period: the first increment occurs prescaler+1 clocks AFTER the enable
write. While enabled, increment count modulo 65536 on each divider pulse.
Writing PRESCALER resets its phase even when the new value equals the old one;
the next pulse is new_prescaler+1 clocks after that write. Otherwise retain
the phase across CONTROL writes that keep the timer enabled.

A match event occurs on an enabled edge if the pre-edge count already equals
MATCH_VALUE, or if that edge's divider increment reaches MATCH_VALUE. Do not
delay recognition until another prescaler period. An event sets sticky STATUS
regardless of interrupt enable and clears divider phase. In interval mode,
replace count with RELOAD_VALUE; otherwise stop at the matched count and clear
CONTROL.enable. If reload equals match, interval mode matches on every subsequent
enabled edge. interrupt is the live level STATUS[0] AND CONTROL[2]; masking it
does not clear status, and unmasking reasserts it if status is still set.

Resolve same-edge effects as follows. Reads and timer decisions use PRE-edge
configuration. Then writes take effect; a CONTROL write overrides single-shot
automatic disable, while MATCH/RELOAD changes apply to later edges. A match
event wins over simultaneous STATUS write-one-clear so a new event is not lost.
STATUS write zero does nothing. No read or idle clock implicitly clears status.

Source completions: sticky status/masking, registered read hold and old-value
read-during-write, divider restart, equality/overflow and same-edge priorities
were not fully specified upstream and are fixed above. This also corrects the
legacy rewrite's prescaler-delayed match (up to 16 clocks), incompatible with
the source's one-clock interrupt bound. No source top-level RTL or TB is supplied;
these are explicit coherent completions, not an unchanged upstream equivalence claim.


## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All six registers, divider phase and read output are zero. The timer is disabled and interrupt is low.
After reset release, resume the edge-by-edge behavior above.
