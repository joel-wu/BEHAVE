# custom_byte_enable_ram

Pipeline dual-port byte-enabled memory operations, with per-byte Port A priority.

## Interface

### RTL interface

```systemverilog
module custom_byte_enable_ram #(
    parameter int XLEN = 32, parameter int LINES = 8192,
    localparam int ADDR_WIDTH = (LINES > 1 ? $clog2(LINES) : 1)
) (
    input logic clk, init_reset,
    input logic [ADDR_WIDTH-1:0] addr_a,
    input logic en_a,
    input logic [XLEN/8-1:0] be_a,
    input logic [XLEN-1:0] data_in_a,
    input logic [ADDR_WIDTH-1:0] addr_b,
    input logic en_b,
    input logic [XLEN/8-1:0] be_b,
    input logic [XLEN-1:0] data_in_b,
    output logic [XLEN-1:0] data_out_a, data_out_b
);
```

### Behavior interface

```python
class CustomByteEnableRam:
    def __init__(self, XLEN=32, LINES=8192): ...
    def reset(self): ...
    def process(self, addr_a, en_a, be_a, data_in_a, addr_b, en_b, be_b, data_in_b): ...
```

Each `process(...)` call returns exactly
`{"data_out_a": a_word, "data_out_b": b_word}` with the settled output values
immediately BEFORE one rising edge of `clk`, then advances that edge. Both
returned words are unsigned `XLEN`-bit integers. Arguments are unsigned port
bit patterns, with enables encoded as integers 0 or 1. Every call, including
an idle or write-only cycle, returns this dictionary, never `None` or a list.
Memory and request/output registers persist between calls. `reset()` restores
their stated initial values, returns `None` and emits no output record;
reset is controlled separately from the process arguments.

## Modules

- `custom_byte_enable_ram`: the complete native module above.

## Functional Behavior

`XLEN` is a positive multiple of 8 (default 32), `LINES >= 1`
(default 8192), and `ADDR_WIDTH=max(1,ceil(log2(LINES)))` is derived, not an
independent override. Memory consists of LINES unsigned XLEN-bit words. Byte lane
j is bits [8*j+7:8*j]. Retain the full default 8192 x 32 capacity.

Every released rising edge performs two stages concurrently:

1. Capture ALL eight input fields into the request registers, even if en=0.
2. Using the PREVIOUS request registers, read both addressed words into the
   output registers and apply byte writes to memory. Both reads see memory
   BEFORE either write at this edge (read-first completion). Reads occur even
   when their port's en=0. en controls writes only.

For a request captured at edge k, its read result becomes visible just after
edge k+1 and is therefore present immediately before edge k+2.
This is an input-register stage followed by a memory/read-register stage, not
a ready/valid transaction abstraction or an arbitrary-latency pipeline.

For each byte independently, a port writes only when en=1 and that byte-enable
bit is 1. At different addresses both writes occur independently. At the same
address, an enabled A byte wins; otherwise an enabled B byte writes; otherwise
the old byte is retained. A's priority does not suppress B's other bytes.
For example, writing A=0x00001234 with mask0011 and B=0xABCD0000 with mask1100
to a zero 32-bit word gives 0xABCD1234, while the simultaneous reads report zero.
The next read of that word reports the merged data. Zero masks never write.

### Initialization and underspecified cases

The source requires zero RAM at startup, but leaves pipeline startup and exact
read-during-write ordering implicit. This task uses read-first ordering above
and adds synchronous high `init_reset`: clear every RAM word, all request
registers and both output registers at that edge; discard any pending write.
Execution begins with this reset. This explicit reinitialization interface
replaces startup-only initialization; it does not add an 8192-cycle scrub delay.
For non-power-of-two LINES, an out-of-range address reads zero and its writes
are ignored, without affecting the other port. These are explicit completions;
the source did not supply answer RTL establishing those cases.

## Reset Behavior

`init_reset` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All stored state, including memory and pipeline registers when present, is zero.
After reset release, resume the edge-by-edge behavior above.
