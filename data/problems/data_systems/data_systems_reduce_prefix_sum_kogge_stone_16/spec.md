# InclusivePrefixSum16UInt32

Compute all sixteen inclusive prefix sums of one unsigned 32-bit vector, with modulo-2^32 arithmetic.

## Interface

### RTL interface

```systemverilog
module data_systems_reduce_prefix_sum_kogge_stone_16 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_x0,
    input logic [31:0] in_x1,
    input logic [31:0] in_x2,
    input logic [31:0] in_x3,
    input logic [31:0] in_x4,
    input logic [31:0] in_x5,
    input logic [31:0] in_x6,
    input logic [31:0] in_x7,
    input logic [31:0] in_x8,
    input logic [31:0] in_x9,
    input logic [31:0] in_x10,
    input logic [31:0] in_x11,
    input logic [31:0] in_x12,
    input logic [31:0] in_x13,
    input logic [31:0] in_x14,
    input logic [31:0] in_x15,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3,
    output logic [31:0] out_y4,
    output logic [31:0] out_y5,
    output logic [31:0] out_y6,
    output logic [31:0] out_y7,
    output logic [31:0] out_y8,
    output logic [31:0] out_y9,
    output logic [31:0] out_y10,
    output logic [31:0] out_y11,
    output logic [31:0] out_y12,
    output logic [31:0] out_y13,
    output logic [31:0] out_y14,
    output logic [31:0] out_y15
);
endmodule
```

Input and output transfers occur on rising clock edges when their respective
`valid` and `ready` signals are both high and reset is inactive. Each accepted
input produces exactly one output, in acceptance order. A stalled output must
retain its valid bit and payload until accepted or reset. Latency and internal
architecture are not prescribed. The input producer likewise holds a stalled
request stable.

### Behavior interface

```python
class data_systems_reduce_prefix_sum_kogge_stone_16:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7, in_x8, in_x9, in_x10, in_x11, in_x12, in_x13, in_x14, in_x15): ...
```

Each call represents one accepted input vector and returns one dictionary
with exactly the sixteen keys `out_y0`, `out_y1`, `out_y2`, `out_y3`, `out_y4`,
`out_y5`, `out_y6`, `out_y7`, `out_y8`, `out_y9`, `out_y10`, `out_y11`,
`out_y12`, `out_y13`, `out_y14` and `out_y15`. Each argument and returned value
is an unsigned 32-bit integer. Zero results still return the complete
dictionary, not `None` or a list. No handshake signals are returned. The model
has no state between vectors; construction and `reset()` establish this empty
state, and `reset()` returns `None`.

## Modules

`data_systems_reduce_prefix_sum_kogge_stone_16` is the sole top-level module.

## Functional Behavior

For every position `i` from zero through fifteen,
`out_yi = (in_x0 + ... + in_xi) mod 2^32`.
All input bit patterns are legal. Carry beyond bit 31 is discarded, not
saturated. Thus `out_y0 = in_x0`, and each later output includes its own input
and every preceding input exactly once.

A four-stage parallel-prefix network with distances 1, 2, 4, 8 is one realization;
the observable contract is the inclusive sum, not a required circuit topology.

## Reset Behavior

Reset is active-high and asynchronous. Reset clears `out_valid` and all output payloads to zero and makes
`in_ready` high. Any outstanding response is discarded. Requests are not
accepted during reset. The computation has no state between transactions.
