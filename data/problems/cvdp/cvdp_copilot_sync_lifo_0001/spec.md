# sync_lifo

A parameterized synchronous last-in, first-out memory.

## Interface

### RTL interface

```systemverilog
module sync_lifo #(parameter int DATA_WIDTH = 8, parameter int ADDR_WIDTH = 3) (
    input logic clock, reset, write_en, read_en,
    input logic [DATA_WIDTH-1:0] data_in,
    output logic empty, full,
    output logic [DATA_WIDTH-1:0] data_out
);
```

### Behavior interface

```python
class SyncLifo:
    def __init__(self, DATA_WIDTH=8, ADDR_WIDTH=3): ...
    def reset(self): ...
    def process(self, write_en, read_en, data_in): ...
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
edge of `clock`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records. This observation convention does
not add a pipeline register or alter the native edge update.

## Modules

- `sync_lifo`: the complete native module above.

## Functional Behavior

`DATA_WIDTH >= 1`, `ADDR_WIDTH >= 1`, with depth `2**ADDR_WIDTH`.
On each rising edge, a read succeeds exactly when `read_en=1` and pre-edge
occupancy is nonzero; a write succeeds exactly when `write_en=1` and pre-edge
occupancy is less than depth. These decisions are independent and both use
the pre-edge flags. Apply a successful pop first, then a successful push.

A pop registers the old top entry in `data_out`. A push places `data_in` at
the new top. If both succeed, replace the top and preserve occupancy. When
both enables are high at full, only the pop succeeds; at empty, only the
push succeeds. An unsuccessful read holds `data_out`; an unsuccessful write
does not alter memory. `empty` and `full` reflect the current occupancy.
There is no handshake or extra read pipeline. The output from a successful
read appears after that edge and holds until another successful read or reset.

## Reset Behavior

`reset` is synchronous, active high. Only a rising edge of `clock` with reset asserted restores the initial state. Memory, occupancy and data_out are zero; empty=1 and full=0.
After reset release, resume the edge-by-edge behavior above.
