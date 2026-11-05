# Four-channel depthwise 3x3 convolution

This is a finite integer specialization of timm ConvNormAct with four input/output channels, groups=4, kernel=3, stride=1, dilation=1, bias enabled, and normalization/activation/antialiasing disabled. Each transaction supplies an already extracted 3x3 patch per channel. It computes all four independent depthwise results; spatial window extraction and image-boundary padding are outside this interface.

## Interface

### RTL interface

```systemverilog
module ai_ml_vision_dwconv_timm_dwconv_3x3_s1 (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [287:0] in_patch, in_kernel,
    input logic [127:0] in_bias,
    output logic out_valid,
    input logic out_ready,
    output logic [127:0] out_y
);
```

All ports are raw unsigned bit patterns. For channel c=0..3 and row-major patch position k=0..8, signed 8-bit samples/weights occupy bits `[8*(9*c+k) +: 8]`. Signed 32-bit bias c and output c occupy bits `[32*c +: 32]`.

### Behavior interface

```python
class Depthwise3x3:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_patch, in_kernel, in_bias): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_y": ...}`, whose value is a Python integer holding all packed output
channels, not a list of per-channel results.

## Modules

The named top-level module is the complete task. No external coefficient ROM or helper module is required; implementation architecture is unrestricted.

## Functional Behavior

For each channel independently, compute `s[c] = bias[c] + sum(k=0..8, patch[c,k]*kernel[c,k])` using exact signed integer multiplication and addition. Emit the low 32 bits of each s[c], packed in channel order, with channel zero least significant. There is no kernel reversal, cross-channel mixing, intermediate rounding, saturation, normalization or activation. All raw input patterns are admitted.

One accepted input gives one output record. For example, all samples/weights equal to +1 and biases (0,1,-1,4) give outputs (9,10,8,13). A zero kernel returns the supplied biases. Distinct channel kernels must remain independent.

Input and output transfers occur on rising edges with the corresponding valid and ready both high. A stalled output remains valid with unchanged payload; input data has no effect unless accepted.

## Reset Behavior

Asynchronous active-high reset discards any pending output, deasserts out_valid, and zeros out_y. There is no arithmetic state between transactions. When no output is pending, out_y is zero.
