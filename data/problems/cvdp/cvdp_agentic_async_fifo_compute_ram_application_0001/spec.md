# cvdp_agentic_async_fifo_compute_ram_application_0001

Implement the source asynchronous FIFO with native independent clocks and status.

## Interface

### RTL interface

```systemverilog
module async_fifo #(
    parameter int p_data_width=32, parameter int p_addr_width=16
)(input logic i_wr_clk_level, i_rd_clk_level, i_wr_rst_n, i_rd_rst_n,
  input logic i_wr_en, i_rd_en, input logic [p_data_width-1:0] i_wr_data,
  output logic o_fifo_full, o_fifo_empty, output logic [p_data_width-1:0] o_rd_data);
```

### Behavior interface

```python
class Behavior:
    def __init__(self, p_data_width=32, p_addr_width=16): ...
    def reset(self): ...
    def process(self, i_wr_clk_level, i_rd_clk_level, i_wr_rst_n, i_rd_rst_n, i_wr_en, i_rd_en, i_wr_data): ...
```

All pins are unsigned integers of the widths in the RTL interface. Each call applies one
input record, updates persistent state according to the rules below, and returns
one dictionary of all settled output pins. Repeated identical records are
separate observations but create no new signal transition. No automatic periodic
clock is inserted between calls. All input-level sequences are defined.

The return value is one `dict` with exactly `o_fifo_full`, `o_fifo_empty`, and
`o_rd_data`. Flags are Python integers in 0..1; read data is an integer in
0..2^p_data_width-1. Return all fields for idle, blocked and reset observations,
not `None` or an empty list. `reset()` establishes a fresh execution, including
zero RAM, zero read data and low remembered clock levels. It is not equivalent
to asserting the physical reset pins mid-run, which retain RAM and read data.

## Modules

- `async_fifo`: the stateful digital circuit below.

## Functional Behavior

Implement a dual-clock FIFO with p_data_width-bit words and
2^p_addr_width usable entries. Preserve the source defaults 32 and 16 (65536
words); supported p_data_width is 1..64 and p_addr_width is 1..16. The source
title mentions a compute/RAM application, but the supplied specification and
test define only this asynchronous FIFO, not an additional compute operation.

The native write/read pins and status/data outputs are retained; only the two
clock names acquire a _level suffix. Supply their actual physical levels in
each input record. Each rising write edge accepts i_wr_data iff i_wr_rst_n=1,
i_wr_en=1 and pre-edge o_fifo_full=0. Each rising read edge dequeues the oldest
word iff i_rd_rst_n=1, i_rd_en=1 and pre-edge o_fifo_empty=0. Successful reads
update registered o_rd_data; idle or blocked reads hold it. Full writes cannot
overwrite memory. No added ready/valid or output queue is used.

Maintain one binary position including a wrap bit per side and publish its
CURRENT Gray-coded image in a register updated on the same local edge. Each
remote Gray pointer passes through two receiving-clock registers. They advance
on EVERY local rising edge, including when the request enable is zero or an
operation is blocked. Otherwise an initially empty reader could never observe
a write, which was the old task's deadlock.

Full and empty are registered local flags, as in the source's wptr_full and
rptr_empty modules. On a local edge, compute the next position using the OLD
local flag; then update the flag by comparing the next Gray pointer against
the OLD second synchronizer stage. Empty uses equality. Full uses equality
after inverting the remote Gray pointer's highest two bits (the wrap-bit Gray
rule). A new remote pointer therefore takes three receiving edges to affect
the flag: two synchronization stages and one status register. No pointer or
flag borrows a newer stage value on the same edge. All entries remain usable.

Independent active-low resets asynchronously clear the local binary/Gray
positions, local receiving stages and local status (full=0 or empty=1).
They inhibit that side's memory access but neither clears RAM or o_rd_data.
Fresh execution initializes RAM and o_rd_data to zero. A joint reset flushes
the logical queue; resetting only one side repositions it and does NOT promise
preservation of outstanding FIFO order. The stated pointer rules still define
the digital result, including any old words subsequently observed.

### Explicit source completions and observation

The top-level source says empty reads are blocked, but the standalone RAM
example omits the empty input. Follow the top-level requirement and gate the
RAM read along with pointer advancement. Reset qualification similarly applies
to memory accesses. Initial RAM/data values were unspecified and are completed
as zero. The vague full comparator is completed with the two-highest-bit Gray
rule, including the depth-two case without an invalid negative-width slice.
Registered status uses the old synchronized pointer, not a combinational flag.

The source TB samples some flags/data immediately at an edge and can omit the
last full/empty transition from its scoreboard. Its one-sided-reset assertions
also assume contents are preserved independently of pointer reset. Those are
not copied as architecture. Validate pre-edge acceptance and settled outputs
under the contract above instead; no unchanged-source-TB pass is claimed.

Each record applies all pins together then settles. Both clocks start low;
held levels create no edges. Coincident read/write edges see the common old
memory and pointers, so a same-cell read sees pre-write data. A reset dominates
its own edge; the other domain's simultaneous edge sees the old remote pointer.
This is digital event semantics, not proof of analog metastability behavior
or safe independent reset release. No answer RTL was supplied upstream.


## Reset Behavior

Independent active-low asynchronous resets clear local pointers, synchronizers and local status only. RAM and the read-data register retain their values. Fresh RAM/read data and pointers are zero, full=0 and empty=1.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
