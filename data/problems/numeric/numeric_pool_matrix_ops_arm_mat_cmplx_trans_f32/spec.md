# Complex Matrix Transpose Tile

This module performs a complex matrix transpose on one fixed-size tile per accepted input transaction. The tile size is 2 rows by 2 columns, with each matrix element represented as one complex FP32 value stored as two 32-bit words `(real, imag)`. For each transaction, the module accepts the four source elements in row-major order and emits the four transposed destination elements in row-major order of the transposed tile. The computation is a pure data rearrangement: no arithmetic is performed on the FP32 bit patterns, and each 32-bit real and imaginary word is copied unchanged.

## Interface

### RTL interface

```systemverilog
module complex_matrix_transpose_tile (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_a00_real,
    input  logic [31:0]  in_a00_imag,
    input  logic [31:0]  in_a01_real,
    input  logic [31:0]  in_a01_imag,
    input  logic [31:0]  in_a10_real,
    input  logic [31:0]  in_a10_imag,
    input  logic [31:0]  in_a11_real,
    input  logic [31:0]  in_a11_imag,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_b00_real,
    output logic [31:0]  out_b00_imag,
    output logic [31:0]  out_b01_real,
    output logic [31:0]  out_b01_imag,
    output logic [31:0]  out_b10_real,
    output logic [31:0]  out_b10_imag,
    output logic [31:0]  out_b11_real,
    output logic [31:0]  out_b11_imag
);
```

### Behavior interface

```python
class ComplexMatrixTransposeTile:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_a00_real, in_a00_imag,
        in_a01_real, in_a01_imag,
        in_a10_real, in_a10_imag,
        in_a11_real, in_a11_imag,
    ): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_b00_imag`, `out_b00_real`, `out_b01_imag`, `out_b01_real`, `out_b10_imag`,
  `out_b10_real`, `out_b11_imag`, `out_b11_real`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `complex_matrix_transpose_tile`: top-level single-tile complex transpose kernel.

## Functional Behavior

Each accepted input transaction represents one 2×2 source matrix tile:

- `A[0][0] = (in_a00_real, in_a00_imag)`
- `A[0][1] = (in_a01_real, in_a01_imag)`
- `A[1][0] = (in_a10_real, in_a10_imag)`
- `A[1][1] = (in_a11_real, in_a11_imag)`

The source tile is interpreted in row-major order, and each complex element occupies two consecutive logical components: real then imaginary. The transpose operation exchanges rows and columns without conjugation and without modifying any FP32 payload bits.

The destination tile `B = A^T` is:

- `B[0][0] = A[0][0]`
- `B[0][1] = A[1][0]`
- `B[1][0] = A[0][1]`
- `B[1][1] = A[1][1]`

The output transaction shall therefore be:

- `out_b00_real = in_a00_real`
- `out_b00_imag = in_a00_imag`
- `out_b01_real = in_a10_real`
- `out_b01_imag = in_a10_imag`
- `out_b10_real = in_a01_real`
- `out_b10_imag = in_a01_imag`
- `out_b11_real = in_a11_real`
- `out_b11_imag = in_a11_imag`

All input and output data ports are 32-bit unsigned bit patterns. They carry IEEE-754 binary32 encodings, but the module treats them as opaque 32-bit values. NaNs, infinities, signed zeros, normal numbers, and subnormals are copied bit-exactly with no decoding, rounding, canonicalization, or conjugation.

The module produces exactly one output transaction for each accepted input transaction.

## Reset Behavior

Reset is synchronous active-high through port `rst`.

On reset, all architectural state is cleared. This module has no persistent architectural state.

While reset is asserted, `out_valid` shall be `0`. After reset, before any input transaction has been accepted, `out_valid` shall be `0`. Output data ports have no architecturally significant value when `out_valid` is `0`.
