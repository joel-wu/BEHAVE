# Bitonic Sort U32 x 16

This block sorts sixteen unsigned 32-bit keys into ascending order using a bitonic compare-exchange network. One accepted input transaction produces exactly one output transaction.

## Interface

### RTL interface

```systemverilog
module bitonic_sort_u32x16 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_data_0,
    input  logic [31:0]  in_data_1,
    input  logic [31:0]  in_data_2,
    input  logic [31:0]  in_data_3,
    input  logic [31:0]  in_data_4,
    input  logic [31:0]  in_data_5,
    input  logic [31:0]  in_data_6,
    input  logic [31:0]  in_data_7,
    input  logic [31:0]  in_data_8,
    input  logic [31:0]  in_data_9,
    input  logic [31:0]  in_data_10,
    input  logic [31:0]  in_data_11,
    input  logic [31:0]  in_data_12,
    input  logic [31:0]  in_data_13,
    input  logic [31:0]  in_data_14,
    input  logic [31:0]  in_data_15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_data_0,
    output logic [31:0]  out_data_1,
    output logic [31:0]  out_data_2,
    output logic [31:0]  out_data_3,
    output logic [31:0]  out_data_4,
    output logic [31:0]  out_data_5,
    output logic [31:0]  out_data_6,
    output logic [31:0]  out_data_7,
    output logic [31:0]  out_data_8,
    output logic [31:0]  out_data_9,
    output logic [31:0]  out_data_10,
    output logic [31:0]  out_data_11,
    output logic [31:0]  out_data_12,
    output logic [31:0]  out_data_13,
    output logic [31:0]  out_data_14,
    output logic [31:0]  out_data_15
);
```

### Behavior interface

```python
class BitonicSortU32x16:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_data_0, in_data_1, in_data_2, in_data_3, in_data_4, in_data_5, in_data_6, in_data_7, in_data_8, in_data_9, in_data_10, in_data_11, in_data_12, in_data_13, in_data_14, in_data_15): ...
```

Each `process` call represents one accepted block and returns one dictionary
with exactly the keys `out_data_0` through `out_data_15`. All arguments and
results are unsigned 32-bit words. Zero and duplicate keys still produce all
sixteen fields, not a packed vector, list, or `None`. No numerical state
persists between blocks. Construction and `reset()` have no numerical effect;
`reset()` returns `None`. No calls are made for idle or output-stall cycles.

## Modules

- `bitonic_sort_u32x16`: top-level transaction operator.

## Functional Behavior

Transfers occur on rising edges with valid and ready both high. Output payloads remain valid and unchanged under backpressure. The block supports ordered responses and simultaneous output consumption and input acceptance.

All input patterns are legal unsigned integers. Emit the same multiset in nondecreasing order on `out_data_0..out_data_15`; duplicate multiplicities must be preserved. For example, `0x80000000` is greater than `0x7fffffff`. The task is not floating-point sorting.

The reference network uses levels 1..4. For each level, stage descends from level-1 to zero; lane i is paired with `i XOR (1<<stage)`. Compare each pair once, using ascending order when `i & (1<<level)` is zero and descending order otherwise. These ten stages produce the specified sixteen-lane ascending result; latency and internal register placement are unconstrained.

## Reset Behavior

Reset is synchronous active-high `rst`. It cancels any pending response, clears `out_valid` and all output payloads to zero. There is no retained algorithmic state. New requests can be accepted after reset is released.
