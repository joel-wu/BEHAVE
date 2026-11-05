# Output-stationary 4x4 mesh logical step

Advance a four-by-four mesh of double-buffered multiply-accumulate cells by one
logical tick. A travels to the right; B, propagated accumulator values, validity
and metadata travel downward. State persists between accepted requests. This
is a transaction-stepped mesh specialization, not the native cycle interface
of a processor accelerator.

## Interface

### RTL interface

```systemverilog
module systolic4x4_os_step (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [63:0] in_a, in_b, in_d,
    input logic [3:0] in_lanes, in_propagate, in_last,
    input logic [7:0] in_ids,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_b, out_c,
    output logic [3:0] out_lanes, out_propagate, out_last,
    output logic [7:0] out_ids
);
```

`in_a`, `in_b`, `in_d`, `out_b` and `out_c` pack four unsigned 16-bit lanes,
lane j in bits 16*j+:16. A lanes select rows; B/D and all other lanes select
columns. `in_lanes` and `out_lanes` are per-column logical validity, separate
from the record's transport valid. IDs pack four unsigned two-bit values.
Propagate and last contain one bit per column.

### Behavior interface

```python
class Systolic4x4OS:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_a, in_b, in_d, in_lanes, in_propagate, in_last, in_ids): ...
```

Each `process` call represents one accepted input record and advances one
logical tick. Pass unsigned integers of the declared port widths. It always
returns one dictionary with exactly `out_b`, `out_c` (unsigned 64-bit packed
words), `out_lanes`, `out_propagate`, `out_last` (unsigned 4-bit masks), and
`out_ids` (an unsigned 8-bit packed word). Packing follows the RTL interface.
Even when all logical output lanes are invalid, return the full dictionary,
not `None` or a list; those invalid lanes have zero payload and metadata.
All mesh state persists between calls. Construction and `reset()` clear it
as specified below; `reset()` returns `None`. Physical idle and stall cycles
do not require calls.

## Modules

`systolic4x4_os_step` is the complete mesh and its elastic response buffer.

## Functional Behavior

Each cell (r,j), r,j=0..3, holds unsigned 16-bit A, B, D, C1, C2; one-bit
V (valid), P (propagate), L (last); and two-bit I (ID). All right-hand sides
below refer to the old state of the same logical tick. Define C=C1 if P=1,
otherwise C=C2. No shift or weight-stationary mode is supported.

1. Emit the bottom row (r=3): valid lane j returns B, C, P, I, L of that
   cell and sets out_lanes[j]=1. All fields of an invalid lane are zero.
2. In each cell with V=1, let Q=A*B. If P=0, set C1=(C1+Q) mod 65536
   and C2=D. If P=1, set C2=(C2+Q) mod 65536 and C1=D. If V=0,
   both accumulators hold their values.
3. Advance edge registers simultaneously. A receives input row r at j=0,
   otherwise the left neighbor's A, on every tick. At r=0, V/I/L receive the
   corresponding input lanes; lower rows receive the upper neighbor's V/I/L.
   Only when this incoming V is 1, update B/D/P: from input B/D/propagate at
   r=0, or from the upper neighbor's B/C/P below it. Otherwise B/D/P hold.

Thus an input lane's validity and metadata reach a returned bottom-row record
four logical ticks later. Alternating P selects which accumulator is emitted
and loaded, while the other accumulates products. Multiple skewed input waves
can accumulate a complete matrix product; cells do not act as independent MACs.

### Transport

A record transfers at a rising edge with valid && ready. Every accepted input
causes exactly one logical tick and one ordered response. Physical idle cycles
and output backpressure do not advance the mesh. Hold valid and payload stable
while stalled. Output payload is zero when transport out_valid is zero. No
specific physical latency is required, but logical-tick behavior is fixed.

## Reset Behavior

Synchronous active-high reset clears every mesh register and the pending
response, zeros outputs and restores in_ready=1. It cancels outstanding work.
Zero initialization is part of this finite task, including invalid pipeline
lanes and both accumulators.
