# MatrixMultiplyF32Cell

This module computes one FP32 output element of a matrix product using the standard row-by-column dot-product algorithm from a row-major `A` matrix and a row-major `B` matrix stored in external memories. Each accepted input transaction specifies matrix dimensions and one output coordinate `(row_idx, col_idx)`. The module validates the coordinate and inner-dimension match, reads the required operands from memory, performs the floating-point multiply-accumulate in strict left-fold order, and emits one result transaction containing a status code and the computed FP32 element.

## Interface

### RTL interface

```systemverilog
module matrix_multiply_f32_cell (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_num_rows_a,
    input  logic [7:0]   in_num_cols_a,
    input  logic [7:0]   in_num_rows_b,
    input  logic [7:0]   in_num_cols_b,
    input  logic [7:0]   in_row_idx,
    input  logic [7:0]   in_col_idx,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_status,
    output logic [31:0]  out_value,

    output logic         a_mem_req_valid,
    output logic [15:0]  a_mem_req_addr,
    input  logic         a_mem_req_ready,
    input  logic         a_mem_resp_valid,
    input  logic [31:0]  a_mem_resp_rdata,

    output logic         b_mem_req_valid,
    output logic [15:0]  b_mem_req_addr,
    input  logic         b_mem_req_ready,
    input  logic         b_mem_resp_valid,
    input  logic [31:0]  b_mem_resp_rdata
);
```

### Behavior interface

```python
class MatrixMultiplyF32Cell:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_num_rows_a, in_num_cols_a, in_num_rows_b, in_num_cols_b, in_row_idx, in_col_idx):
        pass
```

Call `process` once per accepted input. It returns one dictionary
`{"out_status": ..., "out_value": ...}`, including for invalid dimensions
or indices. Both fields are Python integers holding the specified port bit
patterns; `out_value` is a binary32 encoding, not a Python float.

The environment initializes the `a_mem` and `b_mem` Behavior views to the external memory images. Reset preserves these views.

## Modules

- `matrix_multiply_f32_cell`: top-level kernel that validates dimensions and indices, reads one row of `A` and one column of `B` from external memories, and computes one FP32 output element.

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

`a_mem` and `b_mem` are read-only external memories of 65536 words each. Each word is one raw IEEE-754 binary32 bit pattern.

The environment supplies their initial contents. Unspecified locations follow the shared runtime memory policy, not an implicit all-zero image. Resetting the computation preserves external memory contents.

Matrix `A` is stored in row-major order in `a_mem`. For an `in_num_rows_a x in_num_cols_a` matrix, the element at row `r` and column `k` is stored at:
- `a_mem[r * in_num_cols_a + k]`

Matrix `B` is stored in row-major order in `b_mem`. For an `in_num_rows_b x in_num_cols_b` matrix, the element at row `k` and column `c` is stored at:
- `b_mem[k * in_num_cols_b + c]`

A transaction is dimension-valid when:
- `in_num_cols_a == in_num_rows_b`

A transaction is coordinate-valid when:
- `in_row_idx < in_num_rows_a`
- `in_col_idx < in_num_cols_b`

If either the dimension-valid condition or either coordinate-valid condition is false, the module returns:
- `out_status = 32'h00000001`
- `out_value = 32'h00000000`

If all validity conditions are true, the module returns:
- `out_status = 32'h00000000`
- `out_value = result`

The result is the dot product of row `in_row_idx` of `A` with column `in_col_idx` of `B`, accumulated in strict scalar program order.

Let:
- `N = in_num_cols_a`
- `a_k = a_mem[in_row_idx * in_num_cols_a + k]`
- `b_k = b_mem[k * in_num_cols_b + in_col_idx]`
- `prod_k = fp32_mul(a_k, b_k)`

Then:
- if `N == 0`, `result = 32'h00000000`
- if `N > 0`, `result = prod_0` for `k = 0`, then for each `k = 1` to `N-1` in ascending order,
  `result = fp32_add(result, prod_k)`

This is a strict left fold:
- `result = (((prod_0 fp32_add prod_1) fp32_add prod_2) ... fp32_add prod_(N-1))`

The arithmetic contract is exactly the `fp_lib` contract:
- multiplication uses `fp32_mul`
- addition uses `fp32_add`

Edge-case handling is exactly the `fp_lib` behavior of those operators:
- NaN propagates through `fp32_mul` and `fp32_add`
- ±Inf are handled by `fp32_mul` and `fp32_add`
- ±0 are handled by `fp32_mul` and `fp32_add`
- subnormal input and underflow output handling follow `fp_lib`

For each valid transaction, the hardware issues exactly `N` reads to `a_mem` and exactly `N` reads to `b_mem`, in ascending `k` order. The observable result must match the arithmetic defined above regardless of memory backpressure or read-response latency.

The module produces exactly one output transaction for each accepted input transaction.

## Reset Behavior

Reset is asynchronous and active-high. On reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_status = 32'h00000000`
- `out_value = 32'h00000000`
- `a_mem_req_valid = 0`
- `a_mem_req_addr = 16'h0000`
- `b_mem_req_valid = 0`
- `b_mem_req_addr = 16'h0000`

Architectural state after reset:
- no input transaction is buffered
- no partial sum is buffered
- no saved matrix dimensions are buffered
- no saved row or column index is buffered
- no pending memory request or response bookkeeping is buffered
- `a_mem` and `b_mem` retain their pre-reset contents; reset does not reinitialize either memory

The external-memory environment cancels pending reads and suppresses responses
while reset is asserted. It accepts no new memory request during reset, so a
response from before reset cannot be consumed by a later transaction.
