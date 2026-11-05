# control_kalman_kalman_1d_state_update

This block performs the update step of a scalar Kalman filter for a one-dimensional state and a one-dimensional measurement using fixed-point arithmetic. Each accepted input transaction provides the predicted state estimate, predicted covariance, measurement, measurement model, and measurement noise. The block computes the residual, innovation covariance, scalar Kalman gain, posterior state estimate, and posterior covariance using the Joseph stabilized covariance update. All state-like quantities that evolve over time are represented in signed Q31, and the scalar gain and measurement model are represented in signed Q15.

## Interface

### RTL interface

```systemverilog
module control_kalman_kalman_1d_state_update (
    input  logic               clk,
    input  logic               rst,

    input  logic               in_valid,
    output logic               in_ready,
    input  logic signed [31:0] in_x_prior,
    input  logic signed [31:0] in_p_prior,
    input  logic signed [31:0] in_z,
    input  logic signed [15:0] in_h,
    input  logic signed [31:0] in_r,

    output logic               out_valid,
    input  logic               out_ready,
    output logic signed [31:0] out_x_post,
    output logic signed [31:0] out_p_post,
    output logic signed [31:0] out_y,
    output logic signed [31:0] out_s,
    output logic signed [15:0] out_k
);
```

### Behavior interface

```python
class ControlKalmanKalman1dStateUpdate:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(self, in_x_prior, in_p_prior, in_z, in_h, in_r):
        pass
```

Each `process(...)` call represents one accepted input transaction and returns
one dictionary with exactly the keys `out_x_post`, `out_p_post`, `out_y`,
`out_s`, and `out_k`. Arguments and results are unsigned port bit patterns.
`in_h` and `out_k` are 16 bits; the other payload fields are 32 bits, with
the signed Q-format interpretations defined below. Every call returns all
five fields, including the zero-denominator case; it does not return `None`
or a list.

The model retains no filter state: every call supplies its own prior estimate
and covariance. `reset()` returns `None` and produces no record. Clock cycles,
stalls and output consumption are not additional model calls.

## Modules

- `control_kalman_kalman_1d_state_update`: top-level scalar Kalman update block.

## Functional Behavior

All arithmetic is signed two's-complement.

Formats:

- `in_x_prior`, `in_p_prior`, `in_z`, `in_r`, `out_x_post`, `out_p_post`, `out_y`, and `out_s` are signed Q31 values: real value = raw × 2^-31.
- `in_h` and `out_k` are signed Q15 values: real value = raw × 2^-15.

The block implements the scalar update equations

- `y = z - h*x_prior`
- `s = h*p_prior*h + r`
- `k = p_prior*h / s`
- `x_post = x_prior + k*y`
- `p_post = (1 - k*h)^2 * p_prior + k^2 * r`

with the fixed-point rules below.

### 1. Q-format helper operations

For a signed Q31 value `a_q31` and signed Q15 value `b_q15`:

- `mul_q31_q15(a_q31, b_q15)` produces signed Q31:
  - form the exact integer product `prod = a_q31 * b_q15`
  - divide by `2^15` with truncation toward zero
  - saturate the result to the signed 32-bit range `[-2147483648, 2147483647]`

For two signed Q15 values `a_q15` and `b_q15`:

- `mul_q15_q15(a_q15, b_q15)` produces signed Q15:
  - form the exact integer product `prod = a_q15 * b_q15`
  - divide by `2^15` with truncation toward zero
  - saturate the result to the signed 16-bit range `[-32768, 32767]`

For two signed Q31 values `a_q31` and `b_q31`:

- `mul_q31_q31(a_q31, b_q31)` produces signed Q31:
  - form the exact integer product `prod = a_q31 * b_q31`
  - divide by `2^31` with truncation toward zero
  - saturate the result to the signed 32-bit range `[-2147483648, 2147483647]`

For signed division of a Q31 numerator by a Q31 denominator to produce Q15:

- `div_q31_to_q15(num_q31, den_q31)`:
  - if `den_q31 == 0`, return `32767` when `num_q31 >= 0`, else return `-32768`
  - otherwise compute `quot = (num_q31 << 15) / den_q31`
  - the division truncates toward zero
  - saturate the result to the signed 16-bit range `[-32768, 32767]`

