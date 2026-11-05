# Winograd F(2,3) Input Transform

Transform one prepared 4x4 signed spatial tile into the F(2,3) domain. All sixteen input samples contribute; this block does not multiply by a kernel.

## Interface

### RTL interface

```systemverilog
module ai_ml_vision_winograd_winograd_f23_input_xform (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic signed [15:0] in_d00,
    input  logic signed [15:0] in_d01,
    input  logic signed [15:0] in_d02,
    input  logic signed [15:0] in_d03,
    input  logic signed [15:0] in_d10,
    input  logic signed [15:0] in_d11,
    input  logic signed [15:0] in_d12,
    input  logic signed [15:0] in_d13,
    input  logic signed [15:0] in_d20,
    input  logic signed [15:0] in_d21,
    input  logic signed [15:0] in_d22,
    input  logic signed [15:0] in_d23,
    input  logic signed [15:0] in_d30,
    input  logic signed [15:0] in_d31,
    input  logic signed [15:0] in_d32,
    input  logic signed [15:0] in_d33,

    output logic out_valid,
    input  logic out_ready,
    output logic signed [19:0] out_t00,
    output logic signed [19:0] out_t01,
    output logic signed [19:0] out_t02,
    output logic signed [19:0] out_t03,
    output logic signed [19:0] out_t10,
    output logic signed [19:0] out_t11,
    output logic signed [19:0] out_t12,
    output logic signed [19:0] out_t13,
    output logic signed [19:0] out_t20,
    output logic signed [19:0] out_t21,
    output logic signed [19:0] out_t22,
    output logic signed [19:0] out_t23,
    output logic signed [19:0] out_t30,
    output logic signed [19:0] out_t31,
    output logic signed [19:0] out_t32,
    output logic signed [19:0] out_t33
);
```

### Behavior interface

```python
class WinogradF23InputXform:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_d00, in_d01, in_d02, in_d03,
        in_d10, in_d11, in_d12, in_d13,
        in_d20, in_d21, in_d22, in_d23,
        in_d30, in_d31, in_d32, in_d33,
    ): ...
```

The return value of `process(...)` is one dict with these keys:

`out_t00, out_t01, out_t02, out_t03, out_t10, out_t11, out_t12, out_t13, out_t20, out_t21, out_t22, out_t23, out_t30, out_t31, out_t32, out_t33`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_t00`, `out_t01`, `out_t02`, `out_t03`, `out_t10`, `out_t11`, `out_t12`,
  `out_t13`, `out_t20`, `out_t21`, `out_t22`, `out_t23`, `out_t30`, `out_t31`,
  `out_t32`, `out_t33`: 20.

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

Interpret each input's low 16 bits as signed two's-complement. Let d be the 4x4 matrix whose entry (i,j) is `in_dij`. Compute `T = B^T d B` exactly, where

```text
B^T = [[1,0,-1,0], [0,1,1,0], [0,-1,1,0], [0,1,0,-1]]
B   = transpose(B^T)
```

Return `out_tij = T[i,j] mod 2**20`. The mathematical results fit signed 18 bits and hence the declared signed 20-bit outputs; no saturation, rounding or intermediate overflow is allowed. Any equivalent computation order or architecture is permitted. No tile extraction, padding, convolution, bias or channel reduction is included.

## Reset Behavior

Asynchronous active-high `rst` clears pending output validity and all output payload fields to zero. While reset is asserted, no transaction is accepted or emitted. An unfinished pre-reset output is cancelled. `in_ready=1` after the reset state is established. There is no persistent algorithmic state; each post-reset input is independent of earlier transactions.
