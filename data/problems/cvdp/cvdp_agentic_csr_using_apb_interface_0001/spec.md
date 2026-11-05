# csr_apb_interface

Four-register APB controller with executed SETUP/ACCESS states, protected interrupt status and every-clock event capture.

## Interface

### RTL interface

```systemverilog
module csr_apb_interface (
    input logic clk, rst,
    input logic pselx, penable, pwrite,
    input logic [31:0] paddr, pwdata,
    input logic overflow_is_set, sign_is_set, parity_is_set, zero_is_set,
    output logic [31:0] prdata,
    output logic pready, pslverr,
    output logic [1:0] debug_state
);
```

### Behavior interface

```python
class CsrApbInterface:
    def __init__(self): ...
    def reset(self): ...
    def process(self, pselx, penable, pwrite, paddr, pwdata, overflow_is_set, sign_is_set, parity_is_set, zero_is_set): ...
```

Every `process` call supplies all nine arguments and represents one non-reset
rising edge, including idle and incomplete APB cycles. It returns one dictionary
with exactly `prdata` (unsigned 32-bit), `pready` and `pslverr` (one-bit), and
`debug_state` (unsigned 2-bit), observed immediately before that edge, then
advances the state. Addresses and write data are unsigned 32-bit words; all
other arguments are one-bit values. Idle, wait and error cycles still return
all four keys, never `None` or a list. Construction and `reset()` restore the
registers, captured request and FSM to the reset state. `reset()` returns `None`
without advancing a non-reset edge or emitting a record.

## Modules

- `csr_apb_interface`: the complete native module above.

## Functional Behavior

Use the native APB phases and four observable states: IDLE=0, SETUP=1,
READ_STATE=2 and WRITE_STATE=3. On an edge with pselx=1 and penable=0, capture
paddr, pwdata and pwrite and enter SETUP. The first following ACCESS edge
(pselx=penable=1) enters READ_STATE or WRITE_STATE without completing the access.
In that state pready=1 while pselx=penable=1; the following edge completes the
transfer and returns to IDLE. Thus every transfer has one ACCESS wait cycle.
Keep request pins stable through completion. A new transfer can use a SETUP
cycle immediately after completion without deasserting pselx.

pready, prdata and pslverr are combinational responses to the current state.
prdata is the selected register during a completing read, otherwise zero.
pslverr is asserted only on completion for an unmapped address or an attempted
ISR write. Writes take effect at the completion edge, not at SETUP. debug_state
always reports the actual stored FSM state, not a fabricated completion label.
An ISR read samples status before events at its completion edge.

| Byte address | Register | Fields / behavior |
| --- | --- | --- |
| 0x10 | DATA_REG | data1=bits19:10, data2=bits9:0; upper12 bits are stored reserved data |
| 0x14 | CONTROL_REG | enable=bit0, mode=bit1; remaining30 bits are stored reserved data |
| 0x18 | INTERRUPT_REG | bits0..3 are overflow/sign/parity/zero enable; upper28 bits are stored |
| 0x1C | ISR_REG | bits0..3 are overflow/sign/parity/zero status; upper28 bits read zero |

Addresses match all32 bits exactly, including alignment. All four registers are
readable; the first three are writable as full32-bit words. ISR is write-protected.
Capture overflow_is_set/sign_is_set/parity_is_set/zero_is_set on EVERY non-reset
clock, even with no APB traffic and independently of the enable register.
Status bits are sticky. Completing a write to INTERRUPT_REG both stores its
value and clears the ISR bits selected by pwdata[3:0]. A clear wins over an
event on that same edge; other event bits are retained. Direct ISR writes
produce an error without clearing status or suppressing independent events.

For total behavior outside legal APB traces: deselecting aborts any unfinished
access; a new SETUP recaptures its payload; ACCESS without prior SETUP does
nothing. Holding ACCESS after its completion does not repeat the write.
The captured payload is used if request pins improperly change during ACCESS.

## Reset Behavior

`rst` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All registers and captured request fields are zero; the FSM is IDLE.
After reset release, resume the edge-by-edge behavior above.
