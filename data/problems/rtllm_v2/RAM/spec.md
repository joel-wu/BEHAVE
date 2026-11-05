# RAM

A synchronous dual-port RAM with independent read and write enables and an asynchronous clear.

## Interface

### RTL interface

```systemverilog
module RAM #(parameter int WIDTH = 6, parameter int DEPTH = 8)(
    input logic clk, rst_n,
    input logic write_en,
    input logic [7:0] write_addr,
    input logic [WIDTH-1:0] write_data,
    input logic read_en,
    input logic [7:0] read_addr,
    output logic [WIDTH-1:0] read_data
);
```

### Behavior interface

```python
class RAM:
    def __init__(self, WIDTH=6, DEPTH=8): ...
    def reset(self): ...
    def process(self, write_en, write_addr, write_data, read_en, read_addr): ...
```

Each call returns all settled output pin values immediately BEFORE one rising
edge of `clk`, then advances that edge. Arguments and outputs are unsigned port bit patterns.
Every call represents a clock edge, including edges with both enables zero.
Reset is controlled separately and produces no logical records.

Return one `dict` with exactly `read_data`, a Python integer in
0..2^WIDTH-1. Arguments are integers in the ranges of their corresponding port
widths. Disabled or invalid reads still have complete pre-edge observations,
not `None` or an empty list. Their zero result becomes observable on the next
call, after the modeled edge. Memory and the registered read value persist
between calls; `reset()` clears both without returning a record.

## Modules

- `RAM`: the complete native module above.

## Functional Behavior

WIDTH is a positive integer (default 6); DEPTH is in 1..256 (default 8).
Store exactly DEPTH words of WIDTH bits. Address pins remain eight bits wide,
as in the native source interface; they are not shortened to log2(DEPTH).

On every rising edge, independently perform each enabled operation. A write
updates the addressed word. A read registers the PRE-WRITE value at read_addr;
simultaneous read/write to the same address returns the old word. A disabled
read registers zero, rather than holding the previous result. The registered
read output otherwise holds between edges. Ignore writes with write_addr >=
DEPTH; reads with read_addr >= DEPTH register zero. Never truncate an invalid
address into a valid one. Reset takes priority over both operations.

The original source contradicts itself: the leading requirement says eight
six-bit words, its array explanation uses 64 eight-bit words, and its RTL
allocates 12 eight-bit words but resets only eight. This explicit completion
uses the leading WIDTH/DEPTH requirement, clears every allocated word, and
defines invalid-address behavior. It retains the original two independent
enable/address ports, asynchronous reset, registered old-data read and
read-disable zeroing. No ready/valid wrapper or transaction-only clock is added.

## Reset Behavior

`rst_n` is asynchronous, active low. Assertion immediately restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
