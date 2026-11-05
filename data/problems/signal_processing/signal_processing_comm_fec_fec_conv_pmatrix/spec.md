# Convolutional Code Puncture Matrix Lookup

This module returns the puncture matrix bit for a selected punctured convolutional code family. It implements a fixed lookup over twelve predefined matrices: six rates for constraint length K=7 and six rates for constraint length K=9. Each accepted input transaction selects one matrix by `(in_k_sel, in_rate_sel)` and one element within that matrix by `in_index`; the module returns the addressed matrix bit and a validity flag indicating whether the selector combination names a defined matrix and the index is within that matrix's length.

## Interface

### RTL interface

```systemverilog
module signal_processing_comm_fec_fec_conv_pmatrix (
    input  logic       clk,
    input  logic       rst,

    input  logic       in_valid,
    output logic       in_ready,
    input  logic [0:0] in_k_sel,
    input  logic [2:0] in_rate_sel,
    input  logic [3:0] in_index,

    output logic       out_valid,
    input  logic       out_ready,
    output logic       out_found,
    output logic [0:0] out_bit
);
```

`in_k_sel` encoding:
- `1'b0` : K=7 family
- `1'b1` : K=9 family

`in_rate_sel` encoding:
- `3'd0` : rate 2/3
- `3'd1` : rate 3/4
- `3'd2` : rate 4/5
- `3'd3` : rate 5/6
- `3'd4` : rate 6/7
- `3'd5` : rate 7/8
- `3'd6`, `3'd7` : undefined

### Behavior interface

```python
class SignalProcessingCommFecFecConvPmatrix:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_k_sel, in_rate_sel, in_index):
        pass
```

Each call represents one accepted lookup and returns exactly
`{"out_found": found, "out_bit": bit}`, with both fields integer 0 or 1.
Arguments are unsigned port encodings of 1, 3, and 4 bits respectively.
Undefined selectors and out-of-range indices return the same complete
dictionary with both fields zero, never `None` or a list. Requests retain
no mutable model state. `reset()` returns `None` and emits no lookup record.

## Modules

- `signal_processing_comm_fec_fec_conv_pmatrix`: top-level puncture-matrix lookup block.

## Functional Behavior

For each accepted input transaction, the module performs a pure table lookup.

1. Decode `in_k_sel` as one of two code families:
   - `0` selects the K=7 family.
   - `1` selects the K=9 family.

2. Decode `in_rate_sel` as one of six defined punctured-code rates:
   - `0` selects rate 2/3 and matrix length 4.
   - `1` selects rate 3/4 and matrix length 6.
   - `2` selects rate 4/5 and matrix length 8.
   - `3` selects rate 5/6 and matrix length 10.
   - `4` selects rate 6/7 and matrix length 12.
   - `5` selects rate 7/8 and matrix length 14.
   - `6` and `7` do not select any matrix.

3. If `(in_k_sel, in_rate_sel)` selects one of the following matrices, the matrix contents are indexed in row-major order exactly as listed below. The first listed integer is index 0, the second is index 1, and so on.

K=7 family (`in_k_sel = 0`):
- rate 2/3 (`in_rate_sel = 0`), length 4:
  - `[1, 1, 1, 0]`
- rate 3/4 (`in_rate_sel = 1`), length 6:
  - `[1, 1, 0, 1, 0, 1]`
- rate 4/5 (`in_rate_sel = 2`), length 8:
  - `[1, 1, 1, 1, 1, 0, 0, 0]`
- rate 5/6 (`in_rate_sel = 3`), length 10:
  - `[1, 1, 0, 1, 0, 1, 0, 1, 0, 1]`
- rate 6/7 (`in_rate_sel = 4`), length 12:
  - `[1, 1, 1, 0, 1, 0, 1, 0, 0, 1, 0, 1]`
- rate 7/8 (`in_rate_sel = 5`), length 14:
  - `[1, 1, 1, 1, 0, 1, 0, 1, 0, 0, 0, 1, 0, 1]`

K=9 family (`in_k_sel = 1`):
- rate 2/3 (`in_rate_sel = 0`), length 4:
  - `[1, 1, 1, 0]`
- rate 3/4 (`in_rate_sel = 1`), length 6:
  - `[1, 1, 1, 1, 0, 0]`
- rate 4/5 (`in_rate_sel = 2`), length 8:
  - `[1, 1, 0, 1, 1, 0, 1, 0]`
- rate 5/6 (`in_rate_sel = 3`), length 10:
  - `[1, 0, 1, 1, 0, 1, 1, 0, 0, 1]`
- rate 6/7 (`in_rate_sel = 4`), length 12:
  - `[1, 1, 0, 1, 1, 0, 1, 0, 1, 0, 0, 1]`
- rate 7/8 (`in_rate_sel = 5`), length 14:
  - `[1, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 0, 0]`

4. Output generation:
   - If `(in_k_sel, in_rate_sel)` does not select any defined matrix, then `out_found = 0` and `out_bit = 0`.
   - Else, if `in_index` is less than the selected matrix length, then `out_found = 1` and `out_bit` is the selected matrix entry at that zero-based index.
   - Else, if `in_index` is greater than or equal to the selected matrix length, then `out_found = 0` and `out_bit = 0`.

5. Arithmetic and encoding:
   - All ports carry unsigned integer bit patterns.
   - `out_bit` is a 1-bit unsigned value, either `0` or `1`.
   - `out_found` is a 1-bit unsigned value, either `0` or `1`.
   - No internal state participates in the lookup result; each transaction is independent.

## Reset Behavior

Reset is synchronous active-high `rst`.

On reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_found = 0`
- `out_bit = 0`

Architectural state after reset:
- There is no persistent architectural state other than the fixed lookup tables.
- The block is ready to accept the next input transaction immediately after reset is deasserted.
