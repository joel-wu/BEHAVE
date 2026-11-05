# CsrMatvec4Lane

CsrMatvec4Lane computes one 4-lane sparse matrix/vector output row in compressed-sparse-row form, scoped to a fixed nonzero count of two entries per row (`NNZ_PER_ROW = 2`).  Each accepted input transaction provides a row index and signed scalar coefficients `alpha` and `beta`.  The kernel reads `row_start = row_ptr[r]` to locate the row's CSR entries, then processes the two entries at indices `k = row_start` and `k = row_start + 1`, forms four signed dot products between the sparse row and a packed 4-lane dense vector source, and returns the packed 4-lane result `alpha * dot + beta * b[row]` using two's-complement wraparound arithmetic.

The fixed `NNZ_PER_ROW = 2` scope keeps the kernel's transaction shape deterministic (a single setup access plus exactly two CSR-entry passes), so simulation cost and structural coverage are bounded — at the cost of not modelling rows with other nonzero counts.  Larger sparse matvec engines composed of multiple `CsrMatvec4Lane` instances accumulate over their respective 2-entry sub-rows.

## Interface

### RTL interface

```systemverilog
module csr_matvec4_lane (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_row,
    input  logic [15:0]  in_alpha,
    input  logic [15:0]  in_beta,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_y0,
    output logic [15:0]  out_y1,
    output logic [15:0]  out_y2,
    output logic [15:0]  out_y3,

    output logic         row_ptr_req_valid,
    output logic [8:0]   row_ptr_req_addr,
    input  logic         row_ptr_req_ready,
    input  logic         row_ptr_resp_valid,
    input  logic [8:0]   row_ptr_resp_rdata,

    output logic         col_idx_req_valid,
    output logic [9:0]   col_idx_req_addr,
    input  logic         col_idx_req_ready,
    input  logic         col_idx_resp_valid,
    input  logic [7:0]   col_idx_resp_rdata,

    output logic         a_val_req_valid,
    output logic [9:0]   a_val_req_addr,
    input  logic         a_val_req_ready,
    input  logic         a_val_resp_valid,
    input  logic [15:0]  a_val_resp_rdata,

    output logic         x_vec_req_valid,
    output logic [9:0]   x_vec_req_addr,
    input  logic         x_vec_req_ready,
    input  logic         x_vec_resp_valid,
    input  logic [63:0]  x_vec_resp_rdata,

    output logic         b_vec_req_valid,
    output logic [9:0]   b_vec_req_addr,
    input  logic         b_vec_req_ready,
    input  logic         b_vec_resp_valid,
    input  logic [63:0]  b_vec_resp_rdata
);
```

### Behavior interface

```python
class CsrMatvec4Lane:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_row, in_alpha, in_beta):
        pass
```

The return value of `process()` is one dictionary with keys `out_y0`, `out_y1`, `out_y2`, and `out_y3`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

External read-only memory views are self.row_ptr (512 unsigned 9-bit words),
self.col_idx (1024 unsigned 8-bit words), self.a_val (1024 unsigned 16-bit words), and
self.x_vec and self.b_vec (1024 unsigned 64-bit words each). Signed lanes use the
specified two's-complement encoding. The execution environment supplies these images
before processing; they are not additional process arguments or returned fields.
Construction creates zero-filled default storage. `reset()` preserves the current
contents and bindings of these external views. A new independent run installs
its configured images; a reset within that run does not reinstall them.

## Modules

- `csr_matvec4_lane`: top-level kernel that accepts one row request, reads CSR row bounds and row entries from external memories, computes four signed sparse dot products, adds the scaled packed `b` row, and emits one four-lane result.

## Functional Behavior

The kernel uses five external read-only memories:

- `row_ptr`: 512 entries of 9-bit unsigned values. `row_ptr[r]` is the first CSR entry index of row `r`, and `row_ptr[r+1]` is the exclusive end index.
- `col_idx`: 1024 entries of 8-bit unsigned values. `col_idx[k]` is the column index of CSR entry `k`.
- `a_val`: 1024 entries of 16-bit two's-complement signed values. `a_val[k]` is the coefficient of CSR entry `k`.
- `x_vec`: 1024 entries of 64-bit packed four-lane vectors. For address `c`, bits `[15:0]`, `[31:16]`, `[47:32]`, and `[63:48]` hold lanes 0, 1, 2, and 3 respectively. Each lane is interpreted as a signed 16-bit value.
- `b_vec`: 1024 entries of 64-bit packed four-lane vectors with the same lane packing and signed interpretation as `x_vec`.

