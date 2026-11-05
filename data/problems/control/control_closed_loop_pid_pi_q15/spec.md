# Fixed-Sample PID Q15

This block performs a complete discrete PID update: proportional response, clamped Tustin integration, backward-difference derivative, final output limiting, and optional slew-rate limiting. It retains the previous error, integral and output. One accepted input transaction produces exactly one output transaction.

## Interface

### RTL interface

```systemverilog
module fixed_sample_pid_q15 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [15:0] in_error,
    input logic signed [15:0] in_p,
    input logic signed [15:0] in_i,
    input logic signed [15:0] in_d,
    input logic signed [15:0] in_output_ramp,
    input logic signed [15:0] in_limit,
    output logic out_valid,
    input logic out_ready,
    output logic signed [15:0] out_output
);
```

### Behavior interface

```python
class FixedSamplePidQ15:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_error, in_p, in_i, in_d, in_output_ramp, in_limit): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_output": ...}`, whose value is a Python integer holding the 16-bit
output port pattern.

### Transaction protocol

State advances only on an accepted input (valid and ready high at a rising edge). Outputs are ordered and held unchanged while valid and not ready. Simultaneous output consumption and input acceptance is legal.

## Modules

- `fixed_sample_pid_q15`: complete fixed-point PID controller.

## Functional Behavior

Error, P/I/D gains, output ramp, configured limit and output are signed 16-bit values with 15 fractional bits (raw/32768). All input bit patterns are defined. A nonnegative limit enables symmetric clamping to [-limit,limit], including limit zero. A negative limit disables that configured clamp; the signed output range [-32768,32767] still applies. A positive ramp enables the rate limit; zero or negative ramp disables it.

The sample interval is fixed at 1/1024 second: `dt_num=1`, `dt_den=1024`.

Let `T(n,d)` be signed integer division truncated toward zero; every denominator below is positive. Let `clip(v)` apply the configured limit, or the signed 16-bit range when that limit is disabled. Initially `e_prev=I_prev=y_prev=0`. For each accepted request decode `e=in_error`, `P=in_p`, `I=in_i`, `D=in_d`, then compute in widened signed arithmetic:
```text
proportional = T(P * e, 32768)
increment    = T(I * (e + e_prev) * dt_num, 65536 * dt_den)
integral     = clip(I_prev + increment)
derivative   = T(D * (e - e_prev) * dt_den, 32768 * dt_num)
requested    = clip(proportional + integral + derivative)
```
Do not truncate or saturate `e+e_prev`, `e-e_prev`, the intermediate products, or individual P/D terms. Only the divisions shown quantize the computation. The widened intermediates fit signed 64 bits for the declared inputs.

If ramp is positive, set `step=floor(ramp*dt_num/dt_den)` and clamp `requested` to `[y_prev-step,y_prev+step]`; otherwise output `requested`. Save the resulting output as `y_prev`, save `integral` as `I_prev`, and save `e` as `e_prev`. If a configured limit is reduced, slew limiting may temporarily leave the output outside the new configured interval while it approaches that interval; the integral is already clamped. The signed 16-bit output range is always preserved.

Small changes below one output unit can quantize to zero. This is an integer PID specification; no floating-point arithmetic, wall-clock measurement, or hidden sampling interval is used.

## Reset Behavior

Reset is synchronous active-high `rst`. It clears all three controller states, `out_output` and `out_valid`, discarding any pending response. No pre-reset error or integral affects subsequent requests.
