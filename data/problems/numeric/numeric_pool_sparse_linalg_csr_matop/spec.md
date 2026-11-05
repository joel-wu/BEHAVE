# Complete four-plus-four CSR row addition

Compute one row of alpha*A + beta*B from two four-entry lane bundles. The row label is opaque eight-bit metadata and is copied to out_row. Coefficients and values are signed 32-bit integers; arithmetic wraps modulo 2^32. Column identifiers are unsigned 16-bit integers.

## Interface

### RTL interface

```systemverilog
module csr_matrix_add_row_merge (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_row,
    input  logic [31:0]  in_alpha,
    input  logic [31:0]  in_beta,

    input  logic [2:0]   in_a_row_start,
    input  logic [2:0]   in_a_row_end,
    input  logic [15:0]  in_a_col0,
    input  logic [15:0]  in_a_col1,
    input  logic [15:0]  in_a_col2,
    input  logic [15:0]  in_a_col3,
    input  logic [31:0]  in_a_val0,
    input  logic [31:0]  in_a_val1,
    input  logic [31:0]  in_a_val2,
    input  logic [31:0]  in_a_val3,

    input  logic [2:0]   in_b_row_start,
    input  logic [2:0]   in_b_row_end,
    input  logic [15:0]  in_b_col0,
    input  logic [15:0]  in_b_col1,
    input  logic [15:0]  in_b_col2,
    input  logic [15:0]  in_b_col3,
    input  logic [31:0]  in_b_val0,
    input  logic [31:0]  in_b_val1,
    input  logic [31:0]  in_b_val2,
    input  logic [31:0]  in_b_val3,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_row,
    output logic [3:0]   out_nnz,
    output logic [15:0]  out_col0,
    output logic [15:0]  out_col1,
    output logic [15:0]  out_col2,
    output logic [15:0]  out_col3,
    output logic [31:0]  out_val0,
    output logic [31:0]  out_val1,
    output logic [31:0]  out_val2,
    output logic [31:0]  out_val3,
    output logic [15:0]  out_col4,
    output logic [15:0]  out_col5,
    output logic [15:0]  out_col6,
    output logic [15:0]  out_col7,
    output logic [31:0]  out_val4,
    output logic [31:0]  out_val5,
    output logic [31:0]  out_val6,
    output logic [31:0]  out_val7
);
```

### Behavior interface

```python
class CsrMatrixAddRowMerge:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_row,
            in_alpha,
            in_beta,
            in_a_row_start,
            in_a_row_end,
            in_a_col0,
            in_a_col1,
            in_a_col2,
            in_a_col3,
            in_a_val0,
            in_a_val1,
            in_a_val2,
            in_a_val3,
            in_b_row_start,
            in_b_row_end,
            in_b_col0,
            in_b_col1,
            in_b_col2,
            in_b_col3,
            in_b_val0,
            in_b_val1,
            in_b_val2,
            in_b_val3):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_col0`, `out_col1`, `out_col2`, `out_col3`, `out_col4`, `out_col5`, `out_col6`,
  `out_col7`: 16.
- `out_nnz`: 4.
- `out_row`: 8.
- `out_val0`, `out_val1`, `out_val2`, `out_val3`, `out_val4`, `out_val5`, `out_val6`,
  `out_val7`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `csr_matrix_add_row_merge`: the complete task kernel.

## Functional Behavior

Compute one row of alpha*A + beta*B from two four-entry lane bundles. The row label is opaque eight-bit metadata and is copied to out_row. Coefficients and values are signed 32-bit integers; arithmetic wraps modulo 2^32. Column identifiers are unsigned 16-bit integers.

An A lane k participates exactly when 0<=k<4 and in_a_row_start<=k<in_a_row_end; B uses its own bounds. The three-bit endpoints do not wrap or index storage outside these four physical lanes. Empty or reversed intervals contribute no terms.

Visit active A lanes in port order, then active B lanes. For each column, accumulate all scaled values into one result, including repeated columns within either input. Output columns appear in first-occurrence order. Keep a column even if its sum is zero. There are at most eight distinct columns, so out_nnz is four bits and all eight result slots are available. No unmatched input entry is discarded.

Return out_col0..out_col7 and out_val0..out_val7; slots at or above out_nnz contain zero. This is an unsorted row kernel, not whole-matrix allocation or CSR row-pointer construction. One input produces one output.

## Reset Behavior

Reset is active-high. It cancels pending work, clears output-valid and resets the output registers to zero. No pre-reset transaction may reappear. Inputs transfer only when in_valid and in_ready are both high; outputs transfer only when out_valid and out_ready are both high. A pending output remains valid with unchanged payload until accepted. The task does not prescribe internal latency or microarchitecture.
