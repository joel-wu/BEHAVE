# Trapezoidal motion planning and sample evaluation

Plan an accelerate/coast/decelerate move under the supplied kinematic bounds and evaluate its position, velocity and acceleration at a requested time.

## Interface

### RTL interface

```systemverilog
module control_motion_trapezoidal_motion_profile (
    input logic clk,rst,in_valid,
    output logic in_ready,
    input logic [31:0] in_xf,
    input logic [31:0] in_xi,
    input logic [31:0] in_vi,
    input logic [31:0] in_vmax,
    input logic [31:0] in_amax,
    input logic [31:0] in_dmax,
    input logic [31:0] in_t,
    input logic out_ready,
    output logic out_valid,
    output logic [31:0] out_y,
    output logic [31:0] out_yd,
    output logic [31:0] out_ydd,
    output logic [31:0] out_ta,
    output logic [31:0] out_tv,
    output logic [31:0] out_td,
    output logic [31:0] out_tf,
    output logic [0:0] out_error
);
```

### Behavior interface

```python
class ControlMotionTrapezoidalMotionProfile:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_xf, in_xi, in_vi, in_vmax, in_amax, in_dmax, in_t): ...
```

One call represents one accepted planning request. Pass all seven inputs as
unsigned 32-bit binary32 encodings, not Python floats. Every call returns one
dictionary with exactly `out_y`, `out_yd`, `out_ydd`, `out_ta`, `out_tv`, `out_td`,
`out_tf` and `out_error`. The first seven values are unsigned 32-bit encodings;
`out_error` is 0 or 1. Invalid inputs still return the complete error record
defined below, never `None` or a list. Plans are not retained between calls.
Construction and `reset()` establish the same stateless model; `reset()` returns
`None` without emitting a record.

## Modules

One transaction plans the full ODrive-style accelerate/coast/decelerate move and evaluates it at time t. Plans are not retained across transactions.

## Functional Behavior

All numerical inputs/outputs are raw binary32 words, not saturated Q15 fractions. xf/xi are final/initial position, vi initial velocity, vmax/amax/dmax positive kinematic bounds, and t sample time. This restores the source's time and displacement range: a one-second stage is not clamped below one. The finite arithmetic profile below fixes evaluation order.

Write A(a,b), M(a,b), D(a,b), S(a,b) for separately rounded add, multiply, divide and subtract, and sq(a)=M(a,a). Sign changes toggle the sign bit, including signed zero; sign(v) is -1 for a set sign bit and +1 otherwise. Using the raw binary32 input values:

1. dx=S(xf,xi), stop=D(sq(vi),M(2,dmax)), dstop=copysign(stop,vi), s=sign(S(dx,dstop)). Set ar=s*amax, dr=-s*dmax, vr=s*vmax (these sign products are exact). If s*vi>s*vr, negate ar: this retains the source's double-deceleration move.
2. ta=D(S(vr,vi),ar), td=D(-vr,dr), minimum=A(M(M(0.5,ta),A(vr,vi)),M(M(0.5,td),vr)). If s*dx<s*minimum, use a triangular profile: rad=D(A(M(dr,sq(vi)),M(M(M(2,ar),dr),dx)),S(dr,ar)); replace rad<0 by +0; vr=s*sqrt(rad); ta=max(D(S(vr,vi),ar),+0), td=max(D(-vr,dr),+0), tv=+0. Otherwise rad=+0 and tv=D(S(dx,minimum),vr). Each max replaces strictly negative values only.
3. ta_tv=A(ta,tv), tf=A(ta_tv,td), end_accel=A(A(xi,M(vi,ta)),M(M(0.5,ar),sq(ta))). Return ta,tv,td,tf as out_ta/out_tv/out_td/out_tf.
4. For t<0, return (y,yd,ydd)=(xi,vi,+0). For 0<=t<ta, return (A(A(xi,M(vi,t)),M(M(0.5,ar),sq(t))),A(vi,M(ar,t)),ar). For ta<=t<ta_tv, return (A(end_accel,M(vr,S(t,ta))),vr,+0). For ta_tv<=t<tf, put e=S(t,tf) and return (A(xf,M(M(0.5,dr),sq(e))),M(dr,e),dr). Otherwise return (xf,+0,+0). Comparisons are numeric, so phase boundaries belong to the following phase.

Reject nonfinite inputs and kinematic bounds that are not strictly positive. Otherwise execute the stated assignments and replacements before checking the final values of the named plan quantities dx/stop/dstop/ar/dr/vr/minimum/rad/ta/tv/td/tf/end_accel and the evaluated y/yd/ydd. If an input check fails or any checked quantity is nonfinite, return out_error=1 and all seven numeric outputs zero; otherwise out_error=0. There is no additional intermediate-overflow trap: for example, the specified rad<0 replacement also replaces negative infinity with positive zero.

### Numerical contract

All arithmetic operands are raw binary32 encodings. Each stated operation
rounds separately to binary32, round-to-nearest with ties to even. Subnormal
operands retain their numerical value and underflow is gradual, including the
zero/subnormal and subnormal/normal rounding boundaries. Overflow gives signed
infinity. No fused operation or reassociation may change the stated rounding
points; compare output encodings exactly, without a tolerance.

An arithmetic NaN operand or invalid operation produces `0x7FC00000`; this
sign/payload is a project convention. Invalid operations include opposite
infinities added, equal infinities subtracted, zero times infinity, zero/zero,
infinity/infinity, and sqrt of a negative nonzero value. Multiplication and
division XOR the operand signs, including zero and infinity results.
Finite nonzero/zero gives signed infinity; finite/infinity gives signed zero.
Adding two negative zeros gives negative zero; other exact cancellation gives
positive zero. Square root preserves signed zero and positive infinity.
Ordered comparisons with NaN are false; both zeros compare equal, and
subnormal values are compared without flushing. No exception flags or dynamic
rounding modes are required.

Square root is correctly rounded binary32 under the same RNE/gradual-underflow profile. One accepted input produces one complete eight-field output record. Transfers occur on rising edges with valid and ready high. Output valid/payload remain stable while stalled.

## Reset Behavior

Synchronous active-high reset discards pending output, clears out_valid and all output fields. in_ready=1 after reset. Numerical state is transaction-local; idle payloads are zero.
