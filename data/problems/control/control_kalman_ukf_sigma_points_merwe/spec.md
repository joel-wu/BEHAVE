# Two-State Merwe Scaled Sigma Points

Generate the five Van der Merwe sigma points and their mean/covariance weights
for a two-state mean and a full symmetric positive-definite covariance. Use the
upper-triangular Cholesky orientation and return points in source order.
Negative central weights and beta=2 must be representable; they are not clipped.
This is the sigma-point helper, not the complete UKF prediction/update.

## Interface

### RTL interface

```systemverilog
module MerweSigmaPoints (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [31:0] in_x0, in_x1,
    input logic signed [31:0] in_p00, in_p01, in_p10, in_p11,
    input logic [15:0] in_alpha,
    input logic signed [15:0] in_beta, in_kappa,
    output logic out_valid,
    input logic out_ready,
    output logic signed [63:0] out_sigma0_0,
    output logic signed [63:0] out_sigma0_1,
    output logic signed [63:0] out_sigma1_0,
    output logic signed [63:0] out_sigma1_1,
    output logic signed [63:0] out_sigma2_0,
    output logic signed [63:0] out_sigma2_1,
    output logic signed [63:0] out_sigma3_0,
    output logic signed [63:0] out_sigma3_1,
    output logic signed [63:0] out_sigma4_0,
    output logic signed [63:0] out_sigma4_1,
    output logic signed [63:0] out_wm0,
    output logic signed [63:0] out_wm1,
    output logic signed [63:0] out_wm2,
    output logic signed [63:0] out_wm3,
    output logic signed [63:0] out_wm4,
    output logic signed [63:0] out_wc0,
    output logic signed [63:0] out_wc1,
    output logic signed [63:0] out_wc2,
    output logic signed [63:0] out_wc3,
    output logic signed [63:0] out_wc4,
    output logic out_error
);
```

### Behavior interface

```python
class MerweSigmaPoints:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_x0, in_x1, in_p00, in_p01, in_p10, in_p11,
                in_alpha, in_beta, in_kappa): ...
```

Each call represents one accepted request and returns exactly one dictionary
with 21 keys: `out_sigma0_0`, `out_sigma0_1`, `out_sigma1_0`, `out_sigma1_1`,
`out_sigma2_0`, `out_sigma2_1`, `out_sigma3_0`, `out_sigma3_1`, `out_sigma4_0`,
`out_sigma4_1`, `out_wm0` through `out_wm4`, `out_wc0` through `out_wc4`, and
`out_error`. The 20 numerical values are unsigned 64-bit port words encoding
the signed formats below; `out_error` is an unsigned one-bit value. Arguments
use the signed or unsigned integer values declared below. An invalid request
returns this same complete dictionary with zero numerical fields and
`out_error=1`, not `None` or an exception. No waiting-cycle calls are needed.
There is no cross-request state; `reset()` returns `None` and emits no response.

### Numeric interface

Mean/covariance inputs are signed 32-bit integers with 16 fractional bits. Alpha is unsigned with 16
fractional bits; beta and kappa are signed 16-bit integers with 8 fractional
bits. Each sigma-coordinate output is signed 64-bit with 32 fractional bits;
each weight output is signed 64-bit with 24 fractional bits. For example,
alpha=0x8000 is 0.5, beta=0x0200 is 2, and weight -3 is
0xfffffffffd000000. Wider weights are essential for small alpha.

One request transfers at an active rising edge on in_valid && in_ready; one
ordered response transfers on out_valid && out_ready. Hold each offered request
and each valid response stable while stalled. Latency is implementation-defined;
there is one response per accepted request unless reset discards it.

## Modules

`MerweSigmaPoints` computes a finite two-dimensional numerical helper. There is
no cross-request filter state or covariance memory. No diagonal approximation,
silent symmetry repair or saturation of an otherwise valid point/weight is used.

## Functional Behavior

The valid domain requires alpha>0, 0<=beta<=4, -1<=kappa<=4, p00>0, p11>0,
p01=p10, and p00*p11-p01*p01>0, evaluated in exact decoded values. All other
requests return out_error=1 and zero for every point and weight. Valid requests
return out_error=0. This makes singular, asymmetric and indefinite covariances
explicit errors instead of silently emitting degenerate points.

Let n=2, s=alpha^2*(2+kappa), lambda=s-2. Define the real upper factor

    U00 = sqrt(s*p00)
    U01 = sign(p01)*sqrt(s*p01^2/p00)
    U11 = sqrt(s*(p00*p11-p01^2)/p00).

Thus U=[[U00,U01],[0,U11]] and U^T U=s*P. Quantize each component independently
toward zero to 32 fractional bits. In particular, U11 uses the full determinant,
not a rounded or diagonal-only substitute. Write these integers as u00,u01,u11,
and promote the mean integers by shifting them left 16 bits to m0,m1. Emit:

    sigma0 = (m0,     m1)
    sigma1 = (m0+u00, m1+u01)
    sigma2 = (m0,     m1+u11)
    sigma3 = (m0-u00, m1-u01)
    sigma4 = (m0,     m1-u11).

The exact real weights are Wm0=lambda/s, Wc0=lambda/s+1-alpha^2+beta,
and Wmi=Wci=1/(2*s) for i=1..4. Quantize each weight independently toward zero
to 24 fractional bits. Wc0 is rounded from its complete real expression,
not formed by adding to an already-rounded Wm0. No intermediate is saturated.

For bit-exact implementation, use the decoded raw integers a,b,k and covariance
words A=p00, B=p01, C=p11. Set d=a*a*(512+k) and D=A*C-B*B. Then

    u00 = isqrt(d*A*256)
    u01 = sign(B)*isqrt(floor(d*B*B*256/A))
    u11 = isqrt(floor(d*D*256/A))
    wm0 = trunc((d-2^41)*2^24/d)
    wi  = floor(2^63/d)                     (i=1..4, both weight kinds)
    wc0 = trunc(((2*d-2^41)*2^32-d*a*a+d*b*2^24)/(d*256)).

isqrt(z) is the largest nonnegative integer r with r*r<=z; trunc is signed
division toward zero. Wide integer arithmetic is required before the divisions.
All valid root radicands fit 84 bits, intermediate numerators fit signed 128
bits, and every output fits its declared signed 64-bit format.

The five quantized mean weights sum to one within 4*2^-24. Each factor component
differs from the real Cholesky factor by less than 2^-32; the symmetric offsets
are retained exactly. Small alpha amplifies factor quantization when recovering
covariance, so floating-point bit equivalence is not claimed.

Example: x=(1,-2), P=[[4,2],[2,5]], alpha=0.5, beta=2, kappa=0 yields s=0.5,
Wm0=-3, Wc0=-0.25 and every other weight 1. Its correlated upper factor has
U00=sqrt(2), U01=1/sqrt(2), U11=sqrt(2), not a diagonal-only factor.

## Reset Behavior

Reset is synchronous active-high. A reset edge clears the pending computation
and response, clears out_valid and zeroes all output words. in_ready is zero
during reset. Output data are otherwise
unspecified when out_valid=0.
