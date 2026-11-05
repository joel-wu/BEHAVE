# Control FOC Transform Inverse Clarke Transform 2P to 3P

This block converts a two-axis stationary-frame vector `(alpha, beta)` into a three-phase vector `(a, b, c)` using the inverse Clarke transform. All inputs and outputs are signed Q15 values. The transform is implemented as `a = alpha`, `b = -0.5*alpha + (sqrt(3)/2)*beta`, `c = -0.5*alpha - (sqrt(3)/2)*beta`, with each constant multiply performed in Q15, each product narrowed from Q30 to Q15 by arithmetic right shift of 15 bits, and each final phase output saturated to the signed 16-bit Q15 range.

## Interface

### RTL interface

```systemverilog
module control_foc_transform_inv_clarke_transform_2p_to_3p (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_alpha,
    input  logic [15:0] in_beta,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_a,
    output logic [15:0] out_b,
    output logic [15:0] out_c
);
```

### Behavior interface

```python
class ControlFocTransformInvClarkeTransform2pTo3p:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_alpha, in_beta): ...
```

Each `process(in_alpha, in_beta)` call represents one accepted input pair.
Both arguments are unsigned 16-bit integers encoding the signed Q15 values.
Return one dictionary with exactly these keys:

```python
{"out_a": ..., "out_b": ..., "out_c": ...}
```

Each value is an unsigned 16-bit integer encoding its signed Q15 phase
result. Return all three fields even for a zero vector, not `None` or a list
of phases. No algorithmic state persists between calls. `reset()` returns
`None` and produces no response.

## Modules

- `control_foc_transform_inv_clarke_transform_2p_to_3p`: top-level inverse Clarke transform block.

## Functional Behavior

`in_alpha` and `in_beta` are signed 16-bit Q15 numbers. Their mathematical values are `raw * 2^-15`. Examples: `0x4000` represents `+0.5`, `0xC000` represents `-0.5`, and `0x8000` represents `-1.0`.

`out_a`, `out_b`, and `out_c` are signed 16-bit Q15 numbers with the same encoding.

The transform constants are fixed Q15 integers:

- `NEG_HALF = -16384`, representing `-0.5`
- `SQRT3_BY_2 = 28378`, representing `28378 / 32768 ≈ 0.86602783203125`

On each accepted input transaction, the block computes one output transaction as follows.

1. Interpret `in_alpha` and `in_beta` as signed 16-bit integers.
2. Compute `a_raw = alpha`.
3. Compute the Q15 products for phase `b`:
   - `b_alpha_prod_q30 = alpha * NEG_HALF`
   - `b_beta_prod_q30 = beta * SQRT3_BY_2`
4. Narrow each Q30 product to Q15 with arithmetic right shift by 15 bits:
   - `b_alpha_q15 = b_alpha_prod_q30 >>> 15`
   - `b_beta_q15 = b_beta_prod_q30 >>> 15`
   The shift is arithmetic and therefore rounds toward negative infinity for negative values.
5. Form the unsaturated Q15 sum for phase `b`:
   - `b_sum = b_alpha_q15 + b_beta_q15`
6. Saturate `b_sum` to the signed 16-bit range:
   - if `b_sum > 32767`, `b_sat = 32767`
   - else if `b_sum < -32768`, `b_sat = -32768`
   - else `b_sat = b_sum`
7. Compute the Q15 products for phase `c`:
   - `c_alpha_prod_q30 = alpha * NEG_HALF`
   - `c_beta_prod_q30 = beta * SQRT3_BY_2`
8. Narrow each Q30 product to Q15 with arithmetic right shift by 15 bits:
   - `c_alpha_q15 = c_alpha_prod_q30 >>> 15`
   - `c_beta_q15 = c_beta_prod_q30 >>> 15`
9. Form the unsaturated Q15 difference for phase `c`:
   - `c_sum = c_alpha_q15 - c_beta_q15`
10. Saturate `c_sum` to the signed 16-bit range:
    - if `c_sum > 32767`, `c_sat = 32767`
    - else if `c_sum < -32768`, `c_sat = -32768`
    - else `c_sat = c_sum`
11. Saturate `a_raw` to the signed 16-bit range using the same thresholds. Since `a_raw` is already a signed 16-bit input, this saturation does not change any legal input value.
12. Drive:
    - `out_a = a_sat` as a 16-bit unsigned bit pattern
    - `out_b = b_sat` as a 16-bit unsigned bit pattern
    - `out_c = c_sat` as a 16-bit unsigned bit pattern

Worked example with `alpha = 0x4000` (`+0.5`) and `beta = 0x0000` (`0.0`):

- `a = 0x4000`
- `b_alpha_prod_q30 = 16384 * (-16384) = -268435456`
- `b_alpha_q15 = -8192 = 0xE000`
- `b_beta_q15 = 0`
- `b = -8192 = 0xE000`
- `c = -8192 = 0xE000`

Worked example with `alpha = 0x0000` and `beta = 0x4000` (`+0.5`):

- `a = 0x0000`
- `b_beta_prod_q30 = 16384 * 28378 = 464945152`
- `b_beta_q15 = 14189 = 0x376D`
- `b = 14189 = 0x376D`
- `c = -14189 = 0xC893`

There is no internal frame accumulation, no dependence between transactions, and exactly one output transaction is produced for each accepted input transaction.

## Reset Behavior

The block has no architectural state.

`rst` is active-high. Reset may be implemented synchronously or asynchronously. While reset is asserted, the block shall present `out_valid = 0`. Since there is no stored data, output payload ports shall be driven to zero during reset: `out_a = 16'h0000`, `out_b = 16'h0000`, `out_c = 16'h0000`.

After reset deassertion, the block is ready to accept a new input transaction immediately. `in_ready` may be permanently asserted.
