# MX Swizzled-Scale Geometry and Bounds

Compute the allocation geometry of an MX scale buffer and validate a supplied flat index. The corresponding byte is supplied by the caller; this block has no memory or swizzle datapath.

## Interface

### RTL interface

```systemverilog
module ai_ml_quant_mx_mx_e8m0_scale_extract (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_hp_data_M,
    input  logic [15:0] in_hp_data_K,
    input  logic [31:0] in_scale_index,
    input  logic [7:0]  in_scale_value,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_scale_rows,
    output logic [15:0] out_scale_cols,
    output logic        out_in_range,
    output logic [7:0]  out_scale_value
);
endmodule
```

### Behavior interface

```python
class MXE8M0ScaleExtract:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_hp_data_M, in_hp_data_K, in_scale_index, in_scale_value): ...
```

The `process()` return value is one dict with keys:
`out_scale_rows`, `out_scale_cols`, `out_in_range`, `out_scale_value`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_in_range`: 1.
- `out_scale_cols`, `out_scale_rows`: 16.
- `out_scale_value`: 8.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction Protocol

Transfers occur on rising clock edges with `valid && ready` and reset inactive. Each accepted input produces exactly one output, in acceptance order. The source holds its input record and valid until accepted; the implementation holds output valid and the complete payload while `out_ready=0`. Gaps, stalls and simultaneous input/output transfers do not change the result. Latency and internal topology are not prescribed.

## Modules

The declared top-level module implements this transaction transform.

## Functional Behavior

Inputs M=`in_hp_data_M` and K=`in_hp_data_K` are unsigned 16-bit high-precision dimensions, not scale-matrix dimensions. Return

```
out_scale_rows = 32 * ((M + 127) // 128)
out_scale_cols = 16 * ((K + 127) // 128)
count = out_scale_rows * out_scale_cols
out_in_range = int(in_scale_index < count)
out_scale_value = in_scale_value if out_in_range else 0
```

The index is unsigned 32-bit and the byte is unsigned 8-bit. Rows never exceed 16384, columns 8192 and count 134217728, so no wrap occurs. Zero in either dimension gives an empty buffer. Bounds refer to the padded allocation, not the unpadded logical scales; padding addresses are valid. Every byte pattern is transported unchanged, including E8M0 special encodings. No E8M0 decoding or assertion that the supplied byte matches external storage is performed.

## Reset Behavior

Asynchronous active-high `rst` clears pending output validity and all output payload fields to zero. While reset is asserted, no transaction is accepted or emitted. An unfinished pre-reset output is cancelled. `in_ready=1` after the reset state is established. There is no persistent algorithmic state; each post-reset input is independent of earlier transactions.
