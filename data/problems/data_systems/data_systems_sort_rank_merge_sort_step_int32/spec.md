# Argsort8Fp32

Return the ascending permutation of eight IEEE 754 binary32 values, with deterministic ordering for ties and NaNs.

## Interface

### RTL interface

```systemverilog
module data_systems_sort_rank_merge_sort_step_int32 (
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
    output logic out_valid,
    input logic out_ready,
    output logic [2:0] out_p0,
    output logic [2:0] out_p1,
    output logic [2:0] out_p2,
    output logic [2:0] out_p3,
    output logic [2:0] out_p4,
    output logic [2:0] out_p5,
    output logic [2:0] out_p6,
    output logic [2:0] out_p7
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
class Argsort8Fp32:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_x2, in_x3, in_x4, in_x5, in_x6, in_x7): ...
```

Arguments are unsigned 32-bit Python integers containing the FP32 encodings.
Each call represents one accepted input and returns one dictionary with
exactly `out_p0` through `out_p7`, each a Python integer index in `[0, 7]`.
Return the complete permutation even for identical inputs or all NaNs, not
`None` or a sequence of separate index records. Do not call for stalls or
latency. `reset()` returns `None` and restores the empty transaction state.

## Modules

`data_systems_sort_rank_merge_sort_step_int32` is the sole top-level module.

## Functional Behavior

Each `in_xi` is the raw IEEE 754 binary32 encoding of value `x[i]`.
Return a permutation `p[0] ... p[7]` of the indices0 through7, in fields
`out_p0 ... out_p7`, sorted by these rules:

- All non-NaN values precede all NaNs. Non-NaNs are ordered numerically,
  including subnormals and both infinities.
- Equal numerical values retain increasing original index. In particular,
  negative zero and positive zero compare equal.
- All NaNs compare equal for this ordering, regardless of sign, payload or
  signaling bit, and retain increasing original index.

A NaN has exponent bits 30:23 equal to 255 and nonzero fraction bits 22:0.
No floating-point arithmetic, rounding or exception output is performed.
Every 32-bit input pattern is legal; input values are not modified. Only the
complete permutation is returned, not sorted values or a pairwise comparison.

## Reset Behavior

Reset is active-high and synchronous to the rising clock edge. Reset clears `out_valid` and all output payloads to zero and makes
`in_ready` high. Any outstanding response is discarded. Requests are not
accepted during reset. The computation has no state between transactions.
