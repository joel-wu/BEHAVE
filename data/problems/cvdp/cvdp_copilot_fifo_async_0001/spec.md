# fifo_async

Dual-clock, Gray-pointer FIFO with independent write/read enables and visible
full, empty and first-word-fall-through outputs.

## Interface

### RTL interface

```systemverilog
module fifo_async #(
    parameter int DATA_WIDTH = 32,
    parameter int DEPTH = 16
) (
    input logic w_clk, w_rst, w_inc,
    input logic [DATA_WIDTH-1:0] w_data,
    output logic w_full,
    input logic r_clk, r_rst, r_inc,
    output logic r_empty,
    output logic [DATA_WIDTH-1:0] r_data
);
```

### Behavior interface

```python
class FifoAsync:
    def __init__(self, DATA_WIDTH=32, DEPTH=16): ...
    def reset(self): ...
    def reset_w(self): ...
    def reset_r(self): ...
    def process_w(self, w_inc, w_data): ...
    def process_r(self, r_inc): ...
```

Arguments and returned fields are Python integers holding unsigned port bit
patterns. Invoke each handler at every non-reset rising edge of its domain:

- `process_w` returns one dictionary `{"w_full": ...}`.
- `process_r` returns one dictionary `{"r_empty": ..., "r_data": ...}`.

These values are sampled immediately before that edge's updates. Each handler
returns a record even when its enable is zero or its transfer is blocked,
not `None`. Coincident handlers operate on independent copies of the same pre-edge
state and commit disjoint domain-owned writes atomically. This does not serialize
the two clocks or add a cycle to the RTL.

`reset_w` and `reset_r` implement the domain reset transitions specified below.
`reset()` initializes both domains and memory to zero. Reset methods produce
no output records.

## Modules

- `fifo_async`: the complete parameterized FIFO, memory and two synchronizers.

## Functional Behavior

`DATA_WIDTH >= 1`; `DEPTH` is a power of two at least 2. Defaults are
32 and 16. All data patterns and all enable combinations are legal. There is no
ready/valid protocol. Sample `w_inc,w_data` on every rising `w_clk` edge and
`r_inc` on every rising `r_clk` edge, including edges with enable zero.

The write and read positions W,R each range over 0 through 2*DEPTH-1, wrapping
modulo 2*DEPTH. Their memory addresses are W modulo DEPTH and R modulo DEPTH.
Define G(p)=p XOR (p>>1). Each domain has a two-register synchronizer for the
OTHER domain's Gray position. On every non-reset write edge, `(rq1,rq2)` becomes
`(G(R),old rq1)`; on every non-reset read edge, `(wq1,wq2)` becomes
`(G(W),old wq1)`. Synchronizers advance even with `w_inc=0` or `r_inc=0`.

At all times, `r_empty = (G(R)==wq2)` and
`w_full = (G(W)==(rq2 XOR (DEPTH+DEPTH/2)))`. In Gray representation the TWO
highest pointer bits are inverted for full detection. The source's one-MSB
rule is the equivalent binary-pointer rule, not a correct Gray comparison.
Flags describe current synchronized occupancy: no lookahead may reduce usable
capacity to DEPTH-1. Flags can remain conservative while a remote change crosses
the two synchronizer stages.

On a non-reset write edge, write `w_data` to memory[W modulo DEPTH] and
increment W exactly when `w_inc && !w_full`. A full write is ignored. On a
non-reset read edge, increment R exactly when `r_inc && !r_empty`; an empty read
is ignored. Each decision uses the flags BEFORE that edge. Coincident edges
read one common pre-edge state, then commit both updates; neither domain sees
the other domain's new pointer early.

`r_data` is a first-word-fall-through, combinational output: zero while empty,
otherwise memory[R modulo DEPTH]. Reading the current head does NOT require
`r_inc`; asserting `r_inc` consumes it at the next read edge. This follows the
source TB, which checks the head before asserting read increment. Keep the
head stable while it is unconsumed, except for explicit reset effects. The
entire DEPTH entries are usable. After a joint reset, blocked operations,
clock-ratio changes and pointer wrap must not lose, duplicate or reorder words.

### Source completions

The source supplies a prompt and TB but no answer RTL. It does not specify reset
edge sensitivity, defaults, empty data, or memory initialization. This task fixes
synchronous active-high resets, the defaults above, zero empty data, and zero
RAM on a write-domain reset. These are explicit choices, not claims about an
unavailable original implementation. There is no analog metastability model;
the task is the digital two-stage sampling model under the supplied clocks.
Independent reset may discard or expose old queue contents; the equations above
still define every result, but FIFO order across a one-sided reset is not promised.
Reset both domains before starting a fresh FIFO sequence.

## Reset Behavior

`w_rst` and `r_rst` are synchronous active-high and affect only their respective
rising clock edges. Write reset clears W, rq1, rq2 and the entire memory; read
reset clears R, wq1 and wq2. Reset takes priority over enable. Neither reset
directly clears the other domain's registers. A reset edge and a coincident
ordinary edge both use the pre-edge state before committing their updates.
Initially `w_full=0`, `r_empty=1`, `r_data=0`.
Outputs are combinational functions of state, not asynchronously reset pins.
