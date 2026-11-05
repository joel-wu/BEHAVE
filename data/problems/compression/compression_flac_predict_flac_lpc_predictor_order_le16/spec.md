# FLAC LPC Predictor Order<=16

This block computes one FLAC linear-predictive residual sample per accepted input sample using a fixed-order, fixed-coefficient predictor. The predictor order is programmable from 1 through 16. For each input sample, the block forms a weighted sum of the most recent `order` decoded samples, performs an arithmetic right shift by the quantization value, subtracts that prediction from the current sample, and outputs the residual. The algorithm is the direct-form integer LPC residual computation used by FLAC with signed coefficients and signed sample history.

## Interface

### RTL interface

```systemverilog
module compression_flac_predict_flac_lpc_predictor_order_le16 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic  [4:0]  in_order,
    input  logic  [4:0]  in_lp_quantization,
    input  logic  [31:0] in_sample,
    input  logic  [31:0] in_warmup0,
    input  logic  [31:0] in_warmup1,
    input  logic  [31:0] in_warmup2,
    input  logic  [31:0] in_warmup3,
    input  logic  [31:0] in_warmup4,
    input  logic  [31:0] in_warmup5,
    input  logic  [31:0] in_warmup6,
    input  logic  [31:0] in_warmup7,
    input  logic  [31:0] in_warmup8,
    input  logic  [31:0] in_warmup9,
    input  logic  [31:0] in_warmup10,
    input  logic  [31:0] in_warmup11,
    input  logic  [31:0] in_warmup12,
    input  logic  [31:0] in_warmup13,
    input  logic  [31:0] in_warmup14,
    input  logic  [31:0] in_warmup15,
    input  logic  [31:0] in_qlp_coeff0,
    input  logic  [31:0] in_qlp_coeff1,
    input  logic  [31:0] in_qlp_coeff2,
    input  logic  [31:0] in_qlp_coeff3,
    input  logic  [31:0] in_qlp_coeff4,
    input  logic  [31:0] in_qlp_coeff5,
    input  logic  [31:0] in_qlp_coeff6,
    input  logic  [31:0] in_qlp_coeff7,
    input  logic  [31:0] in_qlp_coeff8,
    input  logic  [31:0] in_qlp_coeff9,
    input  logic  [31:0] in_qlp_coeff10,
    input  logic  [31:0] in_qlp_coeff11,
    input  logic  [31:0] in_qlp_coeff12,
    input  logic  [31:0] in_qlp_coeff13,
    input  logic  [31:0] in_qlp_coeff14,
    input  logic  [31:0] in_qlp_coeff15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_residual
);
```

### Behavior interface

```python
class CompressionFlacPredictFlacLpcPredictorOrderLe16:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_order,
        in_lp_quantization,
        in_sample,
        in_warmup0,
        in_warmup1,
        in_warmup2,
        in_warmup3,
        in_warmup4,
        in_warmup5,
        in_warmup6,
        in_warmup7,
        in_warmup8,
        in_warmup9,
        in_warmup10,
        in_warmup11,
        in_warmup12,
        in_warmup13,
        in_warmup14,
        in_warmup15,
        in_qlp_coeff0,
        in_qlp_coeff1,
        in_qlp_coeff2,
        in_qlp_coeff3,
        in_qlp_coeff4,
        in_qlp_coeff5,
        in_qlp_coeff6,
        in_qlp_coeff7,
        in_qlp_coeff8,
        in_qlp_coeff9,
        in_qlp_coeff10,
        in_qlp_coeff11,
        in_qlp_coeff12,
        in_qlp_coeff13,
        in_qlp_coeff14,
        in_qlp_coeff15,
    ):
        pass
```

Each call represents one accepted request and returns exactly one dictionary
with key `out_residual`. Its value is a Python integer in [0, 2**32 - 1],
encoding the signed residual's 32-bit word. Return this dictionary even for
order zero, not `None` or a list. Sample, history and coefficient arguments
likewise use unsigned 32-bit encodings; order and quantization arguments are
integers in [0, 31]. History is supplied in each request, not retained between
calls. `reset()` returns no response.

## Modules

- `compression_flac_predict_flac_lpc_predictor_order_le16`: top-level LPC residual calculator for predictor orders 1 through 16.

## Functional Behavior

All `in_sample`, `in_warmup*`, and `in_qlp_coeff*` ports carry signed 32-bit two's-complement integers. `out_residual` is a signed 32-bit two's-complement result returned as a 32-bit bit pattern.

`in_order` is an unsigned 5-bit integer. Only values 1 through 16 inclusive are intended for use. For any value outside that range, the block still computes deterministically by using terms with indices `j` satisfying `0 <= j < 16` and `j < in_order`. Therefore:
- if `in_order = 0`, no terms participate and the prediction sum is 0;
- if `in_order` is 1 through 16, exactly that many terms participate;
- if `in_order` is 17 through 31, all 16 available terms participate.

`in_lp_quantization` is an unsigned 5-bit integer and is used directly as the shift count. Because the port is 5 bits wide, the shift count is always in the range 0 through 31 inclusive.

The block is stateless across accepted transactions. Each accepted input transaction is independent and uses only the values presented in that transaction.

For one accepted input transaction:

1. Interpret the current sample as
   `sample = s32(in_sample)`.

2. Form the history vector from the warmup inputs:
   - `history[0]  = s32(in_warmup0)`
   - `history[1]  = s32(in_warmup1)`
   - ...
   - `history[15] = s32(in_warmup15)`

   `history[k]` is the sample from `k+1` time steps before the current sample. Therefore `history[0]` is `data[i-1]`, `history[1]` is `data[i-2]`, and so on.

3. Form the coefficient vector:
   - `coeff[0]  = s32(in_qlp_coeff0)`
   - `coeff[1]  = s32(in_qlp_coeff1)`
   - ...
   - `coeff[15] = s32(in_qlp_coeff15)`

4. Compute the unshifted prediction sum as a signed integer:
   `sum = Σ(coeff[j] * history[j])` over all `j` in `0 .. 15` for which `j < in_order`.

   Multiplication is signed-by-signed. The mathematical sum is taken with enough precision to preserve the exact integer result before shifting. No intermediate truncation is permitted before the final residual conversion to 32 bits.

5. Compute the shifted prediction:
   `prediction = sum >> in_lp_quantization`

   The shift is an arithmetic right shift on the signed integer `sum`. For `in_lp_quantization = 0`, `prediction = sum`. For `in_lp_quantization` in 1..31, the result is floor division by `2^in_lp_quantization` for nonnegative `sum`, and rounds toward negative infinity for negative `sum`, exactly matching two's-complement arithmetic right shift.

6. Compute the residual:
   `residual = sample - prediction`

7. Drive one output transaction with
   `out_residual = residual mod 2^32`.

   This is two's-complement wrap to 32 bits. No saturation is applied.

Worked example for `in_order = 3`:
- `sum = coeff[0]*history[0] + coeff[1]*history[1] + coeff[2]*history[2]`
- `prediction = sum >> in_lp_quantization`
- `out_residual = u32(sample - prediction)`

Worked example for sign behavior:
- If `sum = -3` and `in_lp_quantization = 1`, then `prediction = -2`.
- If `sample = 7`, the residual is `7 - (-2) = 9`, so `out_residual = 0x00000009`.

## Reset Behavior

Reset is synchronous active-high. On a rising clock edge with `rst = 1`, the block returns to its idle state.

The block has no architectural state.

After reset:
- `out_valid = 0`
- `out_residual = 32'h00000000`

Because the block is stateless, reset does not alter any stored history or coefficient state; none exists.
