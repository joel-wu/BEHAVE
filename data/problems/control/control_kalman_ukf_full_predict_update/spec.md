# Full-Covariance Position/Velocity Unscented Kalman Filter

Perform a complete predict-and-measurement-update step of a two-state UKF.
The state is position and velocity, the transition is f([a,b],dt)=[a+dt*b,b],
and the measurement is h([a,b])=a. Five Merwe sigma points use the full symmetric
covariance, including its off-diagonal entry. Prediction adds process noise and
regenerates sigma points before measurement update. Posterior mean and covariance
persist across accepted requests. This is a fixed numerical instance, not an
arbitrary-dimension or user-supplied-transition API.

## Interface

### RTL interface

```systemverilog
module control_kalman_ukf_full_predict_update (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_measurement,
    input logic [15:0] in_dt,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_x,
    output logic [95:0] out_p,
    output logic [63:0] out_gain,
    output logic [63:0] out_innovation,
    output logic [31:0] out_variance,
    output logic out_error
);
```

### Behavior interface

```python
class ControlKalmanUkfFullPredictUpdate:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_measurement, in_dt): ...
```

One `process(...)` call represents one accepted request and returns one
dictionary with exactly `out_x`, `out_p`, `out_gain`, `out_innovation`,
`out_variance`, and `out_error`. Arguments and results are nonnegative port
words; packed lanes are not separate Python lists or floating-point values.
An error returns the same six-field dictionary, with `out_error=1` and all other
fields zero, not `None`. Keep the same instance across requests. `reset()`
returns `None` and restores the computational state described below. Calls do
not represent internal square-root or transport cycles.

### Input and output values

`in_measurement` is signed 32-bit Q16.16; `in_dt` is unsigned with
16 fractional bits, representing [0,1). Every input bit pattern is legal.
Packed outputs place lane zero at the least-significant end:

| Output | Meaning |
| --- | --- |
| `out_x` | two signed 32-bit Q16.16 lanes: posterior position, velocity |
| `out_p` | three signed 32-bit Q16.16 lanes: P00, P01=P10, P11 |
| `out_gain` | two signed 32-bit Q16.16 lanes: K0, K1 |
| `out_innovation` | signed 64-bit with 16 fractional bits: measured minus predicted position |
| `out_variance` | positive signed-32-bit Q16.16 innovation variance S on success |
| `out_error` | 0: committed update; 1: finite numerical domain exceeded, no state change |

At a rising edge outside reset a request transfers iff `in_valid && in_ready`,
and a response transfers iff `out_valid && out_ready`. Hold an offered request
stable until accepted, and a valid response stable until consumed. Responses
are ordered, one per accepted request unless reset discards it. Latency is not
fixed. A request cannot cause a second update while stalled or awaiting output.

## Modules

`control_kalman_ukf_full_predict_update` holds the filter state and executes
both unscented transforms and the measurement update. Internal arithmetic
sharing, square-root implementation and buffering are implementation choices.

## Functional Behavior

All formulas below operate on decoded integer values; shifts and sums are exact.
R(n/d) means nearest-integer rounding with ties to even, including negative n;
every divisor shown is positive when evaluated. There is no intermediate
wrapping or saturation. The carried mean x and symmetric covariance P have
16 fractional bits. Reset sets x=[0,0] and P=diag(32768,32768), representing
diag(0.5,0.5). Process noise is Q=diag(512,512) and measurement noise Rnoise=512,
both representing 1/128 in covariance units.

The fixed Merwe parameters are n=2, alpha=1/2, beta=2, kappa=0. Thus the real
mean weights are [-3,1,1,1,1], covariance weights [-1/4,1,1,1,1], and the
upper Cholesky factor is that of P/2. These weights are exact, not clipped.

**Sigma points.** For any carried-format mean m and positive-definite covariance
C, compute det=C00*C11-C01*C01 and the following integer factor entries with
32 fractional bits. `isqrt(n)` is floor(sqrt(n)); `//` floors a nonnegative ratio.

```text
U00 = isqrt(C00 * 2^47)
U01 = sign(C01) * isqrt((C01*C01 * 2^47) // C00), with sign(0)=0
U11 = isqrt((det * 2^47) // C00).
```

The five points, with 32 fractional bits, are m*2^16 plus the ordered offsets
[(0,0),(U00,U01),(0,U11),(-U00,-U01),(0,-U11)]. Each factor entry is quantized from its exact
Cholesky expression, not by subtracting rounded factor squares.

For five such vectors v[i], define the mean and covariance conversion:

```text
mean(v)[j] = R((-3*v[0,j] + sum_{i=1..4} v[i,j]) / 2^16)
cross(a,b) = R((-a[0]*b[0] + 4*sum_{i=1..4} a[i]*b[i]) / 2^50).
```

Mean results and cross results have 16 fractional bits; a,b residuals have 32.
Always sum products before rounding. Cross covariances use the same operation.

**Prediction.** Generate X=sigma(x,P). For each point set
F[i,0]=X[i,0]+R(dt*X[i,1]/65536) and F[i,1]=X[i,1]. Set xp=mean(F),
d[i,j]=F[i,j]-xp[j]*2^16, and Pp[j,k]=cross(d[:,j],d[:,k])+Q[j,k].
Check that xp and Pp are representable and Pp is positive definite as specified
below. If so, generate a **new** set V=sigma(xp,Pp), including the added Q.

**Measurement update.** Let Z[i]=V[i,0] and zp=mean(Z). Form
e[i]=Z[i]-zp*2^16 and d[i,j]=V[i,j]-xp[j]*2^16. Compute

```text
S       = cross(e,e) + 512
Pxz[j]  = cross(d[:,j],e)
K[j]    = R(Pxz[j] * 65536 / S)
y       = measurement - zp
xn[j]   = R((xp[j]*65536 + K[j]*y) / 65536)
Pn[j,k] = R((Pp[j,k]*2^32 - K[j]*K[k]*S) / 2^32).
```

Divide by S directly; do not clip a reciprocal into a fractional-only format.
On success atomically commit x=xn and P=Pn, and return xn, Pn, K, y, S with
out_error=0. Pack signed values modulo their lane width. The next request uses
this posterior, never the intermediate prediction or the old diagonal alone.

**Finite numerical domain.** Both xp/Pp and xn/Pn must fit signed 32-bit lanes.
For each covariance require P00>0, P11>0 and P00*P11-P01^2>0. Also require
0<S<=2147483647 and both gain lanes to fit signed 32 bits. Check the prior before
regenerating points and S before division. If any check fails, return out_error=1
and zero for all other outputs, with **all carried state unchanged**. There is
no automatic jitter, clipping, diagonal replacement or partially committed
prediction. This rule makes every request defined, including an update outside
the finite representation. It does not assert such an update is valid in an
unbounded real-valued filter.

## Reset Behavior

`rst` is synchronous active-high and forces `in_ready=0` while asserted. At a
reset edge restore x=[0,0], P00=P11=32768 and P01=0; discard in-flight work and
unconsumed responses. Set out_valid=0 and all output data/error fields to zero.
The next accepted request starts from the reset posterior. Output data is otherwise unspecified
while out_valid=0.
