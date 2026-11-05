# Control FOC Clarke Transform 3-Phase to 2-Phase

This block performs the Clarke transform used in field-oriented motor control to convert three phase currents into a two-axis stationary reference frame. It supports the two forms used by the control software: a full 3-input Clarke transform when all three phase currents are available, and a balanced-current form when only two independent phase currents are used and the third is implied by `ia + ib + ic = 0`. All arithmetic is fixed-point signed Q15.

## Interface

### RTL interface

```systemverilog
module control_foc_transform_clarke_transform_3p_to_2p (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic         in_full_clarke,
    input  logic [15:0]  in_ia,
    input  logic [15:0]  in_ib,
    input  logic [15:0]  in_ic,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_i_alpha,
    output logic [15:0]  out_i_beta
);
```

### Behavior interface

```python
class ControlFocTransformClarkeTransform3pTo2p:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_full_clarke, in_ia, in_ib, in_ic): ...
```

Each call represents one accepted request and returns exactly one dictionary:

```python
{
    "out_i_alpha": int,
    "out_i_beta": int,
}
```

Both values and all three current arguments are Python integers in [0, 65535],
encoding signed 16-bit Q15 port words. `in_full_clarke` is the integer 0 or 1.
Return both fields even for zero currents, not `None` or a list. The transform
has no persistent state; `reset()` returns no response.

## Modules

- `control_foc_transform_clarke_transform_3p_to_2p`: top-level Clarke transform block.

## Functional Behavior

All ports `in_ia`, `in_ib`, `in_ic`, `out_i_alpha`, and `out_i_beta` are signed 16-bit Q15 values. A Q15 value represents the real value `raw / 2^15`. Examples: `0x4000 = +0.5`, `0xC000 = -0.5`, `0x8000 = -1.0`.

`in_full_clarke` selects the transform form:

- `in_full_clarke = 1`: full three-phase Clarke transform.
- `in_full_clarke = 0`: balanced-current Clarke transform using only `ia` and `ib`. In this mode `in_ic` is ignored.

The block is stateless and produces exactly one output transaction for each accepted input transaction.

Signed interpretation:
- Convert each 16-bit input bit pattern to a signed integer in `[-32768, 32767]`.
- All multiplies use signed arithmetic.

Constants are fixed Q15 coefficients:
- `TWO_THIRDS_Q15 = round((2/3) * 2^15) = 21845`
- `NEG_ONE_THIRD_Q15 = round((-1/3) * 2^15) = -10923`
- `ONE_BY_SQRT3_Q15 = round((1/sqrt(3)) * 2^15) = 18919`
- `TWO_BY_SQRT3_Q15 = round((2/sqrt(3)) * 2^15) = 37837`

The transform equations are:

When `in_full_clarke = 1`:
- `i_alpha_real = (2/3) * ia - (1/3) * ib - (1/3) * ic`
- `i_beta_real  = (1/sqrt(3)) * ib - (1/sqrt(3)) * ic`

When `in_full_clarke = 0`:
- `i_alpha_real = ia`
- `i_beta_real  = (1/sqrt(3)) * ia + (2/sqrt(3)) * ib`

The fixed-point implementation is bit-exact as follows.

For `out_i_alpha` when `in_full_clarke = 1`:
1. Form three signed products in Q30:
   - `p0 = ia * 21845`
   - `p1 = ib * -10923`
   - `p2 = ic * -10923`
2. Sum them in signed integer arithmetic:
   - `sum_alpha = p0 + p1 + p2`
3. Convert Q30 to Q15 by arithmetic right shift by 15 bits:
   - `alpha = sum_alpha >> 15`
4. Saturate to signed 16-bit range:
   - if `alpha > 32767`, use `32767`
   - else if `alpha < -32768`, use `-32768`
   - else use `alpha`
5. Return the saturated result as an unsigned 16-bit output bit pattern.

For `out_i_beta` when `in_full_clarke = 1`:
1. Form two signed products in Q30:
   - `p0 = ib * 18919`
   - `p1 = ic * -18919`
2. Sum:
   - `sum_beta = p0 + p1`
3. Convert Q30 to Q15 by arithmetic right shift by 15 bits:
   - `beta = sum_beta >> 15`
4. Saturate to signed 16-bit range using the same limits as above.
5. Return the saturated result as an unsigned 16-bit output bit pattern.

For `out_i_alpha` when `in_full_clarke = 0`:
1. `alpha = ia`
2. Saturate to signed 16-bit range. This step has no effect for legal 16-bit input values, but it is part of the contract.
3. Return as unsigned 16-bit output bit pattern.

For `out_i_beta` when `in_full_clarke = 0`:
1. Form two signed products in Q30:
   - `p0 = ia * 18919`
   - `p1 = ib * 37837`
2. Sum:
   - `sum_beta = p0 + p1`
3. Convert Q30 to Q15 by arithmetic right shift by 15 bits:
   - `beta = sum_beta >> 15`
4. Saturate to signed 16-bit range using the same limits as above.
5. Return the saturated result as an unsigned 16-bit output bit pattern.

Rounding and truncation:
- The Q30-to-Q15 conversion uses arithmetic right shift by 15.
- This is truncation toward negative infinity for negative intermediate values, matching two's-complement arithmetic shift behavior.
- No extra rounding offset is added.

Overflow policy:
- Multiplication and accumulation are mathematically exact prior to the explicit Q30-to-Q15 shift and final 16-bit saturation.
- Final outputs saturate to the inclusive range `[-32768, 32767]`.

Worked examples:
- If `in_full_clarke = 0`, `ia = 0x4000` and `ib = 0x0000`, then `out_i_alpha = 0x4000` and `out_i_beta = 0x24F3` because `(16384 * 18919) >> 15 = 9459 = 0x24F3`.
- If `in_full_clarke = 1` and `ia = ib = ic`, then `out_i_alpha` and `out_i_beta` are both approximately zero, with exact result determined by the quantized coefficients above.

## Reset Behavior

Reset is active-high and synchronous to `clk`.

On reset:
- `in_ready = 1`
- `out_valid = 0`
- `out_i_alpha = 16'h0000`
- `out_i_beta = 16'h0000`

Architectural state:
- None. The block is stateless.
