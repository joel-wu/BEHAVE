# BatchNorm2d Inference

This block performs batch-normalization inference for one NCHW spatial location containing exactly 4 channels. For each channel it computes `y = ((x - running_mean) / sqrt(running_var + eps)) * weight + bias` using the explicitly defined binary32 arithmetic profile below. The block is inference-only: `running_mean` and `running_var` are inputs, not updated state. One accepted input transaction produces exactly one output transaction containing the 4 normalized channel results.

## Interface

### RTL interface

```systemverilog
module batchnorm2d_inference (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_x0,
    input  logic [31:0] in_x1,
    input  logic [31:0] in_x2,
    input  logic [31:0] in_x3,
    input  logic [31:0] in_weight0,
    input  logic [31:0] in_weight1,
    input  logic [31:0] in_weight2,
    input  logic [31:0] in_weight3,
    input  logic [31:0] in_bias0,
    input  logic [31:0] in_bias1,
    input  logic [31:0] in_bias2,
    input  logic [31:0] in_bias3,
    input  logic [31:0] in_running_mean0,
    input  logic [31:0] in_running_mean1,
    input  logic [31:0] in_running_mean2,
    input  logic [31:0] in_running_mean3,
    input  logic [31:0] in_running_var0,
    input  logic [31:0] in_running_var1,
    input  logic [31:0] in_running_var2,
    input  logic [31:0] in_running_var3,
    input  logic [31:0] in_eps,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_y0,
    output logic [31:0] out_y1,
    output logic [31:0] out_y2,
    output logic [31:0] out_y3
);
```

### Behavior interface

```python
class BatchNorm2dInference:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_x0, in_x1, in_x2, in_x3,
        in_weight0, in_weight1, in_weight2, in_weight3,
        in_bias0, in_bias1, in_bias2, in_bias3,
        in_running_mean0, in_running_mean1, in_running_mean2, in_running_mean3,
        in_running_var0, in_running_var1, in_running_var2, in_running_var3,
        in_eps
    ): ...
```

The `process(...)` return value is one dict with keys `out_y0`, `out_y1`, `out_y2`, and `out_y3`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y0`, `out_y1`, `out_y2`, `out_y3`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `batchnorm2d_inference`: top-level module that computes the 4 channel outputs for one accepted input transaction.

## Functional Behavior

All scalar ports `in_x*`, `in_weight*`, `in_bias*`, `in_running_mean*`, `in_running_var*`, `in_eps`, and `out_y*` are raw FP32 bit patterns.

For each channel `i` independently, where `i ∈ {0,1,2,3}`, define:
- `x_i = in_xi`
- `weight_i = in_weighti`
- `bias_i = in_biasi`
- `running_mean_i = in_running_meani`
- `running_var_i = in_running_vari`

The output is computed in this exact operation order:
1. `delta_i = fp32_sub(x_i, running_mean_i)`
2. `var_eps_i = fp32_add(running_var_i, in_eps)`
3. `inv_std_i = fp32_div(0x3F800000, fp32_sqrt(var_eps_i))`
4. `scaled_i = fp32_mul(fp32_mul(delta_i, inv_std_i), weight_i)`
5. `y_i = fp32_add(scaled_i, bias_i)`

The output transaction is:
- `out_y0 = y_0`
- `out_y1 = y_1`
- `out_y2 = y_2`
- `out_y3 = y_3`

`0x3F800000` is the FP32 encoding of `+1.0`.

### Numerical contract

Each addition, subtraction, multiplication, division and square root independently rounds to binary32, nearest with ties to even. There is no fused multiply-add or algebraic reassociation. Subnormal operands retain their value; underflow is gradual with nearest-even rounding also at the zero/subnormal and subnormal/normal boundaries. Output encodings are compared exactly, without a tolerance. NaNs and invalid operations give canonical 0x7FC00000 (a project sign/payload convention); other infinities and signed zeros follow IEEE arithmetic. The initial subtraction is performed before inverse-standard-deviation and weight scaling, not by precomputing an affine slope/intercept. In particular the reciprocal is rounded before multiplication; direct division of delta by sqrt need not be bit-identical.

Every raw input word has a defined result. Ordinary inference uses nonnegative running variance and positive epsilon. Other words follow the same primitives: a negative nonzero or NaN square-root argument produces NaN; sqrt(+0)=+0 and sqrt(-0)=-0; division by either zero produces the corresponding signed infinity unless invalid. Later zero-times-infinity and infinity-minus-infinity operations produce NaN. No variance clipping, epsilon fallback, affine disable, training-mode update or parameter storage is performed.

For example, (x,mean,var,eps,weight,bias)=(3,1,4,0,2,-1) yields 1 in that channel. The task chooses a particular operation order for deterministic hardware evaluation. It retains the BatchNorm inference equation but does not claim bit identity with CPU/GPU implementations that precompute linear coefficients or fuse operations.

The 4 channels are independent; there is no cross-channel reduction, no spatial reduction, and no state carried from one accepted input transaction to the next.

## Reset Behavior

Reset is active-high and asynchronous or synchronous implementation choice is permitted. On reset:
- `out_valid` is `0`.
- `in_ready` may be `1` immediately after reset deassertion if the block is idle.
- `out_y0 = 32'h00000000`
- `out_y1 = 32'h00000000`
- `out_y2 = 32'h00000000`
- `out_y3 = 32'h00000000`

The block has no architectural state other than any internal buffering needed to hold one pending 1:1 output transaction under output backpressure. After reset, no input transaction is pending and no learned parameters are stored internally.
