# ttc_counter_lite

A continuously clocked 16-bit interval timer with native register read/write enables.

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

- `ttc_counter_lite`: the complete native module above.

## Functional Behavior

The native interface uses register enables, not AXI channel handshakes.
The timer evolves on EVERY rising edge, including edges with neither a read
nor a write. Register addresses are the literal 4-bit values below, not byte offsets.

| Address | Register | Write behavior |
| --- | --- | --- |
| 0 | count | ignored |
| 1 | match_value | store low 16 bits |
| 2 | reload_value | store low 16 bits |
| 3 | control | store bit 0 enable, bit 1 interval mode, bit 2 interrupt enable |
| 4 | status | clear interrupt, regardless of write data |

Reads are combinational: while `axi_read_en=1`, return the selected current
register, zero-extended to 32 bits; status is the interrupt bit. With read enable
low or an undefined address, return zero. Undefined writes have no effect.

On each edge use the PRE-EDGE register values for the timer update. If disabled,
hold count. If enabled and count differs from match_value, increment modulo 65536.
If equal, hold in non-interval mode or load reload_value in interval mode, and
set the sticky interrupt if interrupt_enable=1. Disabling interrupts does not
clear a previously latched interrupt. A status write clears it even if a match
occurs on that edge. Other writes take effect after that edge and affect the
next timer update. A simultaneous read/write observes the pre-edge register;
the combinational read subsequently follows its new value.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All registers and interrupt=0. Register reads consequently return zero.
After reset release, resume the edge-by-edge behavior above.
