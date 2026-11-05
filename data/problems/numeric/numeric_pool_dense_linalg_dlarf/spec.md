# HouseholderReflector4x4Step

HouseholderReflector4x4Step applies one real elementary reflector to a fixed-size 4×4 real matrix using the LAPACK DLARF algorithm restricted to a single 4-element reflector vector and a single 4×4 matrix transaction. The reflector is \(H = I - \tau v v^T\). One accepted input transaction provides the side selector, the scalar \(\tau\), the 4-element vector \(v\), and the 16 matrix elements of \(C\); one output transaction returns the 16 elements of the overwritten matrix. Arithmetic is signed two's-complement integer arithmetic on 32-bit elements with wraparound after every add, subtract, and multiply truncation. The algorithm preserves DLARF's trimming rules: it scans for the last non-zero element of \(v\), scans the active submatrix for the last non-zero row or column, forms the work vector over only that active region, and updates only that active region.

## Interface

### RTL interface

```systemverilog
module householder_reflector_4x4_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_side_left,
    input  logic [31:0]  in_tau,
    input  logic [31:0]  in_v0,
    input  logic [31:0]  in_v1,
    input  logic [31:0]  in_v2,
    input  logic [31:0]  in_v3,
    input  logic [31:0]  in_c00,
    input  logic [31:0]  in_c01,
    input  logic [31:0]  in_c02,
    input  logic [31:0]  in_c03,
    input  logic [31:0]  in_c10,
    input  logic [31:0]  in_c11,
    input  logic [31:0]  in_c12,
    input  logic [31:0]  in_c13,
    input  logic [31:0]  in_c20,
    input  logic [31:0]  in_c21,
    input  logic [31:0]  in_c22,
    input  logic [31:0]  in_c23,
    input  logic [31:0]  in_c30,
    input  logic [31:0]  in_c31,
    input  logic [31:0]  in_c32,
    input  logic [31:0]  in_c33,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_c00,
    output logic [31:0]  out_c01,
    output logic [31:0]  out_c02,
    output logic [31:0]  out_c03,
    output logic [31:0]  out_c10,
    output logic [31:0]  out_c11,
    output logic [31:0]  out_c12,
    output logic [31:0]  out_c13,
    output logic [31:0]  out_c20,
    output logic [31:0]  out_c21,
    output logic [31:0]  out_c22,
    output logic [31:0]  out_c23,
    output logic [31:0]  out_c30,
    output logic [31:0]  out_c31,
    output logic [31:0]  out_c32,
    output logic [31:0]  out_c33
);
```

### Behavior interface

```python
class HouseholderReflector4x4Step:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_side_left,
        in_tau,
        in_v0,
        in_v1,
        in_v2,
        in_v3,
        in_c00,
        in_c01,
        in_c02,
        in_c03,
        in_c10,
        in_c11,
        in_c12,
        in_c13,
        in_c20,
        in_c21,
        in_c22,
        in_c23,
        in_c30,
        in_c31,
        in_c32,
        in_c33,
    ):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_c00`, `out_c01`, `out_c02`, `out_c03`, `out_c10`, `out_c11`, `out_c12`,
  `out_c13`, `out_c20`, `out_c21`, `out_c22`, `out_c23`, `out_c30`, `out_c31`,
  `out_c32`, `out_c33`: 32.

Arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `householder_reflector_4x4_step`: top-level module that accepts one reflector-and-matrix transaction and produces one updated 4×4 matrix transaction.

## Functional Behavior

All matrix and vector elements are 32-bit signed integers encoded on ports as 32-bit unsigned bit patterns. `in_side_left = 1` selects left application \(H \cdot C\). `in_side_left = 0` selects right application \(C \cdot H\).

The input matrix is

\[
C =
\begin{bmatrix}
c_{00} & c_{01} & c_{02} & c_{03} \\
c_{10} & c_{11} & c_{12} & c_{13} \\
c_{20} & c_{21} & c_{22} & c_{23} \\
c_{30} & c_{31} & c_{32} & c_{33}
\end{bmatrix}
\]

with entries taken from `in_c00` through `in_c33`. The reflector vector is

\[
v = [v_0, v_1, v_2, v_3]^T
\]

with entries from `in_v0` through `in_v3`, and \(\tau\) is from `in_tau`.

Arithmetic rules:

- Each port value is interpreted as a signed 32-bit two's-complement integer.
- Every addition and subtraction is performed in signed integer arithmetic and truncated to 32 bits by discarding carries above bit 31.
- Every multiplication forms the mathematical signed product of two signed 32-bit integers and then truncates to the low 32 bits.
- Returned outputs are the 32-bit unsigned bit patterns of the final truncated results.
- Zero tests use signed integer equality to zero; only the exact bit pattern `32'h00000000` is zero.

