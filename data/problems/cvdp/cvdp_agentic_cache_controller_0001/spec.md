# cache_controller

Native direct-mapped write-through cache with external memory timing and tag conflicts.

## Interface

### RTL interface

```systemverilog
module cache_controller (
    input logic clk, reset,
    input logic [9:0] address,
    input logic [31:0] write_data,
    input logic read, write,
    input logic [31:0] mem_read_data,
    input logic mem_ready,
    output logic [31:0] read_data,
    output logic hit, miss, mem_write,
    output logic [31:0] mem_address, mem_write_data
);
```

### Behavior interface

```python
class CacheController:
    def __init__(self): ...
    def reset(self): ...
    def process(self, address, write_data, read, write, mem_read_data, mem_ready): ...
```

Each call samples all settled output pins immediately BEFORE one rising edge
of `clk`, then applies that edge's cache update. Return exactly
`{"read_data": data, "hit": hit_bit, "miss": miss_bit, "mem_write": write_bit,
"mem_address": address_bits, "mem_write_data": write_data_bits}`.
Arguments and results are unsigned port encodings: `address` is 10 bits;
`write_data`, `mem_read_data`, `read_data`, `mem_address` and `mem_write_data`
are 32 bits; all other arguments and outputs are one bit.
Every call represents a clock edge, including idle and incomplete requests,
and returns the complete dictionary, never `None` or a list. Cache lines and
retained responses persist between calls. Construction and `reset()` restore
the state specified below. Reset is invoked separately, returns `None` and
produces no output record; external memory is not part of the model state.

## Modules

- `cache_controller`: the complete native module above.

## Functional Behavior

Implement 32 direct-mapped lines, each holding a 32-bit word, a 5-bit
tag and a valid bit. Address is a ten-bit WORD address: index=address[4:0],
tag=address[9:5]. This explicitly corrects the original testbench's five-bit
address, which cannot express both 32 indices and five tag bits. mem_address is
the zero-extension of this word address, not a shifted byte address. Main memory
is external: do not replace it with an internally initialized 1024-word RAM.

**Reads and hit/miss.** While read or write is asserted, hit is the valid-tag
comparison and miss is its complement. A read hit immediately exposes the cached
word, independent of mem_ready. A read miss drives its memory address; read_data
is mem_read_data while mem_ready=1, and zero while waiting. On a rising edge with
read && (hit || mem_ready), the read completes. A completed miss allocates exactly
that word/tag and sets valid; a hit leaves the cache unchanged.

**Writes.** While write=1 and read=0, drive mem_write=1, the requested address
and write_data on the memory bus. A write completes on a rising edge with
mem_ready=1. The external memory consumes that write on that edge; a hit updates
the cache on the SAME edge, and a miss leaves the cache unchanged (no allocation).
During a write read_data=0. While waiting for ready, neither cache nor memory
is updated. No write buffering or private memory mirror is implied.

**Interface timing completion.** The source has no CPU-ready or memory-read-
enable port. Each edge evaluates the current request; there is no hidden pending
request. To wait for a miss/write, the CPU holds its request stable until the
completion condition above. Withdrawing or changing an incomplete request
cancels/replaces it without modifying the cache. Holding a completed request
asserted performs it again on subsequent qualifying edges. Read takes priority
if both read and write are high; mem_write is then zero. The memory can present
the word selected by mem_address without a separate read-enable; read side
effects/MMIO are outside this ordinary-memory interface.

When neither request is asserted, retain read_data, hit and miss from the latest
COMPLETED operation, as required by the original cocotb assertions. During an
active incomplete request the live outputs above may differ from that retained
response. Reset synchronously invalidates every line and clears words, tags and
retained responses; it never resets external memory. Idle memory outputs are
zero. A read hit also drives all memory outputs zero; a read miss has mem_write
and mem_write_data zero. Combinational pins are not asynchronously reset-gated.
The source does not specify these inactive values or coincident-request timing;
they are explicit completions, not claims of an existing golden implementation.

Example from the source tests: read address 1 with mem_read_data=DEADBEEF and
ready=1, then write CAFEBABE to address 1 with ready=1, then read address 1.
The first response is a miss/DEADBEEF, the write a hit, and the final read is a
hit/CAFEBABE even if mem_ready=0. Idle cycles retain the corresponding response.


## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All lines invalid and all stored data/tags/responses zero; external memory is not reset.
After reset release, resume the edge-by-edge behavior above.
