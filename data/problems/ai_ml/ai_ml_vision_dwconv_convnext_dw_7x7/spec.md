# ConvNeXt Depthwise 7x7 Convolution

This block implements the depthwise spatial convolution used by a ConvNeXt block when the depthwise kernel size is 7×7. Each accepted input transaction provides one complete 7×7 spatial neighborhood from a single channel together with the 7×7 depthwise filter coefficients and one bias term for that same channel. The block computes the dot product of the 49 input samples and 49 weights, adds the bias, and emits one output value. The operation is purely per-channel depthwise convolution: there is no cross-channel accumulation, no activation, no normalization, and no residual addition inside this block.

## Interface

### RTL interface

```systemverilog
module ai_ml_vision_dwconv_convnext_dw_7x7 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_x00,
    input  logic [15:0]  in_x01,
    input  logic [15:0]  in_x02,
    input  logic [15:0]  in_x03,
    input  logic [15:0]  in_x04,
    input  logic [15:0]  in_x05,
    input  logic [15:0]  in_x06,
    input  logic [15:0]  in_x10,
    input  logic [15:0]  in_x11,
    input  logic [15:0]  in_x12,
    input  logic [15:0]  in_x13,
    input  logic [15:0]  in_x14,
    input  logic [15:0]  in_x15,
    input  logic [15:0]  in_x16,
    input  logic [15:0]  in_x20,
    input  logic [15:0]  in_x21,
    input  logic [15:0]  in_x22,
    input  logic [15:0]  in_x23,
    input  logic [15:0]  in_x24,
    input  logic [15:0]  in_x25,
    input  logic [15:0]  in_x26,
    input  logic [15:0]  in_x30,
    input  logic [15:0]  in_x31,
    input  logic [15:0]  in_x32,
    input  logic [15:0]  in_x33,
    input  logic [15:0]  in_x34,
    input  logic [15:0]  in_x35,
    input  logic [15:0]  in_x36,
    input  logic [15:0]  in_x40,
    input  logic [15:0]  in_x41,
    input  logic [15:0]  in_x42,
    input  logic [15:0]  in_x43,
    input  logic [15:0]  in_x44,
    input  logic [15:0]  in_x45,
    input  logic [15:0]  in_x46,
    input  logic [15:0]  in_x50,
    input  logic [15:0]  in_x51,
    input  logic [15:0]  in_x52,
    input  logic [15:0]  in_x53,
    input  logic [15:0]  in_x54,
    input  logic [15:0]  in_x55,
    input  logic [15:0]  in_x56,
    input  logic [15:0]  in_x60,
    input  logic [15:0]  in_x61,
    input  logic [15:0]  in_x62,
    input  logic [15:0]  in_x63,
    input  logic [15:0]  in_x64,
    input  logic [15:0]  in_x65,
    input  logic [15:0]  in_x66,

    input  logic [15:0]  in_w00,
    input  logic [15:0]  in_w01,
    input  logic [15:0]  in_w02,
    input  logic [15:0]  in_w03,
    input  logic [15:0]  in_w04,
    input  logic [15:0]  in_w05,
    input  logic [15:0]  in_w06,
    input  logic [15:0]  in_w10,
    input  logic [15:0]  in_w11,
    input  logic [15:0]  in_w12,
    input  logic [15:0]  in_w13,
    input  logic [15:0]  in_w14,
    input  logic [15:0]  in_w15,
    input  logic [15:0]  in_w16,
    input  logic [15:0]  in_w20,
    input  logic [15:0]  in_w21,
    input  logic [15:0]  in_w22,
    input  logic [15:0]  in_w23,
    input  logic [15:0]  in_w24,
    input  logic [15:0]  in_w25,
    input  logic [15:0]  in_w26,
    input  logic [15:0]  in_w30,
    input  logic [15:0]  in_w31,
    input  logic [15:0]  in_w32,
    input  logic [15:0]  in_w33,
    input  logic [15:0]  in_w34,
    input  logic [15:0]  in_w35,
    input  logic [15:0]  in_w36,
    input  logic [15:0]  in_w40,
    input  logic [15:0]  in_w41,
    input  logic [15:0]  in_w42,
    input  logic [15:0]  in_w43,
    input  logic [15:0]  in_w44,
    input  logic [15:0]  in_w45,
    input  logic [15:0]  in_w46,
    input  logic [15:0]  in_w50,
    input  logic [15:0]  in_w51,
    input  logic [15:0]  in_w52,
    input  logic [15:0]  in_w53,
    input  logic [15:0]  in_w54,
    input  logic [15:0]  in_w55,
    input  logic [15:0]  in_w56,
    input  logic [15:0]  in_w60,
    input  logic [15:0]  in_w61,
    input  logic [15:0]  in_w62,
    input  logic [15:0]  in_w63,
    input  logic [15:0]  in_w64,
    input  logic [15:0]  in_w65,
    input  logic [15:0]  in_w66,

    input  logic [31:0]  in_bias,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_y
);
```

