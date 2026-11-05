# ArgMax16Int32

Select the largest of sixteen signed 32-bit integers and the first index at which it occurs.

## Interface

### RTL interface

```systemverilog
module data_systems_reduce_argmax_reduce_16_int32 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_data_0,
    input logic [31:0] in_data_1,
    input logic [31:0] in_data_2,
    input logic [31:0] in_data_3,
    input logic [31:0] in_data_4,
    input logic [31:0] in_data_5,
    input logic [31:0] in_data_6,
    input logic [31:0] in_data_7,
    input logic [31:0] in_data_8,
    input logic [31:0] in_data_9,
    input logic [31:0] in_data_10,
    input logic [31:0] in_data_11,
    input logic [31:0] in_data_12,
    input logic [31:0] in_data_13,
    input logic [31:0] in_data_14,
    input logic [31:0] in_data_15,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_value,
    output logic [3:0] out_index
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
class ReduceArgMaxReduce16Int32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_data_0, in_data_1, in_data_2, in_data_3, in_data_4, in_data_5, in_data_6, in_data_7, in_data_8, in_data_9, in_data_10, in_data_11, in_data_12, in_data_13, in_data_14, in_data_15): ...
```

Each `process(...)` call represents one accepted 16-element vector and returns
exactly `{"out_value": value_bits, "out_index": index}`. Arguments and
`value_bits` are unsigned 32-bit integers encoding signed values; `index` is
an integer from 0 through 15. Every vector returns one complete dictionary,
never `None` or a list. No state persists between vectors; `reset()` returns
`None` and emits no result.

## Modules

`data_systems_reduce_argmax_reduce_16_int32` is the sole top-level module.

## Functional Behavior

Interpret each `in_data_i` as a two's-complement signed integer `x[i]`.
Let `v = max(x[0], ..., x[15])` and let `p` be the smallest index satisfying
`x[p] = v`. Return `out_value = v mod 2^32` and `out_index = p`.

All 32-bit patterns are legal. Ties always select the lowest index, including
the all-`0x80000000` vector, which returns that value and index zero. The output
value is an input bit pattern; no saturation or arithmetic rounding occurs.

## Reset Behavior

Reset is active-high and synchronous to the rising clock edge. Reset clears `out_valid` and all output payloads to zero and makes
`in_ready` high. Any outstanding response is discarded. Requests are not
accepted during reset. The computation has no state between transactions.
