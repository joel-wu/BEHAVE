# BitonicSort16Int32

Sort one vector of sixteen signed 32-bit integers into nondecreasing order, preserving all values and their multiplicities.

## Interface

### RTL interface

```systemverilog
module data_systems_sort_rank_bitonic_merger_16_int32 (
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
    input  logic [31:0]  in_x8,
    input  logic [31:0]  in_x9,
    input  logic [31:0]  in_x10,
    input  logic [31:0]  in_x11,
    input  logic [31:0]  in_x12,
    input  logic [31:0]  in_x13,
    input  logic [31:0]  in_x14,
    input  logic [31:0]  in_x15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_x0,
    output logic [31:0]  out_x1,
    output logic [31:0]  out_x2,
    output logic [31:0]  out_x3,
    output logic [31:0]  out_x4,
    output logic [31:0]  out_x5,
    output logic [31:0]  out_x6,
    output logic [31:0]  out_x7,
    output logic [31:0]  out_x8,
    output logic [31:0]  out_x9,
    output logic [31:0]  out_x10,
    output logic [31:0]  out_x11,
    output logic [31:0]  out_x12,
    output logic [31:0]  out_x13,
    output logic [31:0]  out_x14,
    output logic [31:0]  out_x15
);
```

### Behavior interface

```python
class BitonicMerger16Int32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7, in_x8, in_x9, in_x10, in_x11, in_x12, in_x13, in_x14, in_x15): ...
```

Each `process` call represents one accepted vector. Pass all 16 arguments as
unsigned 32-bit integers encoding signed int32 values. It always returns one
dictionary with exactly `out_x0` through `out_x15`, each an unsigned 32-bit
encoding of the signed result. Dictionary fields, not a list, carry the
sorted positions; all fields remain present for zero or repeated values.
Never return `None`. There is no numerical state between requests.
Construction and `reset()` have no numerical effect; `reset()` returns `None`.
Physical idle, latency and stall cycles require no calls.

## Modules

- `data_systems_sort_rank_bitonic_merger_16_int32`: the complete operator and its input/output handshake.

## Functional Behavior

Interpret every lane as two's-complement int32. The output is the sorted permutation of all sixteen inputs, not a merge that assumes pre-sorted halves. Every adjacent pair is nondecreasing in signed order, and duplicate values retain their multiplicities. Sorting does not round, wrap, discard, or duplicate a lane.

An already sorted vector (0,1,...,15) remains unchanged. A reversed vector (15,14,...,0) becomes (0,1,...,15). Signed -2147483648 precedes every other value and 2147483647 follows every other value. The internal comparator topology and pipeline depth are unrestricted.

Input transfers occur on a rising clock edge when in_valid and in_ready are both 1; outputs transfer when out_valid and out_ready are both 1. The source holds every input field and in_valid until acceptance. The design holds every output field and out_valid until consumption. Each accepted input transaction produces exactly one output transaction, in input order. No fixed latency or internal pipeline is required. With reset inactive and the sink accepting outputs, pending work must eventually complete. Reset cancels pending work.

## Reset Behavior

Synchronous, active-high rst. Reset clears out_valid and visible output payloads to zero and restores an input-ready empty state. There is no retained computational state between requests.
