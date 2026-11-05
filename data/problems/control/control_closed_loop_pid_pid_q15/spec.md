# Control Closed Loop PID Q15

This block implements a discrete PID controller with fixed sample time using signed fixed-point Q15 arithmetic. On each accepted input transaction it computes proportional, trapezoidal integral, and backward-difference derivative terms from the current error sample and retained controller state, applies integral and output saturation, then applies an output slew-rate limit. The controller state consists of the previous error, previous saturated-and-ramped output, and previous saturated integral term.

## Interface

### RTL interface

```systemverilog
module control_closed_loop_pid_pid_q15 (
    input  logic                clk,
    input  logic                rst,

    input  logic                in_valid,
    output logic                in_ready,
    input  logic signed [15:0]  in_error,
    input  logic signed [15:0]  in_p_gain,
    input  logic signed [15:0]  in_i_gain,
    input  logic signed [15:0]  in_d_gain,
    input  logic signed [15:0]  in_dt,
    input  logic signed [15:0]  in_output_ramp,
    input  logic signed [15:0]  in_limit,

    output logic                out_valid,
    input  logic                out_ready,
    output logic signed [15:0]  out_output
);
```

### Behavior interface

```python
class ControlClosedLoopPidPidQ15:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_error,
            in_p_gain,
            in_i_gain,
            in_d_gain,
            in_dt,
            in_output_ramp,
            in_limit):
        pass
```

Each call consumes one accepted input transaction and returns one `dict` with
exactly `out_output`. Arguments and returned values are Python integers in
0..65535 encoding signed Q15 port bits, not floating-point values. A zero output
is `{"out_output": 0}`, never `None` or an empty list. The previous error,
integral and output persist between calls. `reset()` clears all three values
without producing an output record.

## Modules

- `control_closed_loop_pid_pid_q15`: top-level PID controller with internal state registers `error_prev`, `output_prev`, and `integral_prev`.

## Functional Behavior

All data ports are signed 16-bit Q15 values. A raw integer `x` represents the real value `x * 2^-15`. Examples: `0x4000` = +0.5, `0x8000` = -1.0.

The block processes one input sample per accepted input transaction and produces one output transaction per accepted input transaction.

Arithmetic rules:

- Addition and subtraction on Q15 values are performed in a wider signed intermediate and are not truncated until the point explicitly stated.
- Q15 × Q15 multiplication produces a signed Q30 intermediate.
- Conversion from Q30 to Q15 is by arithmetic right shift by 15 bits, truncating toward negative infinity.
- Every externally visible Q15 result and every retained state value is saturated to the signed 16-bit range `[-32768, 32767]` when assigned.
- The division used by the derivative term is signed integer division on the represented real values with a Q15 result. For signed numerator `n_q30` and signed denominator `d_q15`, the quotient is `q_q15 = trunc_toward_zero(n_q30 / d_q15)`. If `d_q15 == 0`, the quotient is defined as 0 before any later saturation.

For one accepted input transaction, let:

- `error` = signed Q15 value from `in_error`
- `P` = signed Q15 value from `in_p_gain`
- `I` = signed Q15 value from `in_i_gain`
- `D` = signed Q15 value from `in_d_gain`
- `dt` = signed Q15 value from `in_dt`
- `output_ramp` = signed Q15 value from `in_output_ramp`
- `limit` = signed Q15 value from `in_limit`
- `error_prev`, `output_prev`, `integral_prev` = retained signed Q15 state from the previous accepted input transaction

The computation order is:

1. Proportional term  
   `proportional_q30 = P * error`  
   `proportional = proportional_q30 >>> 15`

2. Integral term using trapezoidal integration  
   `sum_error = error + error_prev` in signed integer arithmetic  
   `i_dt_q30 = I * dt`  
   `i_dt_half_q15 = (i_dt_q30 >>> 16)`  
   This implements `(I * dt * 0.5)` in Q15 by one extra right shift.  
   `integral_delta_q30 = i_dt_half_q15 * sum_error`  
   `integral_delta = integral_delta_q30 >>> 15`  
   `integral_unsat = integral_prev + integral_delta`

3. Integral saturation  
   The integral state is always limited symmetrically by `limit`, regardless of the sign or magnitude of `limit`.  
   First compute the positive bound:
   - if `limit >= 0`, `limit_pos = limit`
   - else `limit_pos = -limit`, except `-(-32768)` is clamped to `32767`
   
   Then `integral` is:
   - `+limit_pos` if `integral_unsat > +limit_pos`
   - `-limit_pos` if `integral_unsat < -limit_pos`
   - otherwise `integral_unsat`

4. Derivative term  
   `error_delta = error - error_prev`  
   `d_num_q30 = D * error_delta`  
   If `dt == 0`, `derivative = 0`.  
   Otherwise `derivative = trunc_toward_zero(d_num_q30 / dt)` with a signed Q15 result.  
   After division, saturate `derivative` to `[-32768, 32767]`.

5. Output sum before output saturation  
   `output_unsat = proportional + integral + derivative`

6. Output saturation  
   Use the same symmetric bound `limit_pos` derived in step 3.  
   `output_limited` is:
   - `+limit_pos` if `output_unsat > +limit_pos`
   - `-limit_pos` if `output_unsat < -limit_pos`
   - otherwise `output_unsat`

7. Slew-rate limit  
   The ramp limit is enabled only when `output_ramp > 0` and `dt > 0`. If either condition is false, `output = output_limited`.

   When enabled:
   - `output_step = output_limited - output_prev`
   - `output_rate_q30 = output_step << 15`  
     This is the Q30 numerator corresponding to `output_step / dt`.
   - `output_rate = trunc_toward_zero(output_rate_q30 / dt)` in signed Q15
   - `ramp_step_q30 = output_ramp * dt`
   - `ramp_step = ramp_step_q30 >>> 15`

   Then:
   - if `output_rate > output_ramp`, `output = output_prev + ramp_step`
   - else if `output_rate < -output_ramp`, `output = output_prev - ramp_step`
   - else `output = output_limited`

   After this selection, saturate `output` to `[-32768, 32767]`.

8. State update  
   After the output value for the transaction is determined:
   - `integral_prev := integral`
   - `output_prev := output`
   - `error_prev := error`

9. Output transaction  
   The module emits one output transaction with `out_output = output`.

Edge cases:

- `dt == 0`: derivative is 0 and slew-rate limiting is disabled.
- `dt < 0`: derivative uses the signed division rule above; slew-rate limiting is disabled.
- `output_ramp <= 0`: slew-rate limiting is disabled.
- `limit == 0`: both integral and output are forced to 0 after their respective saturation steps.
- `limit < 0`: the saturation bound uses `abs(limit)` as defined in step 3.
- All saturations compare against inclusive signed bounds exactly as written above.

Worked example for the integral scaling: if `I = 0x4000` (+0.5), `dt = 0x4000` (+0.5), and `error + error_prev = 0x4000` (+0.5), then `i_dt_q30 = 0x10000000`, `i_dt_half_q15 = 0x1000` (+0.125 in Q15), `integral_delta_q30 = 0x04000000`, and `integral_delta = 0x0800` (+0.0625 in Q15).

## Reset Behavior

`rst` is active-high. Reset may be implemented synchronously or asynchronously; in either case, while reset is asserted the architectural state and output interface state are forced to their reset values.

Architectural state after reset:

- `error_prev = 0`
- `output_prev = 0`
- `integral_prev = 0`

Output port values after reset:

- `out_output = 0`
- `out_valid = 0`

Input-side readiness after reset:

- `in_ready = 1`
