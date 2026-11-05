# FP32 sixteen-element minimum and first index

Return the first strict minimum of the sixteen raw binary32 inputs and its four-bit index. Initialize best=in_x0, index=0. Scan in_x1 through in_x15; replace best and index only when the new value is strictly less than best.

## Interface

### RTL interface

```systemverilog
module fp32_vector_min16 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,
    input  logic [31:0] in_x4,
    input  logic [31:0] in_x5,
    input  logic [31:0] in_x6,
    input  logic [31:0] in_x7,
    input  logic [31:0] in_x8,
    input  logic [31:0] in_x9,
    input  logic [31:0] in_x10,
    input  logic [31:0] in_x11,
    input  logic [31:0] in_x12,
    input  logic [31:0] in_x13,
    input  logic [31:0] in_x14,
    input  logic [31:0] in_x15,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_min,
    output logic [3:0]  out_index
);
```

### Behavior interface

```python
class Fp32VectorMin16:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_x0, in_x1, in_x2, in_x3,
            in_x4, in_x5, in_x6, in_x7,
            in_x8, in_x9, in_x10, in_x11,
            in_x12, in_x13, in_x14, in_x15):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_index`: 4.
- `out_min`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `fp32_vector_min16`: the complete task kernel.

## Functional Behavior

Return the first strict minimum of the sixteen raw binary32 inputs and its four-bit index. Initialize best=in_x0, index=0. Scan in_x1 through in_x15; replace best and index only when the new value is strictly less than best.

Comparison uses the IEEE numerical order, including subnormals; it does not flush inputs to zero. Signed zeros compare equal. Any comparison involving NaN is false, so an initial NaN remains the result, while later NaNs do not replace a finite best. Equal values retain the earlier index. Copy the selected bit pattern without canonicalizing NaNs or changing zero signs. One input produces one output record.

## Reset Behavior

Reset is active-high. It cancels pending work, clears output-valid and resets the output registers to zero. No pre-reset transaction may reappear. Inputs transfer only when in_valid and in_ready are both high; outputs transfer only when out_valid and out_ready are both high. A pending output remains valid with unchanged payload until accepted. The task does not prescribe internal latency or microarchitecture.
