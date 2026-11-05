# FalconMLP

A two-affine feed-forward transform from FalconMLP: input projection with bias, GELU, then output projection with bias. One width-four vector and both 4x4 matrices arrive in each transaction. There is no GLU branch.

## Interface

### RTL interface

```systemverilog
module ai_ml_tx_mlp_other_falcon_mlp_glu (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [15:0] in_x_0,
    input logic [15:0] in_x_1,
    input logic [15:0] in_x_2,
    input logic [15:0] in_x_3,
    input logic [15:0] in_up_w_0_0,
    input logic [15:0] in_up_w_0_1,
    input logic [15:0] in_up_w_0_2,
    input logic [15:0] in_up_w_0_3,
    input logic [15:0] in_up_w_1_0,
    input logic [15:0] in_up_w_1_1,
    input logic [15:0] in_up_w_1_2,
    input logic [15:0] in_up_w_1_3,
    input logic [15:0] in_up_w_2_0,
    input logic [15:0] in_up_w_2_1,
    input logic [15:0] in_up_w_2_2,
    input logic [15:0] in_up_w_2_3,
    input logic [15:0] in_up_w_3_0,
    input logic [15:0] in_up_w_3_1,
    input logic [15:0] in_up_w_3_2,
    input logic [15:0] in_up_w_3_3,
    input logic [15:0] in_down_w_0_0,
    input logic [15:0] in_down_w_0_1,
    input logic [15:0] in_down_w_0_2,
    input logic [15:0] in_down_w_0_3,
    input logic [15:0] in_down_w_1_0,
    input logic [15:0] in_down_w_1_1,
    input logic [15:0] in_down_w_1_2,
    input logic [15:0] in_down_w_1_3,
    input logic [15:0] in_down_w_2_0,
    input logic [15:0] in_down_w_2_1,
    input logic [15:0] in_down_w_2_2,
    input logic [15:0] in_down_w_2_3,
    input logic [15:0] in_down_w_3_0,
    input logic [15:0] in_down_w_3_1,
    input logic [15:0] in_down_w_3_2,
    input logic [15:0] in_down_w_3_3,
    input logic [15:0] in_up_b_0,
    input logic [15:0] in_up_b_1,
    input logic [15:0] in_up_b_2,
    input logic [15:0] in_up_b_3,
    input logic [15:0] in_down_b_0,
    input logic [15:0] in_down_b_1,
    input logic [15:0] in_down_b_2,
    input logic [15:0] in_down_b_3,
    output logic out_valid,
    input logic out_ready,
    output logic [15:0] out_y_0,
    output logic [15:0] out_y_1,
    output logic [15:0] out_y_2,
    output logic [15:0] out_y_3
);
```

### Behavior interface

```python
class FalconMLP:
    def __init__(self): pass
    def reset(self): pass
    def process(self,
        in_x_0,
        in_x_1,
        in_x_2,
        in_x_3,
        in_up_w_0_0,
        in_up_w_0_1,
        in_up_w_0_2,
        in_up_w_0_3,
        in_up_w_1_0,
        in_up_w_1_1,
        in_up_w_1_2,
        in_up_w_1_3,
        in_up_w_2_0,
        in_up_w_2_1,
        in_up_w_2_2,
        in_up_w_2_3,
        in_up_w_3_0,
        in_up_w_3_1,
        in_up_w_3_2,
        in_up_w_3_3,
        in_down_w_0_0,
        in_down_w_0_1,
        in_down_w_0_2,
        in_down_w_0_3,
        in_down_w_1_0,
        in_down_w_1_1,
        in_down_w_1_2,
        in_down_w_1_3,
        in_down_w_2_0,
        in_down_w_2_1,
        in_down_w_2_2,
        in_down_w_2_3,
        in_down_w_3_0,
        in_down_w_3_1,
        in_down_w_3_2,
        in_down_w_3_3,
        in_up_b_0,
        in_up_b_1,
        in_up_b_2,
        in_up_b_3,
        in_down_b_0,
        in_down_b_1,
        in_down_b_2,
        in_down_b_3,
    ): pass
```

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y_0`, `out_y_1`, `out_y_2`, `out_y_3`: 16.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Transaction protocol

One accepted input produces one ordered response containing `out_y_0`, `out_y_1`, `out_y_2`, `out_y_3`. Transfer is sampled at a rising clock edge with valid and ready both high. Pending output valid and payload remain stable under backpressure. Latency and internal architecture are not prescribed. The named dimensions and word widths are fixed for this task, not configurable through unused parameters.

## Modules

The named top module implements the complete transform without external memory. Matrices are row-major: a weight `in_*_w_j_i` or `in_*_proj_weight_j_i` multiplies input element i to produce element j.

## Functional Behavior

Inputs and outputs are signed 16-bit two's-complement Q8.8 values: raw integer q represents q/256. All bit patterns are accepted. Hidden width and intermediate width are both four; there is no independent gate projection. The directory/top name is a legacy identifier and does not change the specified two-affine FalconMLP computation.

1. Interpret x, weights and biases as signed integers. For each intermediate j, compute S=256*up_b[j]+sum_i(x[i]*up_w[j][i]) using full-width integers. Set z[j]=clip16(trunc0(S/256)), where clip16 clamps to [-32768,32767] and trunc0 rounds toward zero.
2. Set a[j]=G_Q8(z[j]) below.
3. For each output k, compute S=256*down_b[k]+sum_j(a[j]*down_w[k][j]) in full-width integers. Set y[k]=clip16(trunc0(S/256)); expose its 16-bit word.

No partial-sum wraparound or saturation occurs. Quantization and saturation occur once at each affine-layer output. Biases are supplied independently for both layers. This is a fixed-point specialization of `FalconMLP.forward` with its selectable activation set to GELU, not a full decoder layer or an assertion that upstream tensors use Q8.8.

### GELU approximation

The mathematical target is `G(x)=x*Phi(x)`, with `Phi` the standard normal CDF. Approximate its small correction `h(a)=a*Phi(-a)=a*erfc(a/sqrt(2))/2` for `a=abs(x)` rather than discarding the negative branch. Let `T[i]=RNE(2^30*h(i/32))` for i=0..256. This defines a finite table independently of either artifact; RNE denotes nearest integer with ties to even. The task specifies observable arithmetic, not a required circuit or ROM organization.

For signed Q8.8 integer z, let a=abs(z). If a>=2048, return zero for z<0 and z otherwise. Else j=a//8, r=a%8 and C=floor((T[j]*(8-r)+T[j+1]*r)/8). Round C/2^22 to the nearest-even integer c. Return -c for z<0 and z-c otherwise. Thus G_Q8(0)=0 and the negative GELU lobe is retained. The absolute error after dividing the returned integer by 256 is below 0.0021 against GELU(z/256), including final Q8.8 rounding. Saturation/error at the affine boundaries is a separate, explicit numerical adaptation.


## Reset Behavior

Synchronous active-high `rst` clears pending output valid and all output words; `in_ready=1` after reset. No computational state persists across transactions. A reset discards a pending response. Output payload is zero when output valid is low.