Subtraction and addition on Q31 values are performed on mathematical integers and then saturated to the signed 32-bit range `[-2147483648, 2147483647]`. No wraparound is permitted on outputs of the defined steps below.

The internal constant `one_q15 = 32768` uses a widened signed representation. The Joseph factor and its square are also widened; they are not clamped to signed16.

### 2. Residual

Compute the predicted measurement in Q31:

- `hx_q31 = mul_q31_q15(in_x_prior, in_h)`

Compute the residual in Q31:

- `y_q31 = sat32(in_z - hx_q31)`

Drive `out_y = y_q31`.

### 3. Innovation covariance

Compute the intermediate product in Q31:

- `ph_q31 = mul_q31_q15(in_p_prior, in_h)`

Compute the projected covariance term in Q31:

- `hph_q31 = mul_q31_q15(ph_q31, in_h)`

Compute the innovation covariance:

- `s_q31 = sat32(hph_q31 + in_r)`

Drive `out_s = s_q31`.

### 4. Kalman gain

Compute the gain numerator in Q31:

- `k_num_q31 = ph_q31`

Compute the scalar gain in Q15:

- `k_q15 = div_q31_to_q15(k_num_q31, s_q31)`

Drive `out_k = k_q15`.

If `s_q31 == 0`, the saturated sentinel from `div_q31_to_q15` is the required result. No special bypass is applied to any other output.

### 5. Posterior state estimate

Compute the correction term in Q31:

- `ky_q31 = mul_q31_q15(y_q31, k_q15)`

Compute the posterior state:

- `x_post_q31 = sat32(in_x_prior + ky_q31)`

Drive `out_x_post = x_post_q31`.

### 6. Posterior covariance using Joseph form

Compute the Q15 factor:

- `kh_q15 = mul_q15_q15(k_q15, in_h)`

Compute the Q15 factor:

- `i_minus_kh_q15 = 32768 - kh_q15`

Compute its square in Q15:

- `i_minus_kh_sq_q15 = (i_minus_kh_q15 * i_minus_kh_q15) >> 15` (wide, nonnegative)

The following two multiplies accept widened Q15 factors, retain exact products, truncate toward zero by 15 bits, and only then saturate to Q31.

Compute the first covariance term in Q31:

- `term1_q31 = mul_q31_q15(in_p_prior, i_minus_kh_sq_q15)`

Compute the squared gain in Q15:

- `k_sq_q15 = (k_q15 * k_q15) >> 15` (wide, nonnegative)

Compute the second covariance term in Q31:

- `term2_q31 = mul_q31_q15(in_r, k_sq_q15)`

Compute the posterior covariance:

- `p_post_q31 = sat32(term1_q31 + term2_q31)`

Drive `out_p_post = p_post_q31`.

No explicit non-negativity clamp is applied to `out_p_post`; only the stated arithmetic saturation rules apply.

### 7. Edge-case behavior

- All inputs are accepted as arbitrary bit patterns in their declared widths.
- Negative covariance or negative measurement-noise inputs are processed exactly by the equations above; they are not rejected or clamped.
- If `in_h = 0` and `in_r != 0`, then `out_y = in_z`, `out_s = in_r`, `out_k = 0`, `out_x_post = in_x_prior`, and `out_p_post = in_p_prior`.
- If `in_h = 0` and `in_r = 0`, the required gain is the zero-denominator sentinel `32767`; all subsequent numbered update equations still apply. In particular, the posterior state need not equal the prior state.
- If `in_r = 0`, the Joseph covariance update still uses `term2_q31 = 0`.
- Every returned output is the raw unsigned port bit pattern of the signed result in the declared width.

Worked example anchor:

- `in_x_prior = 0x40000000` represents +0.5 in Q31.
- `in_h = 0x4000` represents +0.5 in Q15.
- `hx_q31 = mul_q31_q15(0x40000000, 0x4000) = 0x20000000`, which represents +0.25 in Q31.

## Reset Behavior

Reset is synchronous active-high. On any rising edge with `rst == 1`, all architectural state is set to zero.

This block has no architectural state beyond the handshake-visible output holding registers. After reset:

- `out_valid = 0`
- `out_x_post = 0`
- `out_p_post = 0`
- `out_y = 0`
- `out_s = 0`
- `out_k = 0`

No retained filter state exists inside the block.
