# BitonicSort8Int32

Sort one vector of eight signed 32-bit integers into nondecreasing order. Every input value is preserved, including duplicates.

## Interface

### RTL interface

```systemverilog
module data_systems_sort_rank_bitonic_sort_8_int32 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_x0,
    input  logic [31:0]  in_x1,
    input  logic [31:0]  in_x2,
    input  logic [31:0]  in_x3,
    input  logic [31:0]  in_x4,
    input  logic [31:0]  in_x5,
    input  logic [31:0]  in_x6,
    input  logic [31:0]  in_x7,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_x0,
    output logic [31:0]  out_x1,
    output logic [31:0]  out_x2,
    output logic [31:0]  out_x3,
    output logic [31:0]  out_x4,
    output logic [31:0]  out_x5,
    output logic [31:0]  out_x6,
    output logic [31:0]  out_x7
);
```

### Behavior interface

```python
class BitonicSort8Int32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7): ...
```

Call `process(...)` once per accepted vector with eight unsigned 32-bit port
encodings. Every call returns one dictionary with exactly the eight keys
`out_x0` through `out_x7`, containing unsigned 32-bit encodings of the sorted
signed values. Zero and repeated values remain in the full dictionary; a list
or `None` is not a result for this task. The model retains no sorting state
between calls. Construction and `reset()` establish the same stateless starting
point; `reset()` returns `None` and emits no record.

## Modules

- `data_systems_sort_rank_bitonic_sort_8_int32`: the complete operator and its input/output handshake.

## Functional Behavior

Interpret every lane as two's-complement int32. For the accepted vector x, the output is the sorted permutation y: y[i] <= y[i+1], and every integer occurs equally often in x and y. The order is signed, not unsigned. There is no arithmetic overflow or saturation because sorting only moves values.

For example, (0,1,2,4,3,5,6,7) becomes (0,1,2,3,4,5,6,7). (-1,0,2147483647,-2147483648,0,1,-2,2) becomes (-2147483648,-2,-1,0,0,1,2,2147483647). The implementation may use any complete sorting network or equivalent architecture.

Input transfers occur on a rising clock edge when in_valid and in_ready are both 1; outputs transfer when out_valid and out_ready are both 1. The source holds every input field and in_valid until acceptance. The design holds every output field and out_valid until consumption. Each accepted input transaction produces exactly one output transaction, in input order. No fixed latency or internal pipeline is required. With reset inactive and the sink accepting outputs, pending work must eventually complete. Reset cancels pending work.

## Reset Behavior

Synchronous, active-high rst. Reset clears out_valid and visible output payloads to zero and restores an input-ready empty state. There is no retained computational state between requests.
