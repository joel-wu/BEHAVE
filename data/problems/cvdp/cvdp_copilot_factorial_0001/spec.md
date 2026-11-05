# factorial

Compute a five-bit operand factorial using a native iterative start/busy/done controller.

## Interface

### RTL interface

```systemverilog
module factorial (
    input logic clk, arst_n,
    input logic [4:0] num_in,
    input logic start,
    output logic busy, done,
    output logic [63:0] fact
);
```

### Behavior interface

```python
class Factorial:
    def __init__(self): ...
    def reset(self): ...
    def process(self, num_in, start): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `busy`, `done`: 1.
- `fact`: 64.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `factorial`: the complete native module above.

## Functional Behavior

In IDLE, an edge with `start=1` captures `num_in` (0..31), enters BUSY
and initializes an accumulator to one. In BUSY, multiply by the remaining
factor once per edge in descending order. After exactly n subsequent edges
for n>=1 (one for n=0), publish `n! mod 2**64`, enter DONE, deassert busy and
assert done. Thus 0!=1; the 64-bit residue explicitly defines overflow for n>20.
The DONE state lasts one cycle and then returns to IDLE. Starts in BUSY or
DONE are ignored; a new request may be captured on the next IDLE edge. Input
changes after capture do not affect the result. `fact` retains the previous
completed result until the next completion. The explicit iterative timing takes
precedence over the source's contradictory generic one-cycle sentence.

## Reset Behavior

`arst_n` is asynchronous, active low. Assertion immediately restores the initial state. Controller=IDLE; remaining factor, accumulator and result=0; busy=done=0.
After reset release, resume the edge-by-edge behavior above.
