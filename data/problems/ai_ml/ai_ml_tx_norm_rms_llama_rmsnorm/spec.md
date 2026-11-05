# ai_ml_tx_norm_rms_llama_rmsnorm

This block computes Llama RMSNorm over four BF16 activations with four BF16 weights. It calculates the normalization in FP32, rounds the normalized activations back to BF16, then multiplies by the learned weights and rounds to BF16 again. This preserves the source's cast-before-weight order. Each accepted input produces one four-lane output.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_norm_rms_llama_rmsnorm (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x0,
    input  logic [15:0] in_x1,
    input  logic [15:0] in_x2,
    input  logic [15:0] in_x3,
    input  logic [15:0] in_w0,
    input  logic [15:0] in_w1,
    input  logic [15:0] in_w2,
    input  logic [15:0] in_w3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y0,
    output logic [15:0] out_y1,
    output logic [15:0] out_y2,
    output logic [15:0] out_y3
);
```

### Behavior interface

```python
class ai_ml_tx_norm_rms_llama_rmsnorm:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x0, in_x1, in_x2, in_x3, in_w0, in_w1, in_w2, in_w3):
        pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_tx_norm_rms_llama_rmsnorm`: top-level RMSNorm datapath and handshake control.

## Functional Behavior

All data ports `in_x0..in_x3`, `in_w0..in_w3`, and `out_y0..out_y3` carry raw BF16 bit patterns in 16-bit unsigned containers.

BF16 interpretation:
- Sign bit: bit 15.
- Exponent field: bits 14:7 with bias 127.
- Fraction field: bits 6:0.
- Every input BF16 operand is first promoted to FP32 by `bf16_to_fp32`.
- Every output is produced by converting an FP32 result to BF16 by `fp32_to_bf16`.

The block is stateless apart from handshake storage. There is no accumulation across transactions.

For one accepted input transaction, define:
- `x0 = bf16_to_fp32(in_x0)`
- `x1 = bf16_to_fp32(in_x1)`
- `x2 = bf16_to_fp32(in_x2)`
- `x3 = bf16_to_fp32(in_x3)`
- `w0 = bf16_to_fp32(in_w0)`
- `w1 = bf16_to_fp32(in_w1)`
- `w2 = bf16_to_fp32(in_w2)`
- `w3 = bf16_to_fp32(in_w3)`

The fixed FP32 constants are:
- `ONE_QUARTER = 0x3E800000`, equal to 0.25.
- `EPSILON = 0x358637BD`, equal to the FP32 encoding of `1e-6`.

The computation order is exactly:
1. `sq0 = fp32_mul(x0, x0)`
2. `sq1 = fp32_mul(x1, x1)`
3. `sq2 = fp32_mul(x2, x2)`
4. `sq3 = fp32_mul(x3, x3)`
5. `sum01 = fp32_add(sq0, sq1)`
6. `sum23 = fp32_add(sq2, sq3)`
7. `sum_all = fp32_add(sum01, sum23)`
8. `mean = fp32_mul(sum_all, ONE_QUARTER)`
9. `mean_eps = fp32_add(mean, EPSILON)`

The scale uses the following two separately rounded FP32 operations. This is a declared numerical specialization, not a claim of bit equality to a backend's single `rsqrt` instruction:
- `sqrt_mean = fp32_sqrt(mean_eps)`
- if `sqrt_mean` encodes +0 or -0, then `scale = 0x7F800000`
- else `scale = fp32_div(0x3F800000, sqrt_mean)`

`0x3F800000` is FP32 value 1.0.

The four output lanes are then computed independently in this order:
1. `norm0 = bf16_to_fp32(fp32_to_bf16(fp32_mul(x0, scale)))`
2. `norm1 = bf16_to_fp32(fp32_to_bf16(fp32_mul(x1, scale)))`
3. `norm2 = bf16_to_fp32(fp32_to_bf16(fp32_mul(x2, scale)))`
4. `norm3 = bf16_to_fp32(fp32_to_bf16(fp32_mul(x3, scale)))`
5. `prod0 = fp32_mul(w0, norm0)`
6. `prod1 = fp32_mul(w1, norm1)`
7. `prod2 = fp32_mul(w2, norm2)`
8. `prod3 = fp32_mul(w3, norm3)`
9. `out_y0 = fp32_to_bf16(prod0)`
10. `out_y1 = fp32_to_bf16(prod1)`
11. `out_y2 = fp32_to_bf16(prod2)`
12. `out_y3 = fp32_to_bf16(prod3)`

This is a 1:1 transaction mapping. Every accepted input transaction produces exactly one output transaction with keys:
- `out_y0`
- `out_y1`
- `out_y2`
- `out_y3`

Worked example for exact operation order:
- If `in_x0 = in_x1 = in_x2 = in_x3 = 0x3F80` and `in_w0 = in_w1 = in_w2 = in_w3 = 0x3F80`, then each BF16 input is 1.0 after promotion.
- `sq0..sq3` are all FP32 1.0.
- `sum_all = 4.0`, `mean = 1.0`, `mean_eps = 1.000001`.
- `scale = 1 / sqrt(1.000001)`.
- Each output lane is `fp32_to_bf16(scale)`.

Numerical rules:
- Promotion places the BF16 bits in the upper half of a binary32 word. BF16 conversion rounds the discarded 16 bits to nearest, ties to even, preserving the sign of zero and infinity; narrowing a NaN preserves its sign and yields a quiet payload `0x7fc0` in the remaining bits. Promotion preserves all source bits, including NaN payloads. Arithmetic NaNs below are positive, so final NaN outputs are `0x7fc0`.
- Every FP32 operation rounds separately to nearest, ties to even. Subnormal operands retain their numerical values. Underflow is gradual, with nearest-even rounding also at the zero/subnormal and subnormal/normal boundaries. Compare the final BF16 encodings exactly, without a tolerance.
- Any NaN operand or invalid operation (opposite infinities added, zero times infinity, zero divided by zero, infinity divided by infinity, or a negative nonzero square root) gives `0x7fc00000` (a project sign/payload convention).
- Multiplication and division use the XOR of operand signs for zero or infinity results; a finite nonzero value divided by zero is signed infinity. Square root preserves signed zero and positive infinity. Overflow gives signed infinity.
- Addition of two negative zeros yields negative zero; other exact cancellations yield positive zero. No reassociation or fused operation may change the prescribed rounding points.

Input and output transfers occur on rising clock edges with the respective valid and ready signals both high. Hold valid and payload while stalled, retain response order, and discard pending responses on reset. Payloads need only be meaningful when valid, except for the reset values below.

## Reset Behavior

Reset is active-high. Reset may be implemented synchronously or asynchronously. On reset:
- the block contains no pending output transaction,
- `out_valid` is deasserted,
- `in_ready` is asserted whenever no output transaction is pending,
- stored output payload registers, if any, are set to:
  - `out_y0 = 16'h0000`
  - `out_y1 = 16'h0000`
  - `out_y2 = 16'h0000`
  - `out_y3 = 16'h0000`

There is no other architectural state.
