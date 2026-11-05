# ai_ml_tx_norm_ln_bert_layernorm

This module performs one BERT-style LayerNorm operation on a fixed 4-element feature vector. Each accepted input transaction supplies four FP32 data elements `x0..x3`, four FP32 scale elements `gamma0..gamma3`, and four FP32 bias elements `beta0..beta3`. The module computes the mean of the four data elements, the variance of the four centered elements, adds a fixed epsilon `1.0e-5`, takes the square root, normalizes each element, then applies elementwise affine scaling and bias. Arithmetic uses the binary32 operations and explicit numerical rules below. One accepted input transaction produces exactly one output transaction carrying `y0..y3`.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_norm_ln_bert_layernorm (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,
    input  logic [31:0] in_gamma0,
    input  logic [31:0] in_gamma1,
    input  logic [31:0] in_gamma2,
    input  logic [31:0] in_gamma3,
    input  logic [31:0] in_beta0,
    input  logic [31:0] in_beta1,
    input  logic [31:0] in_beta2,
    input  logic [31:0] in_beta3,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
);
```

### Behavior interface

```python
class ai_ml_tx_norm_ln_bert_layernorm:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_x0, in_x1, in_x2, in_x3,
        in_gamma0, in_gamma1, in_gamma2, in_gamma3,
        in_beta0, in_beta1, in_beta2, in_beta3,
    ): ...
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_tx_norm_ln_bert_layernorm`: top-level LayerNorm block for a single 4-element vector.

## Functional Behavior

All input and output data ports are 32-bit unsigned integers carrying raw IEEE-754 binary32 bit patterns.

The computation uses the following FP32 constants:

- `FOUR = 0x40800000`, representing `4.0`.
- `EPSILON = 0x3727C5AC`, representing `1.0e-5` encoded as binary32.

For each accepted input transaction, define:

- `x[0] = in_x0`, `x[1] = in_x1`, `x[2] = in_x2`, `x[3] = in_x3`
- `gamma[0] = in_gamma0`, `gamma[1] = in_gamma1`, `gamma[2] = in_gamma2`, `gamma[3] = in_gamma3`
- `beta[0] = in_beta0`, `beta[1] = in_beta1`, `beta[2] = in_beta2`, `beta[3] = in_beta3`

The result is computed in this exact operation order:

1. Mean:
   - `sum01 = fp32_add(x[0], x[1])`
   - `sum23 = fp32_add(x[2], x[3])`
   - `sum   = fp32_add(sum01, sum23)`
   - `mean  = fp32_div(sum, FOUR)`

2. Centered values:
   - `d[0] = fp32_sub(x[0], mean)`
   - `d[1] = fp32_sub(x[1], mean)`
   - `d[2] = fp32_sub(x[2], mean)`
   - `d[3] = fp32_sub(x[3], mean)`

3. Squared centered values:
   - `s[0] = fp32_mul(d[0], d[0])`
   - `s[1] = fp32_mul(d[1], d[1])`
   - `s[2] = fp32_mul(d[2], d[2])`
   - `s[3] = fp32_mul(d[3], d[3])`

4. Variance:
   - `vsum01   = fp32_add(s[0], s[1])`
   - `vsum23   = fp32_add(s[2], s[3])`
   - `vsum     = fp32_add(vsum01, vsum23)`
   - `variance = fp32_div(vsum, FOUR)`

5. Standard deviation:
   - `variance_eps = fp32_add(variance, EPSILON)`
   - `stddev       = fp32_sqrt(variance_eps)`

6. Normalize:
   - `n[0] = fp32_div(d[0], stddev)`
   - `n[1] = fp32_div(d[1], stddev)`
   - `n[2] = fp32_div(d[2], stddev)`
   - `n[3] = fp32_div(d[3], stddev)`

7. Affine transform:
   - `m[0] = fp32_mul(n[0], gamma[0])`
   - `m[1] = fp32_mul(n[1], gamma[1])`
   - `m[2] = fp32_mul(n[2], gamma[2])`
   - `m[3] = fp32_mul(n[3], gamma[3])`

8. Outputs:
   - `out_y0 = fp32_add(m[0], beta[0])`
   - `out_y1 = fp32_add(m[1], beta[1])`
   - `out_y2 = fp32_add(m[2], beta[2])`
   - `out_y3 = fp32_add(m[3], beta[3])`

The module is stateless apart from handshake control. No input transaction depends on any earlier transaction.

The output transaction count is exactly 1 for every accepted input transaction.

Each named operation rounds its exact result to binary32, round-to-nearest ties-to-even; multiply and add are separate, not fused. Subnormal operands retain their numerical value; underflow is gradual with nearest-even rounding also at zero/subnormal and subnormal/normal boundaries. Compare output encodings exactly, without a tolerance. Overflow produces signed infinity; NaNs and invalid operations produce `0x7FC00000` (a project sign/payload convention). Other zero/infinity cases follow IEEE-754 arithmetic: multiplication/division use the XOR sign, exact cancellation in addition produces +0, adding two -0 values produces -0, and square root preserves -0. Thus the numerical contract is independent of any library implementation.

Transfers occur at rising edges with valid and ready both high. An offered input and an unaccepted output must remain stable until their respective transfer. Outputs are ordered; no fixed pipeline latency is required beyond the task run budget.

Worked example with exact values that are representable in binary32:

- Inputs:
  - `x = [0x3F800000, 0x40000000, 0x40400000, 0x40800000]` = `[1.0, 2.0, 3.0, 4.0]`
  - `gamma = [0x3F800000, 0x3F800000, 0x3F800000, 0x3F800000]` = all `1.0`
  - `beta = [0x00000000, 0x00000000, 0x00000000, 0x00000000]` = all `0.0`
- Mean is `2.5`.
- Variance is `1.25`.
- `stddev = sqrt(1.25 + 1.0e-5)`.
- Outputs are the four normalized elements, each returned as raw FP32 bit patterns after the exact ordered `fp_lib` computation above.

## Reset Behavior

Reset is active-high and asynchronous, discards pending transactions, and takes priority over transfers.

On reset:

- `out_valid = 0`
- `in_ready = 1`
- `out_y0 = 0x00000000`
- `out_y1 = 0x00000000`
- `out_y2 = 0x00000000`
- `out_y3 = 0x00000000`

The module has no architectural data state beyond the handshake state required to hold one in-flight 1:1 transaction.
