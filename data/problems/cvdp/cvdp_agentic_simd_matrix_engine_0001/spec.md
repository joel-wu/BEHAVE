# simd_matrix_engine_top

A native start/done matrix engine with row-chunk SIMD lanes.

## Interface

### RTL interface

```systemverilog
module simd_matrix_engine_top #(
    parameter int N = 8, parameter int SIMD_WIDTH = 4, parameter int DATA_WIDTH = 16
) (
    input logic clk, rst, start,
    input logic [2:0] op_select,
    input logic [N*N*DATA_WIDTH-1:0] mat_a_flat, mat_b_flat,
    output logic [N*N*DATA_WIDTH-1:0] mat_result_flat,
    output logic done
);
```

### Behavior interface

```python
class SimdMatrixEngineTop:
    def __init__(self, N=8, SIMD_WIDTH=4, DATA_WIDTH=16): ...
    def reset(self): ...
    def process(self, start, op_select, mat_a_flat, mat_b_flat): ...
```

Each call observes the pre-edge pins and then advances the state.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `done`: 1.
- `mat_result_flat`: N*N*DATA_WIDTH.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor settings must match the RTL specialization and coefficient mapping.

### Clock-edge observations

Observe all settled output pins immediately before each non-reset rising edge
of `clk`, then advance that edge. Idle cycles are included.
Reset slots produce no logical records.

## Modules

- simd_matrix_engine_top: row/chunk control and output state.
- simd_datapath: parallel lane packing and readiness aggregation.
- simd_lane: combinational modular arithmetic; disabled lanes cannot block completion.

## Functional Behavior

N, SIMD_WIDTH and DATA_WIDTH are positive integers, defaulting to 8,4,16.
Retain row-major packing: element (r,c) occupies
[(r*N+c)*DATA_WIDTH +: DATA_WIDTH]. This is elementwise ADD/SUB/MUL, NOT
matrix multiplication. Arithmetic wraps modulo 2**DATA_WIDTH; op_select=0/1/2
selects add/subtract/multiply, and 3..7 produces zero.

IDLE accepts start and initializes traversal at row=0,col=0. Each row uses
ceil(N/SIMD_WIDTH) chunks. A COMPUTE edge captures the LIVE matrix operands for
the current chunk. The following STORE edge writes those captured elements,
using the LIVE op_select at that STORE edge. This preserves the supplied RTL's
sampling times; the source does not latch entire matrices/opcode at start.
For one consistent operation, hold operands and opcode stable until done;
their behavior when changed is nevertheless defined by these sampling rules.
Ignore start in COMPUTE, STORE and DONE. After the final STORE, the next edge
raises done for one cycle and returns to IDLE. From acceptance at e, completion
is e + 2*N*ceil(N/SIMD_WIDTH) + 1. A new operation can start at the next edge.

STORE updates only the current chunk; other output elements retain their prior
values, including across idle and operation restart. Reset clears all elements.
Explicit source fixes/completions: synchronous reset follows the prompt (the
supplied RTL used asynchronous reset and omitted output initialization);
partial chunks stop at each row boundary, so N need not divide SIMD_WIDTH and
SIMD_WIDTH may exceed N. Unused lanes output zero and ready=1, following the
top-level/datapath requirement over the contradictory lane prose. All enabled
lanes are combinational; ready aggregation cannot deadlock a partial chunk.

## Reset Behavior

`rst` is synchronous, active high. Only a rising edge of `clk` with reset asserted restores the initial state. All history, counters and output state are zero.
After reset release, resume the edge-by-edge behavior above.
