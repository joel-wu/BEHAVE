# bcd_counter

A six-digit 24-hour BCD clock, advanced by one second per input edge.

## Interface

### RTL interface

```systemverilog
module bcd_counter (
    input logic clk, input logic rst,
    output logic [3:0] ms_hr, ls_hr, ms_min, ls_min, ms_sec, ls_sec
);
```

### Behavior interface

```python
class BcdCounter:
    def __init__(self): ...
    def reset(self): ...
    def process(self): ...
```

Each call returns the settled pre-edge values of all output pins, then advances
one rising edge. Arguments and returned fields are unsigned port bit patterns.
Reset is controlled separately; reset observations emit no logical records.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `ls_hr`, `ls_min`, `ls_sec`, `ms_hr`, `ms_min`, `ms_sec`: 4.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `bcd_counter`: the complete native module with the ports and parameters above.

## Functional Behavior

Every rising edge advances the BCD clock by one second. Seconds and minutes
range from 00 to 59, hours from 00 to 23. Carry through the decimal digits;
23:59:59 advances to 00:00:00. There is no divider, enable or transaction stall.
All six output digits are defined continuously, including before the next edge.

## Reset Behavior

`rst` is asynchronous and active high. Assertion immediately clears all state and output pins to zero.
Reset discards incomplete work. The next released edge resumes from zero.
