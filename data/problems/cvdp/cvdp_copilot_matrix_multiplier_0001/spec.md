# matrix_multiplier

Multiply packed row-major unsigned matrices with configurable compatible dimensions and an output width sufficient to retain each exact sum.

## Interface

### RTL interface

```systemverilog
module matrix_multiplier #(
    parameter int ROW_A = 4, parameter int COL_A = 4,
    parameter int ROW_B = 4, parameter int COL_B = 4,
    parameter int INPUT_DATA_WIDTH = 8,
    parameter int OUTPUT_DATA_WIDTH = 2*INPUT_DATA_WIDTH+$clog2(COL_A)
) (
    input logic [ROW_A*COL_A*INPUT_DATA_WIDTH-1:0] matrix_a,
    input logic [ROW_B*COL_B*INPUT_DATA_WIDTH-1:0] matrix_b,
    output logic [ROW_A*COL_B*OUTPUT_DATA_WIDTH-1:0] matrix_c
);
```

### Behavior interface

```python
class MatrixMultiplier:
    def __init__(self, ROW_A=4, COL_A=4, ROW_B=4, COL_B=4, INPUT_DATA_WIDTH=8, OUTPUT_DATA_WIDTH=None): ...
    def reset(self): ...
    def process(self, matrix_a, matrix_b): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `matrix_c`: ROW_A*COL_B*OUTPUT_DATA_WIDTH.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.
Constructor dimensions and widths match the RTL specialization. OUTPUT_DATA_WIDTH=None
selects 2*INPUT_DATA_WIDTH+ceil(log2(COL_A)), the same derived default as the RTL.

### Combinational observations

All input bit patterns of the declared widths are valid. Outputs are observed
after combinational settling, including repeated identical inputs. There is
no clock, handshake or retained state.

## Modules

- `matrix_multiplier`: the native combinational module shown above.

## Functional Behavior

All dimensions and bit widths are positive and `ROW_B=COL_A`.
Elements are unsigned. Flatten matrices row-major with element (0,0) in
the least-significant lane: A[r,k] starts at `(r*COL_A+k)*INPUT_DATA_WIDTH`,
B[k,c] at `(k*COL_B+c)*INPUT_DATA_WIDTH`, and C[r,c] at
`(r*COL_B+c)*OUTPUT_DATA_WIDTH`. Each C[r,c] is the exact sum of
`A[r,k]*B[k,c]` over k from 0 through COL_A-1. Non-square matrices are supported.

`OUTPUT_DATA_WIDTH` defaults to `2*INPUT_DATA_WIDTH+ceil(log2(COL_A))` and
must be at least that value. This guarantees no product or sum overflow;
narrow-output truncation is not part of the task.

## Reset Behavior

There is no RTL reset or persistent state. Results do not depend on earlier inputs.
