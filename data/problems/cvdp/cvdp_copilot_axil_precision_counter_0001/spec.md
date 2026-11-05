# precision_counter_axi

Native independently buffered AXI channels controlling a continuously clocked counter.

## Interface

### RTL interface

```systemverilog
module precision_counter_axi #(
    parameter int C_S_AXI_DATA_WIDTH=32,
    parameter int C_S_AXI_ADDR_WIDTH=8
) (
    input logic axi_aclk, axi_aresetn,
    input logic [C_S_AXI_ADDR_WIDTH-1:0] axi_awaddr,
    input logic axi_awvalid,
    input logic [C_S_AXI_DATA_WIDTH-1:0] axi_wdata,
    input logic [C_S_AXI_DATA_WIDTH/8-1:0] axi_wstrb,
    input logic axi_wvalid, axi_bready,
    input logic [C_S_AXI_ADDR_WIDTH-1:0] axi_araddr,
    input logic axi_arvalid, axi_rready,
    output logic axi_awready, axi_wready,
    output logic [1:0] axi_bresp,
    output logic axi_bvalid, axi_arready,
    output logic [C_S_AXI_DATA_WIDTH-1:0] axi_rdata,
    output logic [1:0] axi_rresp,
    output logic axi_rvalid, axi_ap_done, irq
);
```

### Behavior interface

```python
class PrecisionCounterAxi:
    def __init__(self, C_S_AXI_DATA_WIDTH=32, C_S_AXI_ADDR_WIDTH=8): ...
    def reset(self): ...
    def process(self, axi_awaddr, axi_awvalid, axi_wdata, axi_wstrb, axi_wvalid, axi_bready, axi_araddr, axi_arvalid, axi_rready): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `axi_ap_done`, `axi_arready`, `axi_awready`, `axi_bvalid`, `axi_rvalid`,
  `axi_wready`, `irq`: 1.
- `axi_bresp`, `axi_rresp`: 2.
- `axi_rdata`: C_S_AXI_DATA_WIDTH.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `axi_aclk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `precision_counter_axi`: the complete native module above.

## Functional Behavior

Restore the native AXI-Lite-style five-channel interface and a 32-bit
timer that advances on EVERY physical clock, including bus idle and response
stalls. Do not serialize it into an in_write transaction that ticks only when
software performs an access. C_S_AXI_ADDR_WIDTH>=8; C_S_AXI_DATA_WIDTH is a positive
multiple of eight. The main AXI4-Lite configuration is 32 bits, also supporting
64 bits. Narrow widths retain the source's explicit >=8 parameter extension;
they are not a claim that eight-bit buses are standard AXI4-Lite. Registers stay
32 bits: reads truncate/zero-extend to the bus, and only existing low four byte
lanes update a register. Higher bus lanes are ignored; narrow accesses preserve
unavailable register bytes. Each listed address selects one register, not adjacent
registers on a wide beat. Full addresses are decoded without truncation.

**Channel contract.** AW and W have independent single-entry holding registers:
accept either first or both together on valid && ready. AWREADY is one iff the
AW buffer is empty and no B response is pending; WREADY follows the same rule
for W. A pair already buffered at an edge commits exactly once at that edge,
then presents BVALID/BRESP. Newly accepted pieces cannot commit until the next
edge. Hold BVALID/BRESP unchanged until BREADY; no new AW/W is accepted while B
is pending, including its consumption edge. Read operates independently: ARREADY
is one iff no R response is pending. AR acceptance snapshots the addressed
PRE-EDGE register into RDATA/RRESP and asserts RVALID after the edge. Hold the
entire R response unchanged until RREADY, even while the timer or writes advance.
A pending response prevents a new request on that channel from being accepted
on its consumption edge. READY can return high after that edge, allowing a new
request at the next rising edge, with no additional cooldown cycle. For
continuous read requests with RREADY high, edge n accepts a request, edge n+1
consumes its response without accepting a request, and edge n+2 can accept the
next request. Inactive B/R response payloads are zero. This deterministic
buffering/latency completes the source's otherwise
unspecified timing; it does not require simultaneous AW and W.

**Register map and errors.** Five 32-bit read/write registers are at 0x00 control,
0x10 elapsed, 0x20 countdown, 0x24 IRQ mask, 0x28 IRQ threshold. Also expose the
source example's 0x0C as READ-ONLY done status (0 or 1); it was omitted from the
source table and is an explicit completion. Other addresses, including unaligned
ones, return SLVERR=2, with zero read data; writes have no register effect.
Writes to 0x0C also return SLVERR. Valid addresses return OKAY=0. For each asserted
WSTRB bit j<4 that exists on the bus, replace register bits [8j+7:8j]. A write
whose relevant strobes are all zero has no side effects, including no elapsed
clear. An invalid-address operation still receives a response.

**Physical timer and priorities.** At every edge use the old control bit 0.
When running and countdown>0, decrement it by one; when running and countdown=0,
increment elapsed modulo 2^32. When stopped both hold. Thus elapsed means running
time after completion, not time since reset; this resolves ambiguous source
wording. Counter zero saturates, and control does not automatically clear.
After the timer update, apply a committing write's strobes to the resulting
register value. A control write with a relevant strobe clears elapsed to zero.
New start/stop affects counting beginning on the next edge. A simultaneous read
sees the state BEFORE both this tick and write. axi_ap_done is the live condition
countdown==0, including reset; irq is live control[0] && mask[0] &&
(countdown==threshold), not a latched copy in a bus response.

Active-low asynchronous reset clears registers, channel buffers and responses.
It aborts unmatched AW/W and pending B/R; no stale operation reappears after reset.
With cleared state, AWREADY/WREADY/ARREADY and done are one; IRQ and valid flags
are zero. The source's 'reset when de-asserted' phrase is corrected to asserted.
No external input is consumed while reset is active.


## Reset Behavior

`axi_aresetn` is asynchronous, active low. Assertion immediately restores the initial state. Registers and pending channels zero; done and channel ready signals reflect idle state.
After reset release, resume the edge-by-edge behavior above.
