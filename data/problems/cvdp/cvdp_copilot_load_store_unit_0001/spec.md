# load_store_unit

Native 32-bit load/store requests, byte enables and delayed writeback.

## Interface

### RTL interface

```systemverilog
module load_store_unit (
    input logic clk, rst_n, dmem_gnt_i, dmem_rvalid_i,
    input logic [31:0] dmem_rsp_rdata_i,
    input logic ex_if_req_i, ex_if_we_i,
    input logic [1:0] ex_if_type_i,
    input logic [31:0] ex_if_wdata_i, ex_if_addr_base_i, ex_if_addr_offset_i,
    output logic dmem_req_o,
    output logic [31:0] dmem_req_addr_o,
    output logic dmem_req_we_o,
    output logic [3:0] dmem_req_be_o,
    output logic [31:0] dmem_req_wdata_o,
    output logic ex_if_ready_o,
    output logic [31:0] wb_if_rdata_o,
    output logic wb_if_rvalid_o
);
```

### Behavior interface

```python
class LoadStoreUnit:
    def __init__(self): ...
    def reset(self): ...
    def process(self, dmem_gnt_i, dmem_rvalid_i, dmem_rsp_rdata_i, ex_if_req_i, ex_if_we_i, ex_if_type_i, ex_if_wdata_i, ex_if_addr_base_i, ex_if_addr_offset_i): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `dmem_req_addr_o`, `dmem_req_wdata_o`, `wb_if_rdata_o`: 32.
- `dmem_req_be_o`: 4.
- `dmem_req_o`, `dmem_req_we_o`, `ex_if_ready_o`, `wb_if_rvalid_o`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

This native pin-level API returns memory request address, write data, byte enables and
controls as ordinary output fields. It receives grants and read responses through the
listed process arguments; there is no hidden model memory or implicit request service.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `load_store_unit`: the complete native module above.

## Functional Behavior

This is the native single-outstanding LSU, not a private memory simulator.
The environment supplies memory grants and read responses through the declared
pins. Every requested address, write bit, byte enable and write-data bit is an
observable output, including stores that have no writeback response.

When idle, ex_if_ready_o=1. At an edge with ex_if_req_i && ex_if_ready_o,
compute address=(base+offset) modulo 2**32. type=0 allows any address and enables
1<<address[1:0]; type=1 requires address[0]=0 and enables 3<<address[1:0];
type=2 requires address[1:0]=0 and enables 15. type=3 and misalignment discard
the request without memory traffic, writeback or a busy interval.

An aligned accepted request is captured. In the following cycle, ready=0 and
dmem_req_o=1 with the COMPLETE 32-bit effective byte address, the captured write
bit and byte enables. For stores, data is the captured 32-bit ex_if_wdata_i;
the source does not prescribe shifting low lanes, so input data is already lane
positioned. For loads request write-data is zero. Keep every request field
unchanged until an edge with dmem_gnt_i=1. EX inputs while busy are ignored.

On that grant edge, clear request valid and all externally visible request
fields. A store is complete and ready returns high in the next cycle. A load
continues waiting with ready=0 until a LATER edge with dmem_rvalid_i=1. That
edge captures the entire dmem_rsp_rdata_i, without byte extraction, sign extension
or clearing unselected lanes, sets wb_if_rvalid_o for the following ONE cycle,
and returns ready high. wb_if_rdata_o then holds until the next load response
or reset. A new EX request can be captured while the previous WB pulse is high.

Source completions: unsolicited grant/response events and a response simultaneous
with its request grant are ignored; the original requires load data one or more
cycles after grant. Requests held across successive idle edges are new requests;
the legal environment only offers when ready. Reset aborts pending work and
clears request/WB pins immediately except ex_if_ready_o=1. No external memory
contents are reset. Storage is external to the unit. This native unit test compares bus
effects; it is not a proof of a separate memory-environment implementation.


## Reset Behavior

`rst_n` is asynchronous, active low. Assertion immediately restores the initial state. No pending request, zero writeback data/valid, and ex_if_ready_o=1.
After reset release, resume the edge-by-edge behavior above.
