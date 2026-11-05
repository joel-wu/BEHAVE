# Bidirectional sixteen-element FP32 bitonic sorter

Return a permutation of the sixteen binary32 input words: ascending for in_dir=1, descending for in_dir=0. No arithmetic changes a payload word. Comparisons preserve subnormals, treat signed zeros as equal, and are unordered for NaNs.

## Interface

### RTL interface

```systemverilog
module bitonic_sort16_fp32 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_dir,
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
class BitonicSort16Fp32:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_dir,
            in_x0, in_x1, in_x2, in_x3,
            in_x4, in_x5, in_x6, in_x7,
            in_x8, in_x9, in_x10, in_x11,
            in_x12, in_x13, in_x14, in_x15):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_x0`, `out_x1`, `out_x10`, `out_x11`, `out_x12`, `out_x13`, `out_x14`,
  `out_x15`, `out_x2`, `out_x3`, `out_x4`, `out_x5`, `out_x6`, `out_x7`, `out_x8`,
  `out_x9`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `bitonic_sort16_fp32`: the complete task kernel.

## Functional Behavior

Return a permutation of the sixteen binary32 input words: ascending for in_dir=1, descending for in_dir=0. No arithmetic changes a payload word. Comparisons preserve subnormals, treat signed zeros as equal, and are unordered for NaNs.

For an exact tie/NaN contract, start a=in_x0..in_x15 and apply blocks of length n=2,4,8,16 in that order. Within each consecutive block at base b, first compare pairs (b+k,b+n-1-k) for k=0..n/2-1. Then, for stride=n/4,n/8,...,1, compare (b+j+k,b+j+stride+k), in increasing j=0,2*stride,...,n-2*stride and k=0..stride-1.

At every pair, swap only when left>right in ascending mode or left<right in descending mode. Equal and unordered pairs never swap. This defines the exact permutation for NaNs and duplicate words; it does not claim that a network is stable or that NaNs have a total order. For non-NaN inputs the output is numerically sorted in the requested direction. Other implementations are allowed if they produce the same records. One input produces one output.

## Reset Behavior

Reset is active-high. It cancels pending work, clears output-valid and resets the output registers to zero. No pre-reset transaction may reappear. Inputs transfer only when in_valid and in_ready are both high; outputs transfer only when out_valid and out_ready are both high. A pending output remains valid with unchanged payload until accepted. The task does not prescribe internal latency or microarchitecture.
