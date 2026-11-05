# queue

queue with its native clocked interface.

## Interface

### RTL interface

```systemverilog
module queue #(
    parameter int DEPTH = 4, parameter int DBITS = 8,
    parameter int ALMOST_EMPTY_THRESHOLD = 1,
    parameter int ALMOST_FULL_THRESHOLD = 1
) (
    input logic clk_i, rst_ni, clr_i, ena_i, we_i, re_i,
    input logic [DBITS-1:0] d_i,
    output logic [DBITS-1:0] q_o,
    output logic empty_o, full_o, almost_empty_o, almost_full_o
);
```

### Behavior interface

```python
class Queue:
    def __init__(self, DEPTH=4, DBITS=8, ALMOST_EMPTY_THRESHOLD=1, ALMOST_FULL_THRESHOLD=1): ...
    def reset(self): ...
    def process(self, clr_i, ena_i, we_i, re_i, d_i): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `almost_empty_o`, `almost_full_o`, `empty_o`, `full_o`: 1.
- `q_o`: DBITS.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clk_i`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- `queue`: the complete native module above.

## Functional Behavior

`DEPTH>=1`, `DBITS>=1`; threshold parameters are nonnegative. Defaults
4,8,1,1 complete the source's unspecified defaults. The queue stores an ordered
sequence of up to DEPTH unsigned words. q_o continuously shows its oldest word,
or zero when empty. Status reflects the CURRENT occupancy, never the next
occupancy computed from live requests.

On each rising edge: clr_i clears storage and occupancy, even if ena_i=0.
Otherwise ena_i=0 holds all state. When enabled, remove the front if re_i=1 and
nonempty, then append d_i if we_i=1 and room remains. A read on empty does
nothing; a write on full without a simultaneous read does nothing. Full read+
write replaces the removed oldest word without changing occupancy. Empty read+
write retains the new word at index zero (occupancy becomes one). This explicitly
resolves the source's conflicting generic unchanged-pointer wording for its
empty special case. Implement the stated shift-register queue, not a transaction
wrapper: reads shift remaining entries toward index zero.

empty_o means count=0; full_o means count=DEPTH. almost_empty_o means
count<=min(DEPTH,ALMOST_EMPTY_THRESHOLD). almost_full_o means
count>=max(0,DEPTH-ALMOST_FULL_THRESHOLD). Oversized thresholds saturate; unsigned
subtraction must not wrap. Reset and clear make q_o=0, empty_o=1, full_o=0 and
derive the two threshold flags from count=0.

## Reset Behavior

`rst_ni` is asynchronous, active low. Assertion immediately clears stored words
and occupancy. `q_o = 0`, `empty_o = 1`, and `full_o = 0`. The `almost_empty_o`
and `almost_full_o` flags follow the stated threshold formulas at zero occupancy.
After reset release, resume the edge-by-edge behavior above.
