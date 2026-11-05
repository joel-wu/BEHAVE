# GemmaMLPGeGLU

A width-two, bias-free specialization of GemmaMLP with GELU activation: a gate projection, independent up projection, pointwise product and down projection. All three matrices are transaction inputs.

## Interface

### RTL interface

```systemverilog
module GemmaMLPGeGLU (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [31:0] in_x_0,
    input logic [31:0] in_x_1,
    input logic [31:0] in_gate_proj_weight_0_0,
    input logic [31:0] in_gate_proj_weight_0_1,
    input logic [31:0] in_gate_proj_weight_1_0,
    input logic [31:0] in_gate_proj_weight_1_1,
    input logic [31:0] in_up_proj_weight_0_0,
    input logic [31:0] in_up_proj_weight_0_1,
    input logic [31:0] in_up_proj_weight_1_0,
    input logic [31:0] in_up_proj_weight_1_1,
    input logic [31:0] in_down_proj_weight_0_0,
    input logic [31:0] in_down_proj_weight_0_1,
    input logic [31:0] in_down_proj_weight_1_0,
    input logic [31:0] in_down_proj_weight_1_1,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_y_0,
    output logic [31:0] out_y_1
);
```

### Behavior interface

```python
class GemmaMLPGeGLU:
    def __init__(self): pass
    def reset(self): pass
    def process(self,
        in_x_0,
        in_x_1,
        in_gate_proj_weight_0_0,
        in_gate_proj_weight_0_1,
        in_gate_proj_weight_1_0,
        in_gate_proj_weight_1_1,
        in_up_proj_weight_0_0,
        in_up_proj_weight_0_1,
        in_up_proj_weight_1_0,
        in_up_proj_weight_1_1,
        in_down_proj_weight_0_0,
        in_down_proj_weight_0_1,
        in_down_proj_weight_1_0,
        in_down_proj_weight_1_1,
    ): pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y_0`, `out_y_1`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

One accepted input produces one ordered response containing `out_y_0`, `out_y_1`. Transfer is sampled at a rising clock edge with valid and ready both high. Pending output valid and payload remain stable under backpressure. Latency and internal architecture are not prescribed. The named dimensions and word widths are fixed for this task, not configurable through unused parameters.

## Modules

The named top module implements the complete transform without external memory. Matrices are row-major: a weight `in_*_w_j_i` or `in_*_proj_weight_j_i` multiplies input element i to produce element j.

## Functional Behavior

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

All data are raw binary32 words. Each multiply and add rounds separately to nearest, ties to even (no fusion). Subnormal operands retain their values and underflow is gradual; arithmetic NaNs/invalid operations return `0x7FC00000`. Other infinity/zero arithmetic follows IEEE rules.

With x of width two and three supplied 2x2 matrices, compute `g=W_gate*x`, `u=W_up*x`, `m[j]=mul(G_approx(g[j]),u[j])`, and `y=W_down*m`. Each matrix row uses `add(mul(w0,x0),mul(w1,x1))`. All three projections matter; there are no biases, normalization, residual or dropout in this selected GemmaMLP operator.

### GELU approximation

The mathematical target is `G(x)=x*Phi(x)`, with `Phi` the standard normal CDF. Approximate its small correction `h(a)=a*Phi(-a)=a*erfc(a/sqrt(2))/2` for `a=abs(x)` rather than discarding the negative branch. Let `T[i]=RNE(2^30*h(i/32))` for i=0..256. This defines a finite table independently of either artifact; RNE denotes nearest integer with ties to even. The task specifies observable arithmetic, not a required circuit or ROM organization.

For a finite normal x with |x|<8, let q=floor(65536*abs(x)), j=q//2048, r=q%2048, and C=floor((T[j]*(2048-r)+T[j+1]*r)/2048). Let h be the nearest-even binary32 rounding of C/2^30. Return -h for negative x and add(x,-h) for positive x. For |x|>=8 return -0 when x is negative and x otherwise. NaNs return canonical NaN; positive/negative infinity return positive infinity/-0 respectively. Zero and subnormal inputs return their signed zero as a rule of this GELU approximation only; basic add/multiply preserve subnormal values.

The absolute activation error is below 0.00011 versus x*Phi(x) over all finite binary32 inputs: interpolation contributes less than 0.0000975, input flooring less than 0.00000763, and quantization/rounding less than 0.0000006; the omitted tail beyond eight is below 6e-15. This is an activation bound, not a bound on the whole MLP under arbitrary large matrix weights. The approximation targets the source's selectable GELU activation; it does not claim to reproduce every Gemma configuration or backend rounding policy.


## Reset Behavior

Synchronous active-high `rst` clears pending output valid and all output words; `in_ready=1` after reset. No computational state persists across transactions. A reset discards a pending response. Output payload is zero when output valid is low.