### Behavior interface

```python
class AiMlVisionDwconvConvnextDw7x7:
    def __init__(self): ...
    def reset(self): ...
    def process(
        self,
        in_x00, in_x01, in_x02, in_x03, in_x04, in_x05, in_x06,
        in_x10, in_x11, in_x12, in_x13, in_x14, in_x15, in_x16,
        in_x20, in_x21, in_x22, in_x23, in_x24, in_x25, in_x26,
        in_x30, in_x31, in_x32, in_x33, in_x34, in_x35, in_x36,
        in_x40, in_x41, in_x42, in_x43, in_x44, in_x45, in_x46,
        in_x50, in_x51, in_x52, in_x53, in_x54, in_x55, in_x56,
        in_x60, in_x61, in_x62, in_x63, in_x64, in_x65, in_x66,
        in_w00, in_w01, in_w02, in_w03, in_w04, in_w05, in_w06,
        in_w10, in_w11, in_w12, in_w13, in_w14, in_w15, in_w16,
        in_w20, in_w21, in_w22, in_w23, in_w24, in_w25, in_w26,
        in_w30, in_w31, in_w32, in_w33, in_w34, in_w35, in_w36,
        in_w40, in_w41, in_w42, in_w43, in_w44, in_w45, in_w46,
        in_w50, in_w51, in_w52, in_w53, in_w54, in_w55, in_w56,
        in_w60, in_w61, in_w62, in_w63, in_w64, in_w65, in_w66,
        in_bias
    ): ...
```

The returned dictionary has one key: `out_y`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_y`: 32.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

## Modules

- `ai_ml_vision_dwconv_convnext_dw_7x7`: top-level depthwise 7×7 convolution block.

## Functional Behavior

All `in_x**` and `in_w**` ports are signed 16-bit integers in two's-complement format. `in_bias` is a signed 32-bit integer in two's-complement format. `out_y` is a signed 32-bit integer returned on the output port as a 32-bit two's-complement bit pattern.

For one accepted input transaction, interpret the input sample matrix and weight matrix as:

- row 0: `x00..x06`, `w00..w06`
- row 1: `x10..x16`, `w10..w16`
- row 2: `x20..x26`, `w20..w26`
- row 3: `x30..x36`, `w30..w36`
- row 4: `x40..x46`, `w40..w46`
- row 5: `x50..x56`, `w50..w56`
- row 6: `x60..x66`, `w60..w66`

The block computes the signed sum

`sum = bias + Σ(r=0..6) Σ(c=0..6) (xrc * wrc)`

where each `xrc * wrc` is a signed integer multiplication of the sign-extended 16-bit operands, and all 49 products and the bias are added with exact integer arithmetic.

After the full sum is formed, the result is truncated to 32 bits by modulo-2^32 wraparound. Equivalently, `out_y` is the low 32 bits of the mathematical integer result. No saturation is applied.

The arithmetic order is the natural row-major accumulation order shown below; because the operation is exact integer addition before final truncation, any implementation producing the same mathematical sum is compliant:

1. Start with `partial_sum = sign_extend(in_bias)`.
2. Add `x00*w00`, `x01*w01`, `x02*w02`, `x03*w03`, `x04*w04`, `x05*w05`, `x06*w06`.
3. Add `x10*w10` through `x16*w16`.
4. Add `x20*w20` through `x26*w26`.
5. Add `x30*w30` through `x36*w36`.
6. Add `x40*w40` through `x46*w46`.
7. Add `x50*w50` through `x56*w56`.
8. Add `x60*w60` through `x66*w66`.
9. Return the low 32 bits of `partial_sum` as `out_y`.

This block performs only the depthwise convolution MAC for one output position of one channel. Padding, spatial window extraction, stride, dilation, per-channel selection, normalization, MLP, layer scale, residual addition, and activation are outside the scope of this block and must be handled by surrounding logic.

The mapping is 1:1: every accepted input transaction produces exactly one output transaction.

## Reset Behavior

Reset is asynchronous active-high. The block has no architectural state affecting numerical results. After reset, any internal busy or output-holding state shall be cleared so that no stale output is pending. `out_valid` shall be deasserted. `in_ready` may be asserted immediately after reset release. `out_y` shall be driven to `32'h00000000` whenever no valid output is being presented.