The block always produces exactly one output transaction for each accepted input transaction.

The algorithm is:

1. Form local signed values `tau`, `v0..v3`, and `c00..c33` from the input bit patterns.

2. Determine `lastv`, the active length of `v`, by scanning backward from element 3 to element 0 and finding the highest index whose value is non-zero.
   - If `v3 != 0`, `lastv = 4`.
   - Else if `v2 != 0`, `lastv = 3`.
   - Else if `v1 != 0`, `lastv = 2`.
   - Else if `v0 != 0`, `lastv = 1`.
   - Else `lastv = 0`.

3. Determine `lastc`, the active extent of the matrix region coupled to `v`.
   - If `tau == 0` or `lastv == 0`, then `lastc = 0`.
   - Otherwise, if `in_side_left = 1`, `lastc` is the largest column count in the range 0..4 such that at least one element in rows `0` through `lastv-1` of that column is non-zero. Equivalently, scan columns 3 down to 0 and find the last column containing a non-zero in the active rows. If none, `lastc = 0`.
   - Otherwise, if `in_side_left = 0`, `lastc` is the largest row count in the range 0..4 such that at least one element in columns `0` through `lastv-1` of that row is non-zero. Equivalently, scan rows 3 down to 0 and find the last row containing a non-zero in the active columns. If none, `lastc = 0`.

4. Initialize the result matrix to the input matrix element-for-element. If `tau == 0` or `lastv == 0` or `lastc == 0`, no update is applied and the result remains equal to the input matrix.

5. If `in_side_left = 1` and `lastv > 0` and `lastc > 0`, form the work vector \(w\) of length `lastc` as
   \[
   w_j = \sum_{i=0}^{lastv-1} c_{ij} \cdot v_i
   \]
   for each column index \(j = 0 \ldots lastc-1\), with the sum evaluated as a left fold in increasing `i` order and truncated to 32 bits after each multiply and after each add.

6. If `in_side_left = 1`, update only rows `0..lastv-1` and columns `0..lastc-1` as
   \[
   c'_{ij} = c_{ij} - ((\tau \cdot v_i) \cdot w_j)
   \]
   for \(i = 0 \ldots lastv-1\), \(j = 0 \ldots lastc-1\).
   The product `tau * v_i` is truncated to 32 bits before multiplying by `w_j`. The subtraction is then truncated to 32 bits. Elements outside this active rectangle are unchanged.

7. If `in_side_left = 0` and `lastv > 0` and `lastc > 0`, form the work vector \(w\) of length `lastc` as
   \[
   w_i = \sum_{j=0}^{lastv-1} c_{ij} \cdot v_j
   \]
   for each row index \(i = 0 \ldots lastc-1\), with the sum evaluated as a left fold in increasing `j` order and truncated to 32 bits after each multiply and after each add.

8. If `in_side_left = 0`, update only rows `0..lastc-1` and columns `0..lastv-1` as
   \[
   c'_{ij} = c_{ij} - (w_i \cdot (\tau \cdot v_j))
   \]
   for \(i = 0 \ldots lastc-1\), \(j = 0 \ldots lastv-1\).
   The product `tau * v_j` is truncated to 32 bits before multiplying by `w_i`. The subtraction is then truncated to 32 bits. Elements outside this active rectangle are unchanged.

9. Output ports `out_c00` through `out_c33` carry the final matrix \(C'\) in row-major naming order.

Worked left-application example: if `in_side_left=1`, `tau=1`, `v=[1,0,0,0]`, and only `c00` and `c01` are non-zero, then `lastv=1`, `lastc=2`, `w=[c00,c01]`, and the only updated elements are `c00 := c00 - c00 = 0` and `c01 := c01 - c01 = 0`; all other matrix elements pass through unchanged.

Worked right-application example: if `in_side_left=0`, `tau=1`, `v=[1,0,0,0]`, and only `c00` and `c10` are non-zero, then `lastv=1`, `lastc=2`, `w=[c00,c10]`, and the only updated elements are `c00 := c00 - c00 = 0` and `c10 := c10 - c10 = 0`; all other matrix elements pass through unchanged.

## Reset Behavior

The module uses synchronous active-high reset `rst`.

Architectural state consists only of the registered output payload and any internal valid/busy state required by the 1:1 handshake implementation.

On a cycle with `rst = 1`:

- `out_valid` is deasserted.
- `in_ready` is asserted if implemented from the idle state.
- `out_c00` through `out_c33` are all set to `32'h00000000`.
- Any internal state tracking whether an output transaction is pending is reset to the idle value.
- No retained matrix, vector, work, or trimming state remains valid after reset.

No computational state persists between transactions.
