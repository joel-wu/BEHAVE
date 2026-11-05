# control_kalman_ekf_update_step

`control_kalman_ekf_update_step` performs one fixed-point Extended Kalman Filter measurement-update step for a 2-state system with a 1-dimensional measurement. The module accepts the prior state vector `x = [x0, x1]^T`, prior covariance matrix `P`, measurement Jacobian row `H = [h0, h1]`, predicted measurement `hx`, scalar measurement `z`, and scalar measurement noise `r`, and returns the posterior state, posterior covariance, innovation, innovation covariance, and Kalman gain. All arithmetic is integer fixed-point. State and Jacobian quantities use Q15, covariance and measurement-noise quantities use Q31, and the update uses the Joseph covariance form `P' = (I-KH)P(I-KH)^T + K R K^T` with truncation toward negative infinity on arithmetic right shifts and saturation after each stated narrowing step.

## Interface

### RTL interface

```systemverilog
module control_kalman_ekf_update_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_x0,
    input  logic [15:0]  in_x1,
    input  logic [31:0]  in_p00,
    input  logic [31:0]  in_p01,
    input  logic [31:0]  in_p10,
    input  logic [31:0]  in_p11,
    input  logic [15:0]  in_h0,
    input  logic [15:0]  in_h1,
    input  logic [15:0]  in_hx,
    input  logic [15:0]  in_z,
    input  logic [31:0]  in_r,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_x0,
    output logic [15:0]  out_x1,
    output logic [31:0]  out_p00,
    output logic [31:0]  out_p01,
    output logic [31:0]  out_p10,
    output logic [31:0]  out_p11,
    output logic [15:0]  out_k0,
    output logic [15:0]  out_k1,
    output logic [15:0]  out_y,
    output logic [31:0]  out_s
);
endmodule
```

### Behavior interface

```python
class control_kalman_ekf_update_step:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_x0, in_x1,
        in_p00, in_p01, in_p10, in_p11,
        in_h0, in_h1,
        in_hx, in_z,
        in_r
    ): ...
```

Call `process(...)` once per accepted input transaction. Arguments use unsigned
port encodings, including Q15 and Q31 values. Every call returns one dictionary
with exactly these keys: `out_x0`, `out_x1`, `out_p00`, `out_p01`, `out_p10`,
`out_p11`, `out_k0`, `out_k1`, `out_y`, and `out_s`. Values are unsigned 16-bit
encodings for `out_x0`, `out_x1`, `out_k0`, `out_k1`, and `out_y`; the other five
fields are unsigned 32-bit encodings. Zero innovation covariance still produces
the full result dictionary with the specified zero gains, not `None` or an error.

Prior state and covariance are supplied in every call, not retained between
calls. Construction and `reset()` establish this stateless model; `reset()`
returns `None` without an output record.

### Transaction protocol

An input transfers on a rising edge with `in_valid && in_ready`; an output
transfers with `out_valid && out_ready`. The sender holds an unaccepted request
stable, and the design holds every field of a stalled valid response stable.
Responses are ordered, one per accepted request. Reset cancels pending work.
Latency is not prescribed, subject to the run's declared bounds. Payloads are
unspecified when `out_valid=0`, except for the reset values stated below.

## Modules

- `control_kalman_ekf_update_step`: top-level single-clock EKF update block.

## Functional Behavior

All ports carry two's-complement integer bit patterns.

Interpretation of inputs and outputs:
- `in_x0`, `in_x1`, `in_h0`, `in_h1`, `in_hx`, `in_z`, `out_x0`, `out_x1`, `out_k0`, `out_k1`, `out_y` are signed 16-bit Q15 values. Real value = raw × 2^-15. Examples: `0x4000 = +0.5`, `0x8000 = -1.0`.
- `in_p00`, `in_p01`, `in_p10`, `in_p11`, `in_r`, `out_p00`, `out_p01`, `out_p10`, `out_p11`, `out_s` are signed 32-bit Q31 values. Real value = raw × 2^-31.

Saturation functions:
- `sat16(v)` clamps signed integer `v` to the inclusive range `[-32768, 32767]`.
- `sat32(v)` clamps signed integer `v` to the inclusive range `[-2147483648, 2147483647]`.

Signed reinterpretation:
- Each input bit pattern is first reinterpreted as a signed integer of its port width.

Arithmetic primitives:
1. Q15 × Q15 → Q15:
   - Form signed product `prod_q30 = a * b`.
   - Narrow with arithmetic right shift by 15 bits: `shr = prod_q30 >> 15`.
   - `>>` is arithmetic right shift on the signed product; this is truncation toward negative infinity.
   - Result is `sat16(shr)`.

2. Q31 × Q15 → Q31:
   - Form signed product `prod_q46 = a * b`.
   - Narrow with arithmetic right shift by 15 bits.
   - Result is `sat32(prod_q46 >> 15)`.

3. Q15 × Q31 → Q31:
   - Same rule as Q31 × Q15.

4. Q31 divided by Q31 to produce Q15:
   - If denominator is zero, the result is exactly `0`.
   - Otherwise compute signed numerator `num = pht << 15`, where `pht` is the signed Q31 dividend before division.
   - Compute signed quotient `q = trunc(num / s)` with truncation toward zero.
   - Result is `sat16(q)`.
   - This defines the Kalman gain computation and is the only division used in the block.

The update is performed in this order:

1. Innovation residual:
   - `y_int = in_z - in_hx` using signed integer subtraction in at least 17 bits.
   - `y = sat16(y_int)`.
   - `out_y = y`.

