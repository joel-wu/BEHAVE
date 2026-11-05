# HouseholderQColumnStep

HouseholderQColumnStep performs one reverse-order update step of the unblocked QR orthogonal-factor construction used by DORG2R. Each accepted input transaction identifies one reflector index `i`; the block reads the current matrix state and reflector scalar from external memories, applies the Householder reflector `H(i) = I - tau * v * v^T` from the left to the trailing submatrix `A(i:m-1, i+1:n-1)`, scales the reflector tail in column `i` by `-tau`, sets the diagonal entry to `1 - tau`, clears rows `0:i-1` of column `i`, and writes the updated matrix back to memory. Arithmetic is signed integer fixed-point with exact operation order matching the rank-1 update structure of the LAPACK step.

## Interface

### RTL interface

```systemverilog
module householder_q_column_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [3:0]   in_m,
    input  logic [3:0]   in_n,
    input  logic [2:0]   in_i,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_done,

    output logic         a_req_valid,
    output logic         a_req_we,
    output logic [5:0]   a_req_addr,
    output logic [31:0]  a_req_wdata,
    input  logic         a_req_ready,
    input  logic         a_resp_valid,
    input  logic [31:0]  a_resp_rdata,

    output logic         tau_req_valid,
    output logic [2:0]   tau_req_addr,
    input  logic         tau_req_ready,
    input  logic         tau_resp_valid,
    input  logic [31:0]  tau_resp_rdata
);
```

Memory `a` is a read-write matrix store containing an 8x8 row-major array of signed Q16.16 values. Address `r*8 + c` holds matrix element `A[r][c]`. Only rows `0..m-1` and columns `0..n-1` are part of the active matrix for a transaction.

Memory `tau` is a read-only vector store of length 8 containing signed Q16.16 values. Address `i` holds `tau[i]`.

### Behavior interface

```python
class HouseholderQColumnStep:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_m, in_n, in_i):
        pass
```

`process()` returns one output transaction with key `out_done`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_done`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

The model memory views are self.a (64 unsigned 32-bit words) and self.tau (8 unsigned
32-bit words). The environment binds the run images and preserves external memory
across reset. The out_done record does not replace required matrix writes; request
pins are not return fields.

## Modules

- `householder_q_column_step`: top-level kernel implementing one DORG2R reverse-loop iteration for a single reflector index.
- `a` external memory: 8x8 signed Q16.16 matrix backing store.
- `tau` external memory: 8-entry signed Q16.16 reflector-scalar backing store.

## Functional Behavior

All matrix indices in this specification are zero-based.

`in_m`, `in_n`, and `in_i` define one step of the reverse loop. The intended operating region is `1 <= in_m <= 8`, `1 <= in_n <= in_m`, and `0 <= in_i < in_n`. Results are defined for all input patterns (4-bit `in_m`/`in_n`, 3-bit `in_i`): if any of these inequalities is false, the matrix memory is left unchanged and one output transaction is still emitted with `out_done = 1`.

Each matrix element and each `tau` entry is a signed 32-bit two's-complement fixed-point value in Q16.16 format. A real value `x` is encoded as integer `round(x * 2^16)` outside the block; this block operates directly on the integer encoding.

Let `s32(x)` mean reduction modulo `2^32`, interpreted as signed two's-complement.
Every multiplication uses signed 32-bit operands and a signed 64-bit product:

- `mul_q16(x, y) = s32((s32(x) * s32(y)) >> 16)`.
- The shift is arithmetic, rounding negative non-integral results toward negative infinity.
- Every dot-product addition wraps immediately: `dot = s32(dot + term)`, starting at zero.
- Every subtraction and unary negation also wraps to 32 bits, including `-tau_i` before it enters a multiplication. Thus negating `0x80000000` gives `0x80000000`, not a positive 33-bit value.

Memory stores the resulting 32-bit bit pattern. These rules apply to intermediate
values as well as memory writes; there is no saturation or floating-point arithmetic.

For a valid transaction, the operation order is:

1. Read `tau_i = tau[in_i]`.

2. Form the reflector vector segment `v` of length `in_m - in_i` from column `in_i` of the current matrix:
   - `v[0] = 65536` (implicit Q16.16 unit entry; the stored diagonal is ignored)
   - for `r = in_i + 1 .. in_m - 1`, `v[r - in_i] = A[r][in_i]`

3. If `in_i < in_n - 1`, apply the Householder reflector to the trailing submatrix `A[in_i:in_m-1][in_i+1:in_n-1]` from the left. Let the trailing column index be `c`, where `c = in_i + 1 .. in_n - 1`. For each such column:
   1. Compute the inner product
      - `dot = Σ_{t=0}^{in_m-in_i-1} mul_q16(v[t], A[in_i + t][c])`
      using a left-fold accumulation in increasing `t`.
   2. Compute
      - `w = mul_q16(tau_i, dot)`
   3. Update the column in increasing row order:
      - `A[in_i + t][c] = A[in_i + t][c] - mul_q16(v[t], w)` for `t = 0 .. in_m-in_i-1`
   Each updated element is written back as a wrapped 32-bit signed Q16.16 value.

4. If `in_i < in_m - 1`, scale the strict-lower part of column `in_i` by `-tau_i`:
   - for `r = in_i + 1 .. in_m - 1`,
     `A[r][in_i] = mul_q16(A[r][in_i], -tau_i)`

5. Set the diagonal entry:
   - `A[in_i][in_i] = 0x00010000 - tau_i`
   where `0x00010000` is Q16.16 encoding of `1.0`.

6. Clear the upper part of column `in_i`:
   - for `r = 0 .. in_i - 1`, `A[r][in_i] = 0`

7. No other matrix elements are modified.

8. Emit one output transaction:
   - `out_done = 1`

The operation order within a transaction is exactly the sequence above. Step 3 reads the matrix values present before steps 4, 5, and 6 modify column `in_i`. Within step 3, each column update uses that column’s values as they stand when that column begins; columns are processed in increasing `c`.

Edge cases for valid transactions:
- If `in_i == in_n - 1`, step 3 is skipped because the trailing submatrix has zero columns.
- If `in_i == in_m - 1`, step 4 is skipped because the strict-lower part of column `in_i` is empty.
- If `tau_i == 0`, step 3 leaves every trailing column unchanged because `w = 0`; step 4 writes zeros into the strict-lower part of column `in_i`; step 5 writes `1.0` to the diagonal.
- Negative values use two's-complement representation throughout; arithmetic right shift preserves sign.

## Reset Behavior

Reset is synchronous active-high.

On reset:
- `out_valid` is deasserted.
- `out_done` is `0`.
- `in_ready` is asserted when the block is idle and deasserted while a transaction is in progress.
- `a_req_valid` is deasserted.
- `a_req_we` is `0`.
- `a_req_addr` is `0`.
- `a_req_wdata` is `0`.
- `tau_req_valid` is deasserted.
- `tau_req_addr` is `0`.

Architectural state after reset:
- No transaction is in progress.
- No pending output is buffered.
- The block holds no partial sums, no cached matrix elements, and no cached `tau` value.
- External memory contents `a` and `tau` are not modified by reset.
