# FILO_RTL

A parameterized first-in/last-out buffer with empty-stack simultaneous push/pop feedthrough.

## Interface

### RTL interface

```systemverilog
module FILO_RTL #(parameter int DATA_WIDTH = 8, parameter int FILO_DEPTH = 16) (
    input logic clk, reset, push, pop,
    input logic [DATA_WIDTH-1:0] data_in,
    output logic [DATA_WIDTH-1:0] data_out,
    output logic full, empty
);
```

### Behavior interface

```python
class FiloRtl:
    def __init__(self, DATA_WIDTH=8, FILO_DEPTH=16): ...
    def reset(self): ...
    def process(self, push, pop, data_in): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `data_out`: DATA_WIDTH.
- `empty`, `full`: 1.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL parameter specialization.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising
edge of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `FILO_RTL`: the complete native module above.

## Functional Behavior

`DATA_WIDTH >= 1` and `FILO_DEPTH >= 1`, including non-power-of-two depths.
Sample push/pop on every rising edge; no ready/valid or output backpressure
is present. full and empty continuously describe the CURRENT occupancy.
The registered data_out changes only on a successful pop, feedthrough or reset.

When empty and push=pop=1, register data_in into data_out and remain empty;
no word is stored. Otherwise, determine both admissions from pre-edge occupancy:
pop succeeds iff nonempty, and push succeeds iff not full. An admitted pop
returns the old top word. If both succeed, replace that word with data_in and
retain occupancy. At full, simultaneous push/pop performs ONLY the pop. With
only push or pop asserted, perform that admitted operation. Ignore overflow
pushes and underflow pops, retaining data_out. Idle cycles hold all state.

Reset defines data_out as zero, including before the first pop. All stored
words are unsigned DATA_WIDTH-bit values. Outputs after an edge reflect the
completed operation; the pre-edge observation precedes that operation.

## Reset Behavior

`reset` is asynchronous, active high. Assertion immediately restores the initial state. Occupancy, data_out and storage are zero; empty=1 and full=0.
After reset release, resume the edge-by-edge behavior above.
