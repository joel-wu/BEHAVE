# ethernet_mac_tx

Native single-clock Ethernet frame RAM, configuration/readback, backpressured AXIS output and independent completion interrupt.

## Interface

### RTL interface

```systemverilog
module ethernet_mac_tx (
    input logic clk_in, rst_in, cfg_wr_in,
    input logic [31:0] cfg_addr_in, cfg_data_wr_in,
    input logic axis_tready_in,
    output logic [31:0] cfg_data_rd_out,
    output logic interrupt_out, axis_tvalid_out,
    output logic [31:0] axis_tdata_out,
    output logic [3:0] axis_tstrb_out,
    output logic axis_tlast_out
);
```

### Behavior interface

```python
class EthernetMacTx:
    def __init__(self): ...
    def reset(self): ...
    def process(self, cfg_wr_in, cfg_addr_in, cfg_data_wr_in, axis_tready_in): ...
```

Each `process` call represents one rising edge, including idle cycles. Supply
`cfg_addr_in` and `cfg_data_wr_in` as unsigned 32-bit integers and the other
arguments as 0 or 1. The call returns the pre-edge observations specified below,
then advances the edge. Its result is always one dictionary with exactly
`cfg_data_rd_out` and `axis_tdata_out` (unsigned 32-bit integers),
`axis_tstrb_out` (an unsigned 4-bit integer), and `interrupt_out`,
`axis_tvalid_out`, `axis_tlast_out` (0 or 1). Idle cycles also return all six
fields, never `None` or a list. Keep one instance across calls to preserve RAM,
configuration and transmission state. Construction initializes the reset
state; `reset()` restores it, returns `None` and produces no output record.

### Clock-edge observations

Observe all settled output pins immediately before each released rising edge
of `clk_in`, then advance that edge. Inputs and outputs are unsigned port bit
patterns. Idle cycles are clock edges too. Reset is controlled separately and
produces no logical records.

## Modules

- `ethernet_mac_tx`: the complete native module above.

## Functional Behavior

Transmit frames from a 1024-word, 32-bit internal RAM. The first three
words hold the destination MAC (six little-endian bytes) and source MAC (six
little-endian bytes); payload starts at byte address 12. This is a MAC frame
source, not an MII serializer: do not insert a preamble, FCS or completion beat.

### Configuration and dual-port storage

On each released rising edge the configuration RAM read register captures the
OLD word selected by `cfg_addr_in[11:2]`, including during writes and TX. Thus
RAM readback has one-cycle latency and writes are read-first. A RAM write on
that edge updates the word for subsequent reads. Bits [1:0] are ignored for
RAM accesses, explicitly completing the source's contradictory alignment text.
RAM reads alias on bits [11:2]; RAM writes require the FULL address below
0x1000. CSR decode uses the exact lower 16 bits and takes priority over RAM:

| Address | Write / readback |
| --- | --- |
| 0x07F4 | Store min(write_data[15:0], 1518); read the stored total frame length. |
| 0x07F8 | Store/read interrupt-enable bit 0. |
| 0x07FC | Store PROGRAM bit 1; bit 0 starts an operation. Read PROGRAM and actual BUSY in bits 1:0. |

With `cfg_wr_in=0`, CSR readback is combinational; any other address returns the
registered RAM read. With `cfg_wr_in=1`, readback is zero. Configuration writes
are accepted only when idle at the edge, including RAM writes; all busy-time
writes are ignored. Reads keep working while busy. The two logical RAM ports
may be implemented inside the top module; a separate source-file layout is not
part of the interface contract.

### Commands and physical timing

Writing control 0 or 2 only updates PROGRAM while idle. Writing 1 starts normal
TX and clears PROGRAM. Writing 3 starts program-only completion: no AXIS beat,
then END on the next edge. The host has already updated MAC RAM words; the
program-only command does not modify payload or RAM. A later write of 1 sends
the changed header with the retained payload. No config ready/valid is added.

A normal start enters READ with byte offset zero. At the next edge READ
captures the selected RAM word into a held output and enters PRESENT. PRESENT
sets `axis_tvalid_out=1`; ready=0 retains valid/data/strb/last exactly. On a
valid-and-ready edge consume the word. A nonfinal word advances offset by four
and enters READ again, giving one read/bubble cycle between held words. A final
word enters END instead. At the following END edge, clear BUSY and PROGRAM,
return idle and pulse `interrupt_out` for one cycle if enabled. BUSY includes
READ, PRESENT and END. Disabled IRQ remains zero; completion never waits for
ready after the final transfer. Output data/strb/last are zero whenever invalid.
These explicit cycle and IRQ-pulse rules complete the source's underspecified
FSM; they do not claim unchanged-source cycle equivalence.

### Length, byte lanes and padding

The length is TOTAL bytes, including the 12-byte MAC header, as in the source
specification. The source test named '1518' instead programs payload+12, which
contradicts its maximum. This task saturates the requested 16-bit length to
1518 rather than transmitting an oversized frame. A request below 12 simply
takes that prefix of RAM; a zero request sends 64 zero padding bytes. These are
explicit completions outside the documented normal MAC-header range.

Transmit max(length,64) bytes. Byte j below length is RAM byte j, least
significant lane first. All bytes from length up to the padded boundary are
zero, even if RAM holds nonzero data there. Each beat has low-lane strobes
F,7,3,1 for four,three,two,one remaining bytes; unused lanes are zero. Assert
last only on the final beat and hold it under backpressure. Backpressure is
supported despite the source's later 'ready always high' assumption.


## Reset Behavior

`rst_in` is asynchronous, active high. Assertion immediately restores the initial state. Clear all RAM words, read registers, configuration, IRQ and stream state. RAM reset-to-zero is an explicit deterministic completion: the original RAM has no stated reset contents. Reset aborts a stalled frame; it never emits a completion beat or IRQ.
After reset release, resume the edge-by-edge behavior above.
