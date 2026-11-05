# ControlImuEstimationMahonyAhrsUpdate

This block performs one stateful, fixed-point Mahony proportional/integral IMU attitude update per accepted gyroscope/accelerometer sample. It retains quaternion integration, acceleration normalization, magnitude-dependent confidence, and final quaternion normalization. This variant stores and reports signed Q31 integral feedback. The arithmetic below defines an integer implementation, not floating-point bit equivalence.

## Interface

### RTL interface

```systemverilog
module control_imu_estimation_mahony_ahrs_update (
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
    input logic signed [15:0] in_kp,
    input logic signed [15:0] in_ki,
    input logic signed [15:0] in_acc_confidence_decay,
    output logic out_valid,
    input logic out_ready,
    output logic signed [15:0] out_q0,
    output logic signed [15:0] out_q1,
    output logic signed [15:0] out_q2,
    output logic signed [15:0] out_q3,
    output logic signed [31:0] out_integralFBx,
    output logic signed [31:0] out_integralFBy,
    output logic signed [31:0] out_integralFBz,
    output logic [15:0] out_accMagP
);
endmodule
```

### Behavior interface

```python
class ControlImuEstimationMahonyAhrsUpdate:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_gx, in_gy, in_gz, in_ax, in_ay, in_az, in_dt, in_kp, in_ki, in_acc_confidence_decay): ...
```

Each `process` call represents one accepted sample. Pass all arguments as
unsigned 16-bit integers encoding signed Q15 values. It returns one dictionary
with exactly `out_q0`, `out_q1`, `out_q2`, `out_q3`, `out_integralFBx`,
`out_integralFBy`, `out_integralFBz`, and `out_accMagP`. Quaternion values are
unsigned 16-bit encodings of signed Q15 results; integral values are unsigned
32-bit encodings of signed Q31 results. `out_accMagP` is unsigned 16-bit with
15 fractional bits. Always return all eight fields, even below the acceleration
gate or with zero correction, never `None` or a list. Quaternion, magnitude
and integral state persist between calls. Construction and `reset()` restore
the state specified below; `reset()` returns `None`. Physical idle or stalled
cycles require no calls.

## Modules

- `control_imu_estimation_mahony_ahrs_update`: stateful fixed-point IMU update and ordered ready/valid transport.

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

Integral state `integralFBx/y/z` is signed Q31 (32-bit). Its only saturation points are the three explicit accumulator clamps below.

```text
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
    vx = mul(q1,q3) - mul(q0,q2)
    vy = mul(q0,q1) + mul(q2,q3)
    vz = mul(q0,q0) - 16384 + mul(q3,q3)
    ex = mul(b,vz) - mul(c,vy)
    ey = mul(c,vx) - mul(a,vz)
    ez = mul(a,vy) - mul(b,vx)
    pk = mul(2*kp,confidence)
    ik = mul(2*ki,confidence)
    if ik > 0:
        integralFBx = clip(integralFBx + ((ik*ex*dt) >> 14),-2147483648,2147483647)
        integralFBy = clip(integralFBy + ((ik*ey*dt) >> 14),-2147483648,2147483647)
        integralFBz = clip(integralFBz + ((ik*ez*dt) >> 14),-2147483648,2147483647)
    else:
        integralFBx = 0
        integralFBy = 0
        integralFBz = 0
    gx = gx + (integralFBx >> 16) + mul(pk,ex)
    gy = gy + (integralFBy >> 16) + mul(pk,ey)
    gz = gz + (integralFBz >> 16) + mul(pk,ez)
hx = (gx*dt) >> 16
hy = (gy*dt) >> 16
hz = (gz*dt) >> 16
n0 = q0 - mul(q1,hx) - mul(q2,hy) - mul(q3,hz)
n1 = q1 + mul(q0,hx) + mul(q2,hz) - mul(q3,hy)
n2 = q2 + mul(q0,hy) - mul(q1,hz) + mul(q3,hx)
n3 = q3 + mul(q0,hz) + mul(q1,hy) - mul(q2,hx)
den = norm_den(n0,n1,n2,n3)
q0 = unit(n0,den) if den != 0 else 32767
q1 = unit(n1,den)
q2 = unit(n2,den)
q3 = unit(n3,den)
```

Commit `q0..q3`, `accMagP` and `integralFBx/y/z` as the next architectural state. Each output named `out_X` carries the new value of state `X`. No intermediate gradient, corrected gyro or unnormalized quaternion is clipped to 16 bits.

## Reset Behavior

Synchronous active-high `rst` has priority over input and output transfer. It initializes `q = (32767,0,0,0)`, `accMagP = 32768` and all three integral terms to zero. Pending output is discarded and `out_valid` is low. Output data registers take the corresponding initial-state values. No transaction occurs during reset. The next accepted sample starts from this state.