2. Cross-covariance vector `PHT = P * H^T`:
   - `pht0 = sat32(((in_p00 * in_h0) + (in_p01 * in_h1)) >> 15)`.
   - `pht1 = sat32(((in_p10 * in_h0) + (in_p11 * in_h1)) >> 15)`.
   - Each multiplication is signed Q31 × Q15; the two products are summed before the final `>> 15` narrowing and `sat32`.

3. Innovation covariance:
   - `hpht = sat32(((in_h0 * pht0) + (in_h1 * pht1)) >> 15)`.
   - `s = sat32(hpht + in_r)`.
   - `out_s = s`.
   - `hpht` is Q31 because each term is Q15 × Q31 narrowed by `>> 15`.
   - The final add uses signed integer addition followed by `sat32`.

4. Kalman gain vector:
   - If `s == 0`, then `k0 = 0` and `k1 = 0`.
   - Else:
     - `k0 = sat16(trunc_toward_zero((pht0 << 15) / s))`.
     - `k1 = sat16(trunc_toward_zero((pht1 << 15) / s))`.
   - `out_k0 = k0`, `out_k1 = k1`.

5. Posterior state:
   - `x0_corr = q15_mul_q15(k0, y)`.
   - `x1_corr = q15_mul_q15(k1, y)`.
   - `x0_new = sat16(in_x0 + x0_corr)`.
   - `x1_new = sat16(in_x1 + x1_corr)`.
   - `out_x0 = x0_new`, `out_x1 = x1_new`.

6. Matrix `A = I - K H`, represented in Q15:
   - `a00 = sat16(32767 - q15_mul_q15(k0, in_h0))`.
   - `a01 = sat16(0 - q15_mul_q15(k0, in_h1))`.
   - `a10 = sat16(0 - q15_mul_q15(k1, in_h0))`.
   - `a11 = sat16(32767 - q15_mul_q15(k1, in_h1))`.
   - The Q15 encoding of `+1.0` used here is `32767`, not `-32768`.

7. Intermediate matrix `M = A * P`, in Q31:
   - `m00 = sat32(((a00 * in_p00) + (a01 * in_p10)) >> 15)`.
   - `m01 = sat32(((a00 * in_p01) + (a01 * in_p11)) >> 15)`.
   - `m10 = sat32(((a10 * in_p00) + (a11 * in_p10)) >> 15)`.
   - `m11 = sat32(((a10 * in_p01) + (a11 * in_p11)) >> 15)`.

8. Joseph left term `J = M * A^T`, in Q31:
   - `j00 = sat32(((m00 * a00) + (m01 * a01)) >> 15)`.
   - `j01 = sat32(((m00 * a10) + (m01 * a11)) >> 15)`.
   - `j10 = sat32(((m10 * a00) + (m11 * a01)) >> 15)`.
   - `j11 = sat32(((m10 * a10) + (m11 * a11)) >> 15)`.

9. Joseph additive term `N = K R K^T`, in Q31:
   - `kr0 = sat32((k0 * in_r) >> 15)`.
   - `kr1 = sat32((k1 * in_r) >> 15)`.
   - `n00 = sat32((kr0 * k0) >> 15)`.
   - `n01 = sat32((kr0 * k1) >> 15)`.
   - `n10 = sat32((kr1 * k0) >> 15)`.
   - `n11 = sat32((kr1 * k1) >> 15)`.

10. Posterior covariance:
   - `p00_new = sat32(j00 + n00)`.
   - `p01_new = sat32(j01 + n01)`.
   - `p10_new = sat32(j10 + n10)`.
   - `p11_new = sat32(j11 + n11)`.
   - `out_p00 = p00_new`, `out_p01 = p01_new`, `out_p10 = p10_new`, `out_p11 = p11_new`.

Division example:
- If `pht0 = 0x40000000` (Q31 = +0.5) and `s = 0x20000000` (Q31 = +0.25), then
  `num = pht0 << 15 = 0x0000200000000000`,
  `num / s = 65536`,
  `sat16(65536) = 32767`,
  so `k0 = 0x7FFF`.
- If `s = 0`, then `k0 = 0` and `k1 = 0` regardless of `pht0` and `pht1`.

The block is stateless across transactions. Each accepted input transaction produces exactly one output transaction, and no result depends on any prior accepted input.

This is a finite fixed-point specialization, not bit-equivalence to floating
FilterPy. In particular, the diagonal constant `32767` represents `1-2^-15`,
not exact one: even with zero gain, the two Joseph multiplications can slightly
reduce `P`. No positive-semidefiniteness guarantee is made for arbitrary input
bit patterns or saturating arithmetic. A zero innovation covariance uses the
explicit zero-gain rule above rather than attempting a singular matrix inverse.

## Reset Behavior

Reset is active-high and asynchronous to the clock edge (`posedge clk or posedge rst` implementation style). On reset:
- `out_valid` is deasserted.
- `in_ready` may be asserted or deasserted according to the handshake wrapper state, but after reset release with no buffered output the block shall be ready to accept one input transaction.
- All output data registers are set to zero:
  - `out_x0 = 16'h0000`
  - `out_x1 = 16'h0000`
  - `out_p00 = 32'h00000000`
  - `out_p01 = 32'h00000000`
  - `out_p10 = 32'h00000000`
  - `out_p11 = 32'h00000000`
  - `out_k0 = 16'h0000`
  - `out_k1 = 16'h0000`
  - `out_y  = 16'h0000`
  - `out_s  = 32'h00000000`
- No architectural state is retained because the functional transform is stateless.
