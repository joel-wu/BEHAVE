# cvdp_agentic_async_filo_0001

Preserve the source storage element and observe its settled digital response.

## Interface

### RTL interface

```systemverilog
module async_filo #(
    parameter int DATA_WIDTH=16,
    parameter int DEPTH=8
)(
    input logic w_clk_level, r_clk_level, w_reset_level, r_reset_level, push, pop,
    input logic [DATA_WIDTH-1:0] w_data,
    output logic [DATA_WIDTH-1:0] r_data,
    output wire r_empty, w_full,
    output logic r_busy, r_valid
);
```

### Behavior interface

```python
class Behavior:
    def __init__(self, DATA_WIDTH=16, DEPTH=8): ...
    def reset(self): ...
    def process(self, w_clk_level, r_clk_level, w_reset_level, r_reset_level, push, pop, w_data): ...
```

All pins are unsigned integers of the widths in the RTL interface. Each call applies one
input record, updates persistent state according to the rules below, and returns
one dictionary of all settled output pins. Repeated identical records are
separate observations but create no new signal transition. No automatic periodic
clock is inserted between calls. All input-level sequences are defined.
The returned dictionary has exactly the keys `r_data`, `r_empty`, `w_full`,
`r_busy`, and `r_valid`. `r_data` is a Python integer in [0, 2**DATA_WIDTH - 1];
the other values are integers 0 or 1. Return all five fields even with no clock
edge or no successful pop, not `None` or a list. `reset()` restores the fresh
state and initial clock levels and returns no response. Physical reset inputs
are applied through `process(...)` according to Reset Behavior below.

## Modules

- `async_filo`: the stateful digital circuit below.

## Functional Behavior

Implement a real LIFO stack with independent push and pop clock domains.
Preserve the source defaults DATA_WIDTH=16 and DEPTH=8; require DATA_WIDTH>=1
and DEPTH>=1, including non-power-of-two depths. The two physical clock pins
are w_clk_level and r_clk_level, supplied as actual levels. The RTL uses real
positive edges. Holds and changes of other inputs do not create clock edges.

### Ownership and explicit source completion

The source describes two FIFO-like pointers but never defines who owns a stack
top shared by pushes and pops. Its supplied test maintains its own occupancy
instead of checking DUT status, and never asserts that popped values match the
stack. There is no answer RTL. Independent increment/decrement counters cannot
implement the requested LIFO; the old converted task consequently never popped.

Use ONE authoritative occupancy n and memory, owned by w_clk_level. A push is
performed directly in that domain; a pop is requested by a round-trip toggle
mailbox from the read domain. This permits concurrent push/pop requests but
serializes mutations of the same top. Add r_busy and r_valid to expose the
previously unspecified pop latency. This is a documented source-inspired
completion, NOT immediate-pop or unchanged-source-test equivalence. No ready/
valid wrapper is placed around the whole module.

### Write-domain operations

The valid stack is mem[0:n], bottom to top. w_full is n==DEPTH. A push at a
rising w_clk_level is accepted exactly when push=1 and PRE-edge w_full=0.
At the same edge a synchronized pop may be serviced. If its PRE-edge n>0,
return mem[n-1] and remove it. Otherwise return an unsuccessful response.
Then apply any accepted push: store w_data at the new top and increment n.
Thus, with both operations at n>0, pop returns the OLD top before the push.
At n=0 a simultaneous push does not retroactively satisfy an empty pop.
At n=DEPTH a simultaneous pop does NOT allow a push which saw w_full=1.
Each edge therefore changes n by at most one. Data is exactly DATA_WIDTH bits.

### Pop request and completion

At a rising r_clk_level with PRE-edge r_busy=0, pop=1 toggles the request and
sets r_busy=1. It is a command even if r_empty=1: its success is decided at the
write-domain service edge. While busy, all additional pop levels are ignored.
A held pop can request another operation on the edge AFTER completion; the
completion edge itself never also accepts a new command.

On EVERY write edge, a two-register synchronizer shifts the request toggle.
The owner uses the OLD second stage; a value different from the completion
toggle causes exactly one service. It publishes the matching completion toggle
and a held {success,data} bundle. On EVERY read edge another two-register
synchronizer shifts the completion toggle. A busy reader whose OLD second
stage matches its request completes: r_busy clears, r_valid is 1 if successful,
and r_data becomes the returned word (zero for an empty pop). r_valid clears on
the next read edge; it and r_data hold while that clock stops. Request/response
bundles stay fixed for the round trip. A stopped owner cannot fabricate a pop.

### Status crossing and simultaneous clocks

After each write edge, form the occupancy Gray code g=n^(n>>1). Shift g through
two registers on EVERY read edge; r_empty is high exactly when the CURRENT
second stage is zero. It is a delayed occupancy indicator, not the authority
for a pending pop. w_full is the current owner-domain occupancy comparison.
One-bit toggle mailboxes carry requests/completions; Gray-coded occupancy
carries status. This replaces the source's inconsistent independent pointers.

Coincident rising edges use common PRE-edge crossing values: neither domain
can see the other domain's same-edge update immediately. The digital model
does not claim to simulate metastability or replace physical CDC constraints.

### Reset completion

Either active-high w_reset_level or r_reset_level resets BOTH domains asynchronously. This
explicitly replaces the source's incomplete independent synchronous reset rule:
resetting half a shared-stack mailbox would otherwise leave stale operations.
Reset discards the stack and pending pop, clears synchronizers/toggles/status
data, sets w_full=0,r_empty=1,r_busy=r_valid=0,r_data=0. RAM contents may remain
but are inaccessible until overwritten by new pushes. Fresh RAM is initialized
to zero for deterministic execution. Releasing reset while clocks are held high
does not create edges. No stale completion survives a reset.


## Reset Behavior

Either active-high source reset asynchronously resets both domains and discards the stack and in-flight command; see the disclosed source completion below. Clocks start low. No implicit between-record reset.
Do not reset state between ordinary observations. There is no mid-run implicit
reset, warm-up masking or discarded output prefix.
