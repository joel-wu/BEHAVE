# cvdp_agentic_dram_controller_0001

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module dramcntrl #(
    parameter int del_width=15,
    parameter int len_auto_ref=10,
    parameter int len_small=8,
    parameter int addr_bits_to_dram=13,
    parameter int addr_bits_from_up=24,
    parameter int ba_bits=2
)(
    input logic clk_in_level, reset_level,
    input logic [addr_bits_from_up-1:0] addr_from_up,
    input logic rd_n_from_up, wr_n_from_up, bus_term_from_up,
    output logic [addr_bits_to_dram-1:0] addr,
    output logic [ba_bits-1:0] ba,
    output wire clk,
    output logic cke,
    output wire cs_n, ras_n, cas_n, we_n,
    output wire [1:0] dqm,
    output logic dram_init_done,
    output wire dram_busy,
    output logic rd_dat_from_dram_ready
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self, del_width=15, len_auto_ref=10, len_small=8, addr_bits_to_dram=13, addr_bits_from_up=24, ba_bits=2): ...
    def reset(self): ...
    def process(self, clk_in_level, reset_level, addr_from_up, rd_n_from_up, wr_n_from_up, bus_term_from_up): ...
```

Constructor arguments match the RTL parameters; `del_width` avoids the Python
keyword `del` used for the source parameter. All arguments and results are
unsigned integers of the declared port widths. Each `process(...)` call applies
one input-level record and returns one dictionary containing exactly `addr`,
`ba`, `clk`, `cke`, `cs_n`, `ras_n`, `cas_n`, `we_n`, `dqm`, `dram_init_done`,
`dram_busy`, and `rd_dat_from_dram_ready`, after the resulting state update and
settling. Return the complete dictionary even without a clock edge or during
initialization; never suppress an observation as `None` or an empty list.
Keep the instance across observations. `reset()` returns `None` and establishes
the fresh state below, including previous clock level zero. In-stream reset is
instead represented by `reset_level` in ordinary input records.

### Input-level observations

Repeated identical input records are separate observations but create no new
signal transition. No automatic periodic clock is inserted between records.
All input-level sequences are defined.

## Modules

- `dramcntrl`: the stateful digital circuit below.

## Functional Behavior

Implement the source SDRAM command controller, not a data memory or a
transaction-gated command generator. The source clk_in/reset become
clk_in_level/reset_level so explicit signal levels are not inferred as an
implicit simulator clock/reset domain;
the RTL still uses its actual rising edge. clk mirrors that level continuously.
All other outputs hold between rising edges, including when reset changes.
No implicit clock, ready/valid wrapper or output backpressure is added.

### Parameters and address layout

The source delay-counter parameter del is spelled del_width in both interfaces.
Its default is restored to 15; len_auto_ref=10
and len_small=8 are also restored. Require del_width>=14, len_auto_ref>=1,
len_small>=4, addr_bits_to_dram>=12 and ba_bits>=1. Define
C=addr_bits_from_up-addr_bits_to_dram-ba_bits and require 1<=C<=10.
These are counter/address widths, NOT alternative timing constants.
The upstream address is the concatenation {bank, row, column}, with widths
ba_bits, addr_bits_to_dram and C. Default slices are [23:22], [21:9], [8:0].
The entire address and selected operation are captured only at ACTIVE;
changing the input address during an operation cannot redirect it.

### Commands and initialization

The six command bits are {cs_n,ras_n,cas_n,we_n,dqm[1:0]}:
INHIBIT=111111, NOP=011111, ACTIVE=001111, READ=010100,
WRITE=010000, BURST_TERMINATE=011011, PRECHARGE=001011,
AUTO_REFRESH=000111, LOAD_MODE=000011, and ongoing data=011100.
Address and bank hold unless a rule explicitly changes them.

After reset, number non-reset rising edges starting at 1. Edges 1..10000
issue NOP. PRECHARGE-all is issued at 10001 with addr=1024,ba=0;
AUTO_REFRESH at 10005 and 10013; LOAD_MODE at 10021 with addr=0x027,ba=0;
dram_init_done becomes 1 at 10024. Other initialization edges issue NOP.
This retains the source's 10000-edge power-up wait and command spacings
tRP=4, tRFC=8, tMRD=3, tRCD=2 (100 MHz: 100 us before precharge).
cke becomes 1 on the first non-reset rising edge and stays 1 until reset.

### Requests and burst termination

Each rising edge samples rd_n_from_up and wr_n_from_up. A new request is a
transition from both high to either low; if both go low, write wins. History
updates on EVERY edge, even during initialization, refresh or another access.
Requests while busy are not queued: return both high and retry after idle.
At an idle edge, pending refresh has priority; otherwise a new request with
bus_term_from_up=1 issues ACTIVE and captures the descriptor.

Two edges after ACTIVE issue READ or WRITE, with zero-extended column address
(A10=0) and the captured bank. Other pre-command edges issue NOP. Thereafter
issue ongoing-data NOP while the selected burst remains open. For a read,
rd_dat_from_dram_ready rises two edges after READ and remains high while that
burst is open; for a write it stays zero. This is a data-valid indication, not
a data bus: no read or write payload is part of this source controller.

Either both requests high or bus_term_from_up=0 requests termination. A request
seen after ACTIVE is sticky, even if withdrawn before tRCD. READ/WRITE still
issues at its specified edge; BURST_TERMINATE is issued on the first subsequent
edge with a latched/current termination. It clears read-ready. PRECHARGE of the
captured bank (addr=0,A10=0) follows one edge later, then four edges of recovery
before idle. New work can start on the following edge. Changes between read
and write while the combined request remains active do not change the operation.

### Refresh and busy

Starting on the edge after initialization completes, count ALL rising edges,
including access/recovery/refresh edges. Every 781 edges creates one refresh
obligation, matching the source delay_reg comparison against 780. Obligations
saturate at 2**len_auto_ref-1. An idle edge with an obligation issues AUTO_REFRESH;
eight edges later it finishes and retires one obligation. Requests do not
interrupt an open burst, so an indefinitely held burst can saturate the counter.
Completion at saturation still decrements; a simultaneous new obligation and
completion cancel before saturation. The next idle edge services any remainder.
All banks are already precharged at idle. dram_busy is high throughout startup,
access/recovery and refresh, or whenever an obligation remains; otherwise low.

### Explicit corrections to the incomplete source

The archived src/dramcntrl.sv is nonempty although its answer slot is empty.
It omits the requested read-ready output, ignores bus_term_from_up, can retain
row bits during column addressing, mixes reset styles, and makes a saturated
refresh counter permanently sticky. Its normal-access logic also depends on
live addresses and ambiguous request timing. This contract fixes those points
with the latched descriptor, timings, priorities and termination rules above.
bus_term is explicitly active LOW (the source TB keeps it high when inactive).
Read-ready is added as requested by the source prompt. These are documented
source-inspired corrections, not bit-for-bit equivalence to the defective RTL.
The original TB logs failures without necessarily failing and does not establish
all-pin command correctness. No physical SDRAM/analog timing claim is made.


## Reset Behavior

Active-high synchronous reset_level applies only on a rising clk_in_level. It clears all state, sets INHIBIT, addr=ba=0, cke=0, init_done=0, busy=1 and read-ready=0. Fresh execution starts in that same state with clock level zero; clk always mirrors its input.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
