# Quantized Pointwise Convolution S8

This block computes all output channels of a signed-int8 1×1 convolution at one
spatial position. It retains the complete channel reduction, optional per-channel
bias, per-channel Q31 requantization, output offset and activation clamp.
Spatial positions are independent commands with shared or varying kernels.

## Interface

### RTL interface

```systemverilog
module signal_processing_cnn_conv_arm_convolve_1x1_s8_fast #(
    parameter int IN_CH  = 8,
    parameter int OUT_CH = 8
) (
    input  logic clk,
    input  logic rst,

    input  logic in_valid,
    output logic in_ready,
    input  logic [31:0] in_input_ch,
    input  logic [31:0] in_output_ch,
    input  logic signed [8:0] in_input_offset,
    input  logic signed [7:0] in_output_offset,
    input  logic signed [7:0] in_activation_min,
    input  logic signed [7:0] in_activation_max,
    input  logic signed [8*IN_CH-1:0] in_input_data,
    input  logic                        in_bias_valid,
    input  logic signed [32*OUT_CH-1:0] in_bias_data,
    input  logic signed [8*IN_CH*OUT_CH-1:0] in_filter_data,
    input  logic signed [32*OUT_CH-1:0] in_multiplier,
    input  logic signed [32*OUT_CH-1:0] in_shift,

    output logic out_valid,
    input  logic out_ready,
    output logic [31:0] out_output_ch,
    output logic signed [8*OUT_CH-1:0] out_output_data,
    output logic out_error
);
```

`IN_CH` and `OUT_CH` are positive integers at most 64, defaulting to 8.
Pack input channels and output channels least-significant slice first.
Weights are output-channel-major:
`in_filter_data[8*(oc*IN_CH+ic)+:8]`.
Bias, multiplier and shift slices are 32 bits per output channel.
All data, offsets, bias and shift values are signed two's-complement;
multipliers must decode nonnegative. The 9-bit input offset includes +128.

### Behavior interface

```python
class SignalProcessingCnnConvArmConvolve1x1S8Fast:
    def __init__(self, in_ch=8, out_ch=8): ...
    def reset(self): ...
    def process(self, in_input_ch, in_output_ch, in_input_offset, in_output_offset,
                in_activation_min, in_activation_max, in_input_data, in_bias_valid,
                in_bias_data, in_filter_data, in_multiplier, in_shift): ...
```

Constructor arguments correspond to RTL parameters. Each `process` call
represents one accepted command. Pass arguments as unsigned integers of the
declared widths, using two's-complement bit patterns for signed fields and
lanes. It always returns one dictionary with exactly `out_output_ch`
(unsigned 32-bit), `out_output_data` (unsigned `8*out_ch`-bit packed word),
and `out_error` (0 or 1). Decode each output data byte as signed int8 using
the channel packing above. An invalid command returns
`{"out_output_ch": 0, "out_output_data": 0, "out_error": 1}`, not `None`,
an exception, or a partial vector. Valid all-zero results also return the
full dictionary. There is no numerical state between calls; `reset()`
returns `None`. Physical idle and stall cycles require no calls.

## Modules

- `signal_processing_cnn_conv_arm_convolve_1x1_s8_fast`: one-position
  pointwise convolution over all input and output channels.

## Functional Behavior

A valid command has `in_input_ch=IN_CH`, `in_output_ch=OUT_CH`,
input offset in [-127,128], an ordered signed-int8 activation interval,
and every multiplier in [0,2^31-1] and shift in [-31,30].

For each output channel `oc`, compute the exact integer
`v = bias[oc] + sum_ic(weight[oc,ic]*(input[ic]+input_offset))`,
using zero bias when `in_bias_valid=0`.
The value `v*2^max(shift[oc],0)` must fit signed int32; otherwise the
whole command is invalid. Requantize `v`, add the signed-int8 output offset,
and clamp to the activation interval. Pack every clamped int8 result in channel
order; `out_output_ch=OUT_CH,out_error=0`.

Invalid metadata, numeric parameters or scaled accumulators produce a single
`out_error=1` response with both other outputs zero. No partial vector is
returned. Weight products and their sum are evaluated without intermediate
wraparound; the output-offset addition is also widened before clipping.

### Q31 requantization

For an integer value `v`, nonnegative signed-32-bit multiplier `m`, and shift
`s`, define `left=max(s,0)` and `right=max(-s,0)`:

1. `high = floor(((v * 2^left) * m + 2^30) / 2^31)`.
   This first rounding uses ties toward positive infinity.
2. If `right=0`, return `high`. Otherwise return
   `sign(high) * floor((abs(high) + 2^(right-1)) / 2^right)`.
   This second rounding uses ties away from zero.

These are two rounding stages, not a single rounded multiplication. Multipliers
represent Q0.31 scales. There is no hidden low-bit shift masking.

## Reset Behavior

Reset is synchronous active-high. It clears output valid, payload and error,
cancelling any pending response. There is no persistent numerical state.
Inputs and outputs transfer only when valid and ready are both high.
An output waiting for ready keeps its valid flag and all payload bits stable.
Every accepted command, including an invalid command, produces one response.
