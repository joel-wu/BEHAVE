# asyn_fifo

Implement a native dual-clock FIFO with registered read data.

## Interface

### RTL interface

```systemverilog
module asyn_fifo #(parameter int WIDTH=8, parameter int DEPTH=16)(
    input logic wclk_level, rclk_level, wrstn, rrstn, winc, rinc,
    input logic [WIDTH-1:0] wdata,
    output wire wfull, rempty, output wire [WIDTH-1:0] rdata
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self, WIDTH=8, DEPTH=16): ...
    def reset(self): ...
    def process(self, wclk_level, rclk_level, wrstn, rrstn, winc, rinc, wdata): ...
```

Constructor arguments match the RTL parameters. Pass the six control and
clock-level arguments as 0 or 1 and `wdata` as an unsigned `WIDTH`-bit integer.
Each `process` call applies all input levels together, updates persistent
state, and returns exactly `{"wfull": full, "rempty": empty, "rdata": data}`
AFTER settling. Both flags are 0/1 and data is an unsigned `WIDTH`-bit integer.
Repeated identical records still return the dictionary but create no new
edge; never return `None` or a list. No periodic clock is inserted between
calls. All input-level sequences are defined. Construction and `reset()`
start a fresh execution with zero pointers, synchronizer stages, RAM, read
data and previous clock levels; `reset()` returns `None`. This fresh-start
operation is distinct from the physical reset pins, which preserve RAM
and read data as specified below.

## Modules

- `asyn_fifo`: the stateful digital circuit below.

## Functional Behavior

Implement the source dual-clock FIFO with a separate dual_port_RAM
submodule, WIDTH-bit unsigned words (WIDTH>=1), and power-of-two DEPTH>=4,
default WIDTH=8 and DEPTH=16. The entire DEPTH is usable capacity. There are
no input/output transaction wrappers.

At each rising write-clock edge, if wrstn=1, winc=1 and the pre-edge wfull=0,
append wdata. Otherwise no write occurs. At each rising read-clock edge, if
rrstn=1, rinc=1 and pre-edge rempty=0, remove the oldest word and update rdata.
rdata is a registered output holding the last successful read, not a show-ahead
port. Blocked reads/writes leave the data and local position unchanged.

Each position has a binary address plus a wrap bit and its registered current
Gray image. Update the binary position and Gray register together on the same
local edge; do not transmit a combinational XOR of the binary counter across
domains.
Each domain samples the remote Gray position through two local-clock stages;
these stages progress on every local rising edge even when winc/rinc is zero
or a request is blocked. rempty compares the current local read Gray position
with the synchronized write position. wfull compares the current local write
Gray position with the synchronized read position after inverting its two
highest bits. Status outputs continuously reflect the settled local state.
A remote operation can therefore take two receiving edges to change a flag;
an operation on the edge that first changes the flag still uses its old value.
Coincident reads and writes read the pre-write memory cell.

wrstn clears only the write position and write-side remote-pointer stages;
rrstn clears only the corresponding read-side controls. Both are asynchronous,
active low, and inhibit their own memory operations while asserted. Neither
reset erases RAM or rdata. Initial pointers, stages, RAM and rdata are zero.
Asserting both resets flushes the logical queue; reset one side alone only
repositions that side and is not a promise to preserve queued-word ordering.
The pointer/flag/memory rules still define its digital observations.

### Source defect resolution

The source registers Gray(old binary) while independently incrementing binary,
so its local full/empty decisions lag by one edge and can admit an extra
write/read. Keep each published Gray image aligned with its CURRENT position.
The source also omits reset qualification on the RAM enables; add it so a
resetting side cannot perform an operation. Initial RAM/rdata zero is an
explicit completion, not source-guaranteed power-up state. The source contains
conflicting one-bit/two-bit full descriptions; use the two-highest-bit rule
required by its wrap-bit Gray architecture.

### Digital observation boundary

Clock pins are renamed with a _level suffix and supplied as physical levels.
The RTL still uses independent real rising-edge blocks. One input record
applies all pin levels together and observes all output pins after settling;
repeated levels do not create edges. Supply every transition of interest.
Coincident clock edges read the common pre-event state, including when another
domain resets on that observation. Each domain's own asserted reset dominates
its edge. There are no automatically inserted clocks, ready/valid wrappers or
output queues. Falling edges alone do not advance these circuits.

Both observed clock levels initially equal zero. The initial state is the
stated zero initialization, completing the source's unspecified startup. All
digital records have defined results. This does not assert analog CDC safety,
metastability resolution or physical setup/hold compliance for arbitrary inputs.


## Reset Behavior

Each active-low reset asynchronously clears only its own domain's control registers. Initial state is explicitly specified below. Physical resets are input pins, not implicit resets between samples.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
