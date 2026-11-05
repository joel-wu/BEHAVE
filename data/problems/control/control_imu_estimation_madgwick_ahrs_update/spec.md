# ControlImuEstimationMadgwickAhrsUpdate

This block performs one stateful, fixed-point Madgwick gradient-descent IMU attitude update per accepted gyroscope/accelerometer sample. It retains quaternion integration, acceleration normalization, magnitude-dependent confidence, and final quaternion normalization. The arithmetic below defines an integer implementation, not floating-point bit equivalence.

## Interface

### RTL interface

```systemverilog
module control_imu_estimation_madgwick_ahrs_update (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [15:0] in_gx,
    input logic signed [15:0] in_gy,
    input logic signed [15:0] in_gz,
    input logic signed [15:0] in_ax,
    input logic signed [15:0] in_ay,
    input logic signed [15:0] in_az,
    input logic signed [15:0] in_dt,
    input logic signed [15:0] in_beta,
    input logic signed [15:0] in_acc_confidence_decay,
    output logic out_valid,
    input logic out_ready,
    output logic signed [15:0] out_q0,
    output logic signed [15:0] out_q1,
    output logic signed [15:0] out_q2,
    output logic signed [15:0] out_q3,
    output logic [15:0] out_accMagP
);
endmodule
```

### Behavior interface

```python
class ControlImuEstimationMadgwickAhrsUpdate:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_gx, in_gy, in_gz, in_ax, in_ay, in_az, in_dt, in_beta, in_acc_confidence_decay): ...
```

Each `process` call represents one accepted sample. Pass every argument as
an unsigned 16-bit integer encoding the signed Q15 value described below.
It returns one dictionary with exactly `out_q0`, `out_q1`, `out_q2`, `out_q3`,
and `out_accMagP`, all integers in 0..65535. The quaternion fields encode
signed Q15 results in two's complement; `out_accMagP` encodes an unsigned
value with 15 fractional bits. Return the full dictionary even when
acceleration is below the gate or the correction is zero, never `None` or
a list. Quaternion and filtered-magnitude state persist across calls.
Construction and `reset()` restore the state specified below; `reset()`
returns `None`. Physical idle or stalled cycles require no calls.

## Modules

- `control_imu_estimation_madgwick_ahrs_update`: stateful fixed-point IMU update and ordered ready/valid transport.

## Functional Behavior

Inputs transfer on rising edges with `in_valid && in_ready`; outputs transfer with `out_valid && out_ready`. Inputs and outputs remain stable while their valid is asserted and ready is low. Results are ordered, exactly one per accepted input. State advances once per input transfer, never while stalled. Latency and internal scheduling are not prescribed. Reset cancels pending work.

### Arithmetic and normalization

Let `S = 32768`. All input words and quaternion outputs are signed 16-bit Q15:
raw integer `v` represents `v/S`. Gyroscope rates use radians/second,
acceleration uses g, and `dt` uses seconds. Every input bit pattern is defined;
negative gains or timesteps follow the same arithmetic, not a separate
physical-model promise. The filtered magnitude `accMagP` is **unsigned**
16-bit with 15 fractional bits, so magnitudes above one g are representable.

In the equations below, `mul(a,b) = floor(a*b/S)` uses a full signed product.
`>> k` means floor division by `2**k`, including for negative operands;
`// 10` is floor division. `clip(x,lo,hi)` clamps to the inclusive endpoints.
Other products, sums, shifts and doubled/fourfold/eightfold values are wide
integers: they do not saturate or wrap. Only the stated clamps narrow values.

`root96(n) = floor(sqrt(n))` for nonnegative integers (all uses fit 96 bits).
Vector normalization uses 16 extra fractional bits in the length:

```text
norm_den(a,b,c,d) = root96((a*a + b*b + c*c + d*d) << 32)
unit(v,den) = 0                                    if den = 0
              clip(trunc_zero((v << 31)/den),-32768,32767) otherwise
```

The length sum is not clipped to Q15. The quotient truncates toward zero;
there is no separately saturated reciprocal. A zero correction vector gives
zero correction; an all-zero integrated quaternion falls back to identity.
Positive unit quaternion components use 32767 because +1 is outside signed
16-bit Q15. In particular, zero gyro and axial positive acceleration preserve
the reset quaternion, without special-casing that input sample.

### State update

Read all state and inputs before these equations. All right-hand names without
`in_` denote their signed input values or current state. Each assignment is
executed in the written order, and each accepted input produces one output.
The acceleration gate `ax*ax+ay*ay+az*az > 107374` is the integer form of
`||a|| > 0.01 g`. Below it, the magnitude filter and integral state (if present)
are held and only gyro integration is performed.

```text
d0 = (-mul(q1,gx) - mul(q2,gy) - mul(q3,gz)) >> 1
d1 = (mul(q0,gx) + mul(q2,gz) - mul(q3,gy)) >> 1
d2 = (mul(q0,gy) - mul(q1,gz) + mul(q3,gx)) >> 1
d3 = (mul(q0,gz) + mul(q1,gy) - mul(q2,gx)) >> 1
acc2 = ax*ax + ay*ay + az*az
if acc2 > 107374:
    accMagP = (9*accMagP + root96(acc2)) // 10
    delta = accMagP - 32768
    delta = -delta if delta < 0 else delta
    confidence = clip(32768 - mul(acc_confidence_decay,root96(delta << 15)),0,32768)
    den = norm_den(ax,ay,az,0)
    a = unit(ax,den)
    b = unit(ay,den)
    c = unit(az,den)
    q00 = mul(q0,q0)
    q11 = mul(q1,q1)
    q22 = mul(q2,q2)
    q33 = mul(q3,q3)
    s0 = mul(4*q0,q22) + mul(2*q2,a) + mul(4*q0,q11) - mul(2*q1,b)
    s1 = mul(4*q1,q33) - mul(2*q3,a) + mul(4*q00,q1) - mul(2*q0,b) - 4*q1 + mul(8*q1,q11) + mul(8*q1,q22) + mul(4*q1,c)
    s2 = mul(4*q00,q2) + mul(2*q0,a) + mul(4*q2,q33) - mul(2*q3,b) - 4*q2 + mul(8*q2,q11) + mul(8*q2,q22) + mul(4*q2,c)
    s3 = mul(4*q11,q3) - mul(2*q1,a) + mul(4*q22,q3) - mul(2*q2,b)
    den = norm_den(s0,s1,s2,s3)
    bc = mul(beta,confidence)
    d0 = d0 - mul(bc,unit(s0,den))
    d1 = d1 - mul(bc,unit(s1,den))
    d2 = d2 - mul(bc,unit(s2,den))
    d3 = d3 - mul(bc,unit(s3,den))
n0 = q0 + mul(d0,dt)
n1 = q1 + mul(d1,dt)
n2 = q2 + mul(d2,dt)
n3 = q3 + mul(d3,dt)
den = norm_den(n0,n1,n2,n3)
q0 = unit(n0,den) if den != 0 else 32767
q1 = unit(n1,den)
q2 = unit(n2,den)
q3 = unit(n3,den)
```

Commit `q0..q3`, `accMagP` as the next architectural state. Each output named `out_X` carries the new value of state `X`. No intermediate gradient, corrected gyro or unnormalized quaternion is clipped to 16 bits.

## Reset Behavior

Synchronous active-high `rst` has priority over input and output transfer. It initializes `q = (32767,0,0,0)`, `accMagP = 32768`. Pending output is discarded and `out_valid` is low. Output data registers take the corresponding initial-state values. No transaction occurs during reset. The next accepted sample starts from this state.
