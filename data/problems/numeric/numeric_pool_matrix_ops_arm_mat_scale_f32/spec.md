# Matrix Scale FP32 4x4

This module performs one complete scaling transaction on a 4×4 matrix of IEEE-754 single-precision values stored in an external memory. For each accepted command, it reads 16 contiguous FP32 elements from a source base address, multiplies each element by one FP32 scalar using IEEE-754 single-precision multiplication with round-to-nearest-even, and writes the 16 results to 16 contiguous destination addresses. The transaction then produces exactly one completion status output. The algorithm is elementwise matrix scaling over a fixed-size 4×4 matrix; no partial outputs are emitted.

## Interface

### RTL interface

```systemverilog
module matrix_scale_fp32_4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_src_rows,
    input  logic [15:0]  in_src_cols,
    input  logic [15:0]  in_dst_rows,
    input  logic [15:0]  in_dst_cols,
    input  logic [15:0]  in_src_base_addr,
    input  logic [15:0]  in_dst_base_addr,
    input  logic [31:0]  in_scale,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [1:0]   out_status,

    output logic         data_req_valid,
    output logic         data_req_we,
    output logic [15:0]  data_req_addr,
    output logic [31:0]  data_req_wdata,
    input  logic         data_req_ready,
    input  logic         data_resp_valid,
    input  logic [31:0]  data_resp_rdata
);
```

The external memory port `data` is a single read-write 32-bit word-addressed memory with 16-bit addresses.

`out_status` encoding:
- `2'b00`: success
- `2'b01`: size mismatch
- `2'b10`: reserved, never produced
- `2'b11`: reserved, never produced

### Behavior interface

```python
class MatrixScaleFp324x4:
    def __init__(self, mem_init=None):
        pass

    def reset(self):
        pass

    def process(self, in_src_rows, in_src_cols, in_dst_rows, in_dst_cols,
                in_src_base_addr, in_dst_base_addr, in_scale):
        pass
```

`process(...)` returns exactly one dictionary per accepted input transaction:
- `{"out_status": 0}` for success
- `{"out_status": 1}` for size mismatch

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_status`: 2.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
`mem_init` is an optional initialization mapping of the form
`{"data": {address: word}}`, with addresses 0..65535 and unsigned 32-bit words.
Construction copies these cells into zero-filled default storage. It is not an
RTL parameter; the execution environment may instead bind the run's data image.

self.data is the read-write memory view of 65536 unsigned 32-bit words. Bind the
configured environment image before processing. `reset()` preserves its current
contents and binding, including completed writes; it does not reinstall the initial
image. A new independent run installs its own configured image. The completion
dictionary does not replace the required
destination writes. Memory-request pins are not return fields.

## Modules

- `matrix_scale_fp32_4x4`: top-level matrix-scaling kernel with one command input, one completion output, and one external memory port.

## Functional Behavior

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.

Each accepted input transaction describes one matrix-scaling operation.

The matrix shape is fixed by contract to 4 rows and 4 columns at both source and destination. The input dimension fields are checked exactly:
- success requires `in_src_rows == 4`, `in_src_cols == 4`, `in_dst_rows == 4`, and `in_dst_cols == 4`
- any other combination is a size mismatch

If the dimension check fails:
1. No memory reads occur.
2. No memory writes occur.
3. The module emits exactly one output transaction with `out_status = 1`.

If the dimension check passes:
1. Let `num_samples = 16`.
2. For each element index `i` from 0 through 15, in increasing order:
   - read one 32-bit FP32 value from memory address `in_src_base_addr + i`
   - compute `result_i = fp32_mul(src_i, in_scale)`
   - write `result_i` to memory address `in_dst_base_addr + i`
3. After all 16 writes complete, emit exactly one output transaction with `out_status = 0`.

Address arithmetic is unsigned 16-bit word addressing. For each access, the effective address is `(base + i) mod 2^16`.

The source and destination matrices are interpreted as contiguous row-major storage, but because the matrix is always 4×4 and stored contiguously, the transaction is fully defined by the linear element order `i = 0..15`.

Floating-point arithmetic uses IEEE-754 binary32 bit patterns on the ports and in memory. The per-element multiplication is `fp32_mul(src_i, in_scale)` with these required behaviors:
- rounding mode: round to nearest, ties to even
- NaN input on either operand produces a quiet NaN result as defined by `fp32_mul`
- `+Inf * 0` and `-Inf * 0`, in either operand order, produce NaN as defined by `fp32_mul`
- multiplication by ±Inf otherwise produces signed infinity as defined by `fp32_mul`
- subnormal inputs are handled exactly as `fp32_mul` defines
- overflow, underflow, signed zero, and normal finite results are handled exactly as `fp32_mul` defines

Read and write ordering is per element, in ascending `i`. For overlapping source and destination regions, each element uses the value read from address `in_src_base_addr + i` before the write to address `in_dst_base_addr + i` for that same `i`. Across different indices, the observable memory behavior is the sequential order `i = 0, 1, ..., 15`.

Output cardinality is exactly 1:1:
- every accepted input transaction produces exactly one output transaction
- there are no transactions that produce zero outputs
- there are no transactions that produce multiple output beats

## Reset Behavior

Reset is synchronous active-high.

Reset cancels computation and outstanding memory traffic. It does not clear
external memory or undo writes that completed before reset.

On reset:
- the module contains no pending command
- no memory request is pending
- the next accepted input starts a fresh transaction

Architectural state after reset:
- no in-flight source base address
- no in-flight destination base address
- no in-flight scale value
- no in-flight element index
- no latched read data
- no pending status beyond the idle default success code

Output/reset values:
- `in_ready = 1` when idle after reset
- `out_valid = 0`
- `out_status = 0`
- `data_req_valid = 0`
- `data_req_we = 0`
- `data_req_addr = 0`
- `data_req_wdata = 0`