Input interpretation:

- `in_row` is an unsigned 8-bit row index `r`.
- `in_alpha` is a signed 16-bit value `alpha`.
- `in_beta` is a signed 16-bit value `beta`.

For one accepted input transaction:

1. Compute `r = unsigned8(in_row)`, `alpha = signed16(in_alpha)`, and `beta = signed16(in_beta)`.
2. Read `row_start = unsigned9(row_ptr[r])`.  This is the only `row_ptr` access; `row_ptr[r + 1]` is *not* consulted because the nonzero count is fixed at 2.
3. Read `b_pack = unsigned64(b_vec[r])`.
4. Unpack:
   - `b0 = signed16(b_pack[15:0])`
   - `b1 = signed16(b_pack[31:16])`
   - `b2 = signed16(b_pack[47:32])`
   - `b3 = signed16(b_pack[63:48])`
5. Initialize signed 32-bit accumulators:
   - `sum0 = 0`
   - `sum1 = 0`
   - `sum2 = 0`
   - `sum3 = 0`
6. For each CSR entry index `k` in `{row_start, row_start + 1}` in that order:
   - Read `c = unsigned8(col_idx[k])`.
   - Read `a = signed16(a_val[k])`.
   - Read `x_pack = unsigned64(x_vec[c])`.
   - Unpack:
     - `x0 = signed16(x_pack[15:0])`
     - `x1 = signed16(x_pack[31:16])`
     - `x2 = signed16(x_pack[47:32])`
     - `x3 = signed16(x_pack[63:48])`
   - Update accumulators with 32-bit wraparound after each add:
     - `sum0 = signed32(unsigned32(sum0 + a * x0))`
     - `sum1 = signed32(unsigned32(sum1 + a * x1))`
     - `sum2 = signed32(unsigned32(sum2 + a * x2))`
     - `sum3 = signed32(unsigned32(sum3 + a * x3))`
7. Form final signed values:
   - `final0 = alpha * signed16(unsigned16(sum0)) + beta * b0`
   - `final1 = alpha * signed16(unsigned16(sum1)) + beta * b1`
   - `final2 = alpha * signed16(unsigned16(sum2)) + beta * b2`
   - `final3 = alpha * signed16(unsigned16(sum3)) + beta * b3`
8. Return:
   - `out_y0 = unsigned16(final0)`
   - `out_y1 = unsigned16(final1)`
   - `out_y2 = unsigned16(final2)`
   - `out_y3 = unsigned16(final3)`

Arithmetic rules:

- `unsigned8`, `unsigned9`, `unsigned16`, `unsigned32`, and `unsigned64` mean keeping the low 8, 9, 16, 32, or 64 bits.
- `signed16` and `signed32` mean interpreting the low 16 or 32 bits as two's-complement signed values.
- Multiplication is signed multiplication on the signed operands named above.
- No saturation is performed. Every truncation wraps modulo `2^16` or `2^32`.
- CSR accumulation order is strictly increasing `k` (so `row_start` is processed before `row_start + 1`).

Edge cases:

- `alpha == 0` is not a separate mode; the same formula applies and produces `unsigned16(beta * bL)` for lanes `L = 0..3`.
- The kernel does not validate CSR consistency.  In particular, `row_start + 1` is allowed to overflow 9 bits — the spec defines behavior solely in terms of the listed reads, unpacking, arithmetic, and truncation rules; the second CSR access is at the 10-bit address `unsigned10(unsigned9(row_start) + 1)`.

## Reset Behavior

Reset is synchronous active-high `rst`.

Reset cancels computation and outstanding memory traffic without changing the
externally supplied memory images.

Architectural state after reset:
- No input transaction is buffered.
- No output transaction is buffered.
- Internal registers for row index, row bounds, current CSR position, unpacked vector lanes, and accumulators are zero.

Output port values during reset and before any produced output:
- `in_ready = 1`
- `out_valid = 0`
- `out_y0 = 16'h0000`
- `out_y1 = 16'h0000`
- `out_y2 = 16'h0000`
- `out_y3 = 16'h0000`
- `row_ptr_req_valid = 0`
- `row_ptr_req_addr = 9'h000`
- `col_idx_req_valid = 0`
- `col_idx_req_addr = 10'h000`
- `a_val_req_valid = 0`
- `a_val_req_addr = 10'h000`
- `x_vec_req_valid = 0`
- `x_vec_req_addr = 10'h000`
- `b_vec_req_valid = 0`
- `b_vec_req_addr = 10'h000`
