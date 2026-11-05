# PNG Paeth Predictor

This block computes the PNG Paeth predictor for one byte position in a filtered scanline and reconstructs the unfiltered output byte for that position. The algorithm is the PNG Paeth rule: given the already-reconstructed left byte `a`, the prior-row byte above `b`, the prior-row byte above-left `c`, and the current filtered byte `x`, it selects one of `a`, `b`, or `c` by minimizing the absolute differences derived from `p = a + b - c`, with ties resolved in favor of `a`, then `b`, then `c`, and outputs `(x + predictor) mod 256`.

## Interface

### RTL interface

```systemverilog
module compression_png_filter_png_paeth_predictor (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_filtered_byte,
    input  logic [7:0]  in_left_byte,
    input  logic [7:0]  in_up_byte,
    input  logic [7:0]  in_up_left_byte,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_recon_byte,
    output logic [7:0]  out_predictor_byte
);
```

### Behavior interface

```python
class CompressionPngFilterPngPaethPredictor:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_filtered_byte, in_left_byte, in_up_byte, in_up_left_byte): ...
```

Each `process` call consumes one accepted input transaction, not a clock cycle.
All four arguments are unsigned 8-bit integers. It always returns one
dictionary with exactly `out_recon_byte` and `out_predictor_byte`, both unsigned
8-bit integers. Zero results still return both fields, never `None` or a list.
The caller supplies all neighbors; the instance keeps no row or pixel history.
Construction and `reset()` produce no output record; `reset()` returns `None`.

## Modules

- `compression_png_filter_png_paeth_predictor`: top-level module implementing one PNG Paeth prediction and byte reconstruction per accepted input transaction.

## Functional Behavior

All inputs and outputs are 8-bit unsigned integers in the range `[0, 255]`.

For each accepted input transaction:

1. Interpret:
   - `a = in_left_byte`
   - `b = in_up_byte`
   - `c = in_up_left_byte`
   - `x = in_filtered_byte`

2. Form signed intermediate values using ordinary integer arithmetic, without 8-bit wrap:
   - `p = a + b - c`
   - `pa = |p - a|`
   - `pb = |p - b|`
   - `pc = |p - c|`

   Because `p - a = b - c`, `p - b = a - c`, and `p - c = a + b - 2*c`, these quantities are evaluated over mathematical integers. No saturation or truncation applies to `p`, `pa`, `pb`, or `pc`.

3. Select the predictor byte `pr` using this exact priority order:
   - if `pa <= pb` and `pa <= pc`, then `pr = a`
   - else if `pb <= pc`, then `pr = b`
   - else `pr = c`

   This is equivalent to choosing the smallest of `pa`, `pb`, and `pc`, with ties resolved in favor of `a` over `b` over `c`.

4. Reconstruct the output byte:
   - `recon = (x + pr) mod 256`

   The modulo is exact wrap modulo `2^8`; equivalently, keep only the low 8 bits of the sum.

5. Drive outputs:
   - `out_predictor_byte = pr`
   - `out_recon_byte = recon`

Worked example:
- If `a = 10`, `b = 20`, `c = 15`, `x = 200`
- `p = 10 + 20 - 15 = 15`
- `pa = |15 - 10| = 5`
- `pb = |15 - 20| = 5`
- `pc = |15 - 15| = 0`
- predictor is `c = 15`
- reconstructed byte is `(200 + 15) mod 256 = 215`

Edge cases:
- If `a = b = c`, then `pa = pb = pc = 0`, so the predictor is `a` by tie priority.
- If `x + pr >= 256`, `out_recon_byte` wraps by discarding the carry.
- There is no special treatment for first row or first column inside this block; those cases are handled by the caller by supplying the appropriate byte values, typically zero for unavailable neighbors.

The block is stateless at the transaction level: each output depends only on the current input transaction.

## Reset Behavior

Reset is synchronous active-high `rst`.

Architectural state: none.

After reset, the block remains able to accept a new input transaction. Since there is no architectural state, reset does not change the functional mapping of future accepted inputs to outputs.

Output port requirements during and immediately after reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_recon_byte = 8'h00`
- `out_predictor_byte = 8'h00`
