# ComplexQr2x2Householder

This module performs one complete unblocked QR factorization transaction for a fixed 2×2 complex matrix using the Householder-reflector sequence of LAPACK `ZGEQR2`: generate a reflector for column 0, apply its conjugate transpose from the left to the remaining trailing column, then generate a reflector for the final 1×1 column. Inputs and R use signed 16-bit integer components; reflector coefficients use signed Q3.13. Internal arithmetic uses scale 2^14 and explicit rounding, as defined below.

## Interface

### RTL interface

```systemverilog
module complex_qr2x2_householder (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_a00_re,
    input  logic [15:0]  in_a00_im,
    input  logic [15:0]  in_a01_re,
    input  logic [15:0]  in_a01_im,
    input  logic [15:0]  in_a10_re,
    input  logic [15:0]  in_a10_im,
    input  logic [15:0]  in_a11_re,
    input  logic [15:0]  in_a11_im,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_r00_re,
    output logic [15:0]  out_r00_im,
    output logic [15:0]  out_r01_re,
    output logic [15:0]  out_r01_im,
    output logic [15:0]  out_r10_re,
    output logic [15:0]  out_r10_im,
    output logic [15:0]  out_r11_re,
    output logic [15:0]  out_r11_im,
    output logic [15:0]  out_tau0_re,
    output logic [15:0]  out_tau0_im,
    output logic [15:0]  out_tau1_re,
    output logic [15:0]  out_tau1_im,
    output logic [15:0]  out_v10_re,
    output logic [15:0]  out_v10_im
);
```

### Behavior interface

```python
class ComplexQr2x2Householder:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
        self,
        in_a00_re, in_a00_im,
        in_a01_re, in_a01_im,
        in_a10_re, in_a10_im,
        in_a11_re, in_a11_im
    ):
        pass
```

The `process(...)` method returns exactly one dictionary for each accepted input transaction. The dictionary keys are `out_r00_re`, `out_r00_im`, `out_r01_re`, `out_r01_im`, `out_r10_re`, `out_r10_im`, `out_r11_re`, `out_r11_im`, `out_tau0_re`, `out_tau0_im`, `out_tau1_re`, `out_tau1_im`, `out_v10_re`, `out_v10_im`.

Arguments and returned values are unsigned 16-bit port words encoding the
signed component formats below. The dictionary always includes all 14 keys,
including structurally zero components and the all-zero matrix result; it is
not a nested matrix, a list or `None`. Calls represent accepted matrices, not
waiting cycles. No arithmetic state is carried between calls; `reset()` returns
`None` and emits no response.

## Modules

- `complex_qr2x2_householder`: top-level fixed-size 2×2 complex QR kernel.

## Functional Behavior

The eight inputs are signed 16-bit integer complex components of a 2x2 A.
Returned R components are signed 16-bit integers; tau0, tau1 and v10 use
signed 16-bit Q3.13. All output components saturate, not wrap. Internal
components use scale S=16384, without intermediate integer overflow.
`near(n/d)` rounds to nearest integer with ties away from zero; a zero
denominator gives zero. A complex scaled product computes its four real
products using near(product/S), then adds/subtracts these rounded terms.

For a scaled column (alpha,x), the Householder construction is:

1. If x=0 and imag(alpha)=0, beta=real(alpha), tau=v=0.
2. Otherwise beta is real: -floor(sqrt(|alpha|²+|x|²)) if real(alpha)>=0,
   or its positive counterpart otherwise. Compute the components of
   tau=S*(beta-alpha)/beta and v=S*x/(alpha-beta), each rounded with near.
   Do not round an unscaled reciprocal before multiplication.

Apply this to the first column of A*S, producing beta0,tau0,v10.
Set w=a01+conj(v10)*a11, z=conj(tau0)*w, r01=a01-z and
a11'=a11-v10*z using the scaled product above. Apply the same construction
to (a11',0) to obtain real beta1 and tau1. R is [[beta0,r01],[0,beta1]].
Return R after near(component/S) and tau/v after near(component/2), each
saturated to [-32768,32767]. This retains fractional reflector coefficients.
The intended identity is Q^H*A=R with Q=H0*H1, where H0 uses vector [1,v10]
and H1 acts on the second row. Internal rounding and final saturation mean
the returned factors approximate this identity; they are not IEEE arithmetic.
One input produces one output record, with no state across transactions.

## Reset Behavior

Reset is synchronous and active-high. A rising clock edge with reset asserted
cancels pending transactions and establishes the following state:
- the module has no architectural arithmetic state;
- `out_valid` is deasserted;
- all output data ports are `0`:
  - `out_r00_re = 0`, `out_r00_im = 0`
  - `out_r01_re = 0`, `out_r01_im = 0`
  - `out_r10_re = 0`, `out_r10_im = 0`
  - `out_r11_re = 0`, `out_r11_im = 0`
  - `out_tau0_re = 0`, `out_tau0_im = 0`
  - `out_tau1_re = 0`, `out_tau1_im = 0`
  - `out_v10_re = 0`, `out_v10_im = 0`
