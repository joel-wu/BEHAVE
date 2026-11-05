# Control FOC Transform Fast Atan2 Approx

This block approximates atan2(y, x) using a min/max magnitude ratio, a polynomial, and quadrant correction. Cartesian inputs are signed 16-bit Q15; the signed 32-bit result uses Q29 radians so all four quadrants are representable. One accepted input transaction produces exactly one output transaction.

## Interface

### RTL interface

```systemverilog
module control_foc_transform_fast_atan2_approx (
    input  logic               clk,
    input  logic               rst,
    input  logic               in_valid,
    output logic               in_ready,
    input  logic signed [15:0] in_y,
    input  logic signed [15:0] in_x,
    output logic               out_valid,
    input  logic               out_ready,
    output logic signed [31:0] out_angle
);
```

### Behavior interface

```python
class ControlFocTransformFastAtan2Approx:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_y, in_x): ...
```

Each `process(in_y, in_x)` call represents one accepted input transaction.
Both arguments are unsigned 16-bit integers encoding the signed Q15 inputs.
Return one dictionary with exactly the key `out_angle`, not a list or `None`:

```python
{"out_angle": ...}
```

The value is an unsigned 32-bit integer encoding the signed Q29 result,
including for a negative angle or the zero vector. There is no persistent
algorithmic state. `reset()` returns `None` and produces no response.

## Modules

- `control_foc_transform_fast_atan2_approx`: Cartesian-pair to approximate-angle operator.

## Functional Behavior

Input and output transfers occur on rising edges with their respective valid and ready both high. A pending output remains valid and unchanged under backpressure. Inputs are accepted in order, including when a previous output is consumed.

All input bit patterns are legal. Decode `x` and `y` as signed 16-bit integers, representing raw/32768. Compute magnitudes exactly, including `abs(-32768) = 32768`. The zero vector returns zero.

For a nonzero vector, compute:
```text
a15 = floor((min(abs(x), abs(y)) * 32768) / max(abs(x), abs(y)))
s15 = floor(a15 * a15 / 32768)
a = a15 * 65536
s = s15 * 65536
```
Both `a` and `s` represent Q31 values in [0,1], including raw 2147483648. They are widened integers, not signed 32-bit values.

Let `mul(p,q) = trunc_toward_zero(p*q / 2147483648)`. Products and sums below are exact widened signed arithmetic; no intermediate wrap or saturation is applied. Use these integer constants:
```text
C3 = -99850786
C2 = 342124625
C1 = -703564562
PI_OVER_2 = 3373259426
PI = 6746518852
```
Evaluate in this order:
```text
r = mul(C3, s) + C2
r = mul(r, s) + C1
r = mul(r, s)
r = mul(r, a) + a
if abs(y) > abs(x): r = PI_OVER_2 - r
if x < 0:          r = PI - r
if y < 0:          r = -r
angle = trunc_toward_zero(r / 4)
```
The final division converts the widened Q31 angle to **Q29 radians**. The output is the signed 32-bit representation of `angle`, decoded as raw/536870912. Its range is [-1686629713, 1686629713], so no angular wrap or saturation is needed.

Axis examples (nonzero magnitude): positive x returns 0; positive y returns 843314856; negative x with y=0 returns 1686629713; negative y returns -843314856. These correspond approximately to 0, pi/2, pi and -pi/2 radians.

## Reset Behavior

Reset is synchronous active-high `rst`. A reset edge cancels any pending output and clears `out_valid` and `out_angle` to zero. There is no retained algorithmic state. After reset is released, the block can accept a new input.
