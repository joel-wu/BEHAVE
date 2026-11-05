# dma_xfer_engine

Register-programmed DMA with stalled native read/write bus, lane conversion and full addresses.

## Interface

### RTL interface

```systemverilog
module dma_xfer_engine #(
    parameter int DMA_B=0,
    parameter int DMA_HW=1,
    parameter int DMA_W=2
) (
    input logic clk, rstn,
    input logic [3:0] addr,
    input logic we,
    input logic [31:0] wd,
    input logic dma_req, bus_grant,
    input logic [31:0] rd_m,
    output logic [31:0] rd,
    output logic bus_req, bus_lock,
    output logic [31:0] addr_m,
    output logic we_m,
    output logic [31:0] wd_m,
    output logic [1:0] size_m
);
```

### Behavior interface

```python
class DmaTransferEngine:
    def __init__(self, DMA_B=0, DMA_HW=1, DMA_W=2): ...
    def reset(self): ...
    def process(self, addr, we, wd, dma_req, bus_grant, rd_m): ...
```

Keep one model instance across the sequence. Each call returns all settled
output pin values immediately BEFORE one rising edge of `clk`, then advances
that edge. Arguments are unsigned Python integers of the declared port widths.
The return is always one dictionary with exactly `rd`, `bus_req`, `bus_lock`,
`addr_m`, `we_m`, `wd_m`, and `size_m`: respectively 32, 1, 1, 32, 1, 32, and
2-bit unsigned Python integers. One-bit values are `0` or `1`.
Every call represents a clock edge, including idle and grant-stalled cycles;
even an all-zero observation is a full record, not `None` or an empty sequence.
`reset()` returns `None` and clears the state specified below. It does not
reset external memory or produce an output record.

## Modules

- `dma_xfer_engine`: the complete native module above.

## Functional Behavior

Implement a 32-bit-address, 32-bit-data memory-to-memory DMA using the
native CPU register interface and external master bus. No private RAM, address
modulo reduction, ready/valid wrapper, or final-status-only replacement is allowed.
DMA_B, DMA_HW and DMA_W are distinct two-bit codes for byte, halfword and word;
defaults are 0,1,2. The fourth code is reserved. Supported overrides permute these
three encodings without changing widths or transfer meaning.

**CPU registers.** Reads are combinational when we=0; when we=1, rd=0.
Addresses 0,4,8 select control, source byte address, destination byte address.
Other addresses read zero and ignore writes. The ten control bits are count[3:0]
(0..15 items), source-size[5:4], destination-size[7:6], increment-source[8],
increment-destination[9]. Writes to control ignore wd[31:10]. This layout explicitly
completes contradictory bit layouts in the supplied source tests; no line-enable
feature is defined. Configuration writes take effect only while idle. A CPU write
has priority over a coincident dma_req, including an unmapped write. Busy writes
and dma_req are ignored; reads still report the current register values.

**Start and bus phases.** A rising edge with idle, we=0 and dma_req=1 starts
the descriptor already held in the registers. Count zero, either reserved size,
or either address not aligned to its transfer size causes no operation. No error
port is invented. The request becomes visible after that edge; start itself
does not consume memory data. bus_req and bus_lock remain one until the last
write completes, including grant stalls. The first phase is a read request at
the current source address; the following phase is a write at the current
destination address. A bus operation completes exactly on an edge for which
bus_req && bus_grant is one. we_m=0 selects a read, we_m=1 a write. Grant loss
pauses either phase and leaves all request pins unchanged. A granted read is
followed by its write; that write is followed by the next read or idle. There is
no extra unobservable grant-acquisition transaction and no separate ready input.
This is the completion of the source's unspecified grant-loss/phase timing.

**Data and addresses.** addr_m is the full byte address, not an aligned-down
or truncated address. size_m carries the active source/destination size code.
For a read, rd_m is the little-endian word at addr_m & ~3; capture the byte,
halfword or word selected by addr_m[1:0], zero-extended into a 32-bit buffer.
For a write, take the low destination-size bytes of that buffer, shift them
into the lanes selected by the destination address, and drive all other wd_m
lanes zero. Thus widening zero-extends and narrowing truncates. The external
memory changes exactly those lanes on a granted write; it is not inside this
module. Increment the source register by its byte size after each completed
read iff bit 8 is set, and the destination after each completed write iff bit 9
is set. Address arithmetic wraps modulo 2^32. Decrement an internal remaining
count on completed writes; the programmed control register is unchanged.
The last write drops bus_req/bus_lock after its edge. A still-high dma_req can
start another descriptor on a later idle edge. Idle addr_m, we_m, wd_m, size_m
are zero; read-phase wd_m=0. These defined inactive values support full-pin checks.

Reset asynchronously clears registers, buffered data, remaining count and all
requests. A reset during either phase aborts the transfer; it does not perform
the pending write and does not erase external memory.


## Reset Behavior

`rstn` is asynchronous, active low. Assertion immediately restores the initial state. Control, addresses, remaining count and buffer are zero; the master is idle.
After reset release, resume the edge-by-edge behavior above.
