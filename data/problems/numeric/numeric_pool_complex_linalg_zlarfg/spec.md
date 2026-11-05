# ComplexHouseholderReflector3

This module generates one complex elementary reflector for a fixed order-4 Householder step. One accepted input transaction supplies a complex scalar `alpha` and a 3-element complex vector `x`; the module returns the scalar `beta`, the reflector coefficient `tau`, and the updated vector `v` such that the reflector has the form `H = I - tau * [1; v] * [1, v^H]`. The transaction implements the LAPACK `ZLARFG` algorithm specialized to `n = 4`, `incx = 1`, and a signed fixed-point integer representation for complex components.

## Interface

### RTL interface

```systemverilog
module complex_householder_reflector3 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_alpha_re,
    input  logic [15:0] in_alpha_im,
    input  logic [15:0] in_x0_re,
    input  logic [15:0] in_x0_im,
    input  logic [15:0] in_x1_re,
    input  logic [15:0] in_x1_im,
    input  logic [15:0] in_x2_re,
    input  logic [15:0] in_x2_im,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_alpha_re,
    output logic [15:0] out_alpha_im,
    output logic [15:0] out_tau_re,
    output logic [15:0] out_tau_im,
    output logic [15:0] out_x0_re,
    output logic [15:0] out_x0_im,
    output logic [15:0] out_x1_re,
    output logic [15:0] out_x1_im,
    output logic [15:0] out_x2_re,
    output logic [15:0] out_x2_im
);
```

### Behavior interface

```python
class ComplexHouseholderReflector3:
    def __init__(self):
        pass

    def reset(self):
        pass

    def process(
            self,
            in_alpha_re, in_alpha_im,
            in_x0_re, in_x0_im,
            in_x1_re, in_x1_im,
            in_x2_re, in_x2_im):
        pass
```

The return value of `process(...)` is exactly one output transaction, represented as a dict with keys:
`out_alpha_re`, `out_alpha_im`, `out_tau_re`, `out_tau_im`,
`out_x0_re`, `out_x0_im`, `out_x1_re`, `out_x1_im`,
`out_x2_re`, `out_x2_im`.

Each call consumes one accepted input transaction, not a clock cycle.
Arguments and all ten returned values are unsigned 16-bit bit patterns, with
the signed integer or Q3.13 interpretations specified below. Use these raw
integers, not Python complex or floating-point values. Identity and zero cases
still return all ten fields, never `None` or a list. Calls have no persistent
mathematical state. Construction and `reset()` produce no output record;
`reset()` returns `None`.

## Modules

- `complex_householder_reflector3`: top-level module implementing one fixed-size Householder reflector generation for one complex scalar and one 3-element complex vector.

## Functional Behavior

Input components and output beta are signed 16-bit integers. Output tau and
the three vector components are signed 16-bit Q3.13 (raw value divided by 8192).
Thus coefficients retain fractional parts rather than an integer reciprocal
turning every nontrivial reflector vector into zero.

Let alpha=(ar,ai), xk=(xr,xi). If all x components and ai are zero, return
beta=ar, tau=0 and v=0. Otherwise compute, without intermediate narrowing:

- bmag=floor(sqrt(ar²+ai²+sum_k(xr²+xi²))).
- beta=-bmag if ar>=0, else bmag; beta is real.
- tau_re=trunc0(8192*(beta-ar)/beta), tau_im=trunc0(-8192*ai/beta).
- d=(ar-beta)²+ai².
- vk_re=trunc0(8192*(xr*(ar-beta)+xi*ai)/d).
- vk_im=trunc0(8192*(xi*(ar-beta)-xr*ai)/d).

Each displayed division rounds toward zero; a zero denominator yields zero.
Saturate beta, tau and v independently to [-32768,32767] only when returning
their raw 16-bit patterns. Ratios use the unsaturated beta. For alpha=3 and
x=(4,0,0) real, beta=-5, tau_re=13107 and v0_re=4096: approximately 1.6 and 0.5.
This approximates the ZLARFG identity H^H*[alpha;x]=[beta;0], with
H=I-tau*[1;v]*[1;v]^H. Integer square-root and fixed-point rounding are part of
this bounded adaptation, not claims of exact complex floating-point arithmetic.
Every accepted input returns one record; the calculation has no persistent state.

## Reset Behavior

Reset is synchronous active-high.

On a reset cycle, all architectural state is set to zero. This block has no architectural state beyond output holding registers required by the handshake. After reset:

- `out_alpha_re = 16'h0000`
- `out_alpha_im = 16'h0000`
- `out_tau_re   = 16'h0000`
- `out_tau_im   = 16'h0000`
- `out_x0_re    = 16'h0000`
- `out_x0_im    = 16'h0000`
- `out_x1_re    = 16'h0000`
- `out_x1_im    = 16'h0000`
- `out_x2_re    = 16'h0000`
- `out_x2_im    = 16'h0000`

The module accepts no retained partial transaction state across reset. The first accepted input after reset is processed as an independent transaction.
