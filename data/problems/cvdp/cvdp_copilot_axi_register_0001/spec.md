# axi_register

Parameterized AXI4-Lite register slave with independent address/data channels, stable responses and autonomous done capture.

## Interface

### RTL interface

```systemverilog
module axi_register #(
    parameter int ADDR_WIDTH=32, parameter int DATA_WIDTH=32
) (
    input logic clk_i, rst_n_i,
    input logic [ADDR_WIDTH-1:0] awaddr_i,
    input logic awvalid_i,
    input logic [DATA_WIDTH-1:0] wdata_i,
    input logic wvalid_i,
    input logic [DATA_WIDTH/8-1:0] wstrb_i,
    input logic bready_i,
    input logic [ADDR_WIDTH-1:0] araddr_i,
    input logic arvalid_i, rready_i, done_i,
    output logic awready_o, wready_o,
    output logic [1:0] bresp_o,
    output logic bvalid_o, arready_o,
    output logic [DATA_WIDTH-1:0] rdata_o,
    output logic rvalid_o,
    output logic [1:0] rresp_o,
    output logic [19:0] beat_o,
    output logic start_o, writeback_o
);
```

### Behavior interface

```python
class AxiRegister:
    def __init__(self, ADDR_WIDTH=32, DATA_WIDTH=32): ...
    def reset(self): ...
    def process(self, awaddr_i, awvalid_i, wdata_i, wvalid_i, wstrb_i, bready_i, araddr_i, arvalid_i, rready_i, done_i): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `arready_o`, `awready_o`, `bvalid_o`, `rvalid_o`, `start_o`, `wready_o`,
  `writeback_o`: 1.
- `beat_o`: 20.
- `bresp_o`, `rresp_o`: 2.
- `rdata_o`: DATA_WIDTH.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clk_i`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `axi_register`: the complete native module above.

## Functional Behavior

ADDR_WIDTH >=12; DATA_WIDTH >=8 and a multiple of8, default32 for both.
The multiple-of8 restriction completes the source's otherwise ambiguous byte
strobe width. For a data bus narrower than a register, writes zero-extend and
reads return the low DATA_WIDTH bits. Wider buses zero-extend reads. Decode
exactly address bits[11:0]; higher address bits are ignored, but low misaligned
bits are not. Registers and response codes are:

| Low12-bit byte offset | Register | Effect |
| --- | --- | --- |
| 0x100 | Beat | Read/write20-bit value, not an autonomously incrementing counter |
| 0x200 | Start | Read/write bit0, held level; writing0 clears it |
| 0x300 | Done | Read sticky status; full write of bit0=1 clears it |
| 0x400 | Writeback | Read/write bit0, held level; writing0 clears it |
| 0x500 | ID | Read-only constant0x00010001 |

All valid reads return OKAY(0); unknown reads return data0 and SLVERR(2).
Writes to ID or unknown addresses return SLVERR regardless of strobes.
For writable registers, update ONLY if all DATA_WIDTH/8 strobe bits are1.
Any partial/zero-strobe write returns OKAY and changes no writable register.
This deliberately follows the source's explicit full/partial-write rule, resolving
its conflicting later suggestion that the lowest byte alone could update controls.

AW and W have independent one-entry buffers. Accept each whenever its own buffer
is empty and no B response is pending; either channel may arrive first. With both
buffers occupied before an edge, commit the captured write on that edge, empty
both buffers and assert BVALID with its response. Do not commit a write until
both halves have arrived. Hold BVALID/BRESP unchanged until a BREADY edge. No new
write half is accepted while BVALID is high, including the response-consumption
edge. Ignored live address/data changes cannot alter a captured request.

The read channel is independent of all write activity. ARREADY is high when
no R response is pending. An AR handshake captures read data and response at that
edge and asserts RVALID. Hold RDATA/RRESP/RVALID until RREADY consumes them; do
not accept another AR on the consumption edge. Responses retain their last
payload when invalid. All ready signals depend only on buffer/response state,
not on a peer VALID. These fixed timing choices permit bounded native pin checks.

done_i sets sticky Done on every clock, including idle and stalled bus cycles.
A full Done write of1 clears status with priority over done_i on that same edge;
a still-high done_i sets it again on the next edge. Writing0 does nothing.
Read sampling uses PRE-edge register values: concurrent write commit and done_i
capture become visible to later read requests, never by changing a held R payload.
beat_o, start_o and writeback_o continuously reflect their respective registers.

Active-low reset assertion aborts all buffered halves and responses and clears
the four writable registers. The source's sentence saying deassertion resets is
corrected to assertion, consistent with its active-low asynchronous requirement.
The ID remains constant. Clock names and all five native AXI channels are retained;
no extra command/response wrapper is introduced. Buffer latency, low12-bit decode,
sticky done, read/write races, narrow-bus extension and control level behavior are
explicit completions of source omissions. No upstream top-level answer exists.


## Reset Behavior

`rst_n_i` is asynchronous, active low. Assertion immediately restores the initial state. Registers and response payloads are zero; buffers are empty and response valids are low. AWREADY, WREADY and ARREADY are high for the empty channels.
After reset release, resume the edge-by-edge behavior above.
