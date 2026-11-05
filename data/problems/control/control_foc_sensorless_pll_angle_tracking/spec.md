# Ortega Flux Observer and Phase-Locked Tracking

Perform one original-Ortega observer update and one PLL update for each accepted
voltage/current sample. Retain both flux coordinates, tracked phase and speed
across requests. This is the numerical observer/PLL core, not the complete motor
controller. Resistance, inductance, flux linkage and gains are supplied directly;
temperature, saliency and saturation-compensation modes are disabled.

## Interface

### RTL interface

```systemverilog
module OrtegaPLL (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [15:0] in_v_alpha, in_v_beta,
    input logic signed [15:0] in_i_alpha, in_i_beta,
    input logic signed [15:0] in_dt, in_r, in_l, in_lambda,
    input logic signed [15:0] in_gamma_half, in_pll_kp, in_pll_ki,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_obs_phase, out_pll_phase,
    output logic signed [31:0] out_pll_speed, out_x1, out_x2
);
```

### Behavior interface

```python
class OrtegaPLL:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_v_alpha, in_v_beta, in_i_alpha, in_i_beta,
                in_dt, in_r, in_l, in_lambda, in_gamma_half, in_pll_kp, in_pll_ki): ...
```

Each call represents one accepted request, advances the observer and PLL once,
and returns one dictionary with exactly the keys `out_obs_phase`,
`out_pll_phase`, `out_pll_speed`, `out_x1`, and `out_x2`. Values are Python
integers in [0, 2**32 - 1], encoding the output port words, including signed
state and speed. Input arguments use unsigned 16-bit encodings. Return one
complete dictionary, not `None` or a list. State persists between calls;
`reset()` restores all four initial states and returns no response.

## Modules

`OrtegaPLL` includes the two-coordinate observer, a specified atan2 approximation
and the stateful PLL. No lookup memory or external motor-model state is required.

## Functional Behavior

Every input is a
signed 16-bit word with **15 fractional bits** (real value raw/32768). All words
have defined arithmetic semantics, including negative gains or dt; normal motor
use supplies positive dt, linkage and nonnegative physical parameters.

The observer states x1,x2 and PLL speed are signed 32-bit words with **24
fractional bits**, spanning [-128,128). Speed is in **turns per time unit**.
Both phase outputs are unsigned 32-bit turns modulo one revolution; 0x40000000
is a quarter turn. Gains kp and ki use the same time unit as dt. These units
replace radians consistently in both PLL equations.

Requests transfer on a rising edge when in_valid && in_ready outside reset;
responses transfer on out_valid && out_ready. Inputs must remain stable while
offered and stalled. Outputs must remain stable while valid and stalled.
Each accepted request advances state once and yields one ordered response.
Do not advance state on an idle or stalled request. Latency is unspecified.

Below, variables denote signed raw integers unless called phase words. Let
T_n(z)=sign(z)*floor(abs(z)/2^n) (truncation toward zero), and let S(z) clamp to
[-2147483648,2147483647]. All intermediate sums and products are widened without
wrap or saturation; S is applied only at the state writes shown below.

Start with the old states x1,x2,P,W, initially zero. Decode input words as signed
integers va,vb,ia,ib,dt,r,l,lambda,g,kp,ki. Compute in this order:

1. Set V_a=va*512, V_b=vb*512, L=lambda*512. Form
   L_a=T_6(l*ia), L_b=T_6(l*ib), R_a=T_6(r*ia), R_b=T_6(r*ib).
2. Set F_a=x1-L_a, F_b=x2-L_b, and
   E=min(0,T_24(L*L-F_a*F_a-F_b*F_b)). The nonpositive clamp is required.
3. Set C_a=T_15(g*T_24(F_a*E)), C_b=T_15(g*T_24(F_b*E)), then
   A=S(x1+T_15((V_a-R_a+C_a)*dt)), B=S(x2+T_15((V_b-R_b+C_b)*dt)).
4. If L>0 and **4*(A*A+B*B)<L*L**, replace each of A,B by
   S(sign(z)*floor(11*abs(z)/10)). This is the positive 1.1 low-magnitude boost.
   The squared comparison is the exact nonnegative equivalent of
   sqrt(A*A+B*B)<L/2; neither a wrapped square nor a quantized square root is used.
5. Let O=angle(B-L_b,A-L_a), as defined below. Let D be O-P reduced to the
   signed interval [-2^31,2^31). The exactly opposite phase uses the negative
   half-turn. Compute Kp=T_23(kp*D) and Ki=T_23(ki*D), both in the speed/state
   format. Then P'=(P+T_7((W+Kp)*dt)) mod 2^32 and W'=S(W+T_15(Ki*dt)).
   The phase update uses **old W**, not W'.
6. Retain (A,B,P',W'). Return out_x1=A, out_x2=B, out_obs_phase=O,
   out_pll_phase=P', out_pll_speed=W', encoded as 32-bit words.

For angle(y,x), let a=abs(x), b=abs(y). If a=b=0 return zero. If a>=b,
set q=floor((b*2^29)/a); otherwise set q=2^30-floor((a*2^29)/b).
Return q in quadrant (+,+), 2^31-q in (-,+), 2^31+q in (-,-), or
2^32-q in (+,-), modulo 2^32. Zero coordinates use the nonnegative quadrant.
This octant-linear approximation is exact on axes/diagonals; its error relative
to real atan2 is below 0.0114 turns. It is not the upstream floating-point helper.

Example after reset: va=4096, vb=0, dt=4096, lambda=16384 and all other inputs
zero produce x1=288358, x2=0 and both phases zero. The pre-boost x1 is 262144;
the boost increases it without reversing its sign. Persistent integration may
exceed magnitude one, which is why the state representation is wider than Q31.
Saturation, fixed rounding and the declared angle approximation are part of
this finite numerical task, not a claim of float-bit equivalence to the source.

## Reset Behavior

Reset is synchronous active-high. A reset edge discards any pending response,
clears all four states, clears out_valid and zeroes all output words. in_ready
is zero during reset.
Outputs are otherwise unspecified when out_valid=0.
