# Quantized Elementwise Add S8

This block computes one signed-int8 quantized elementwise sum. Each input has
its own offset and Q31 scale; a second Q31 scale and activation clamp map the
sum into the output quantizer. Consecutive commands process consecutive vector
elements independently.

## Interface

### RTL interface

```systemverilog
module signal_processing_cnn_elementwise_arm_elementwise_add_s8 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [7:0]   in_input_1,
    input  logic [7:0]   in_input_2,
    input  logic [31:0]  in_input_1_offset,
    input  logic [31:0]  in_input_1_mult,
    input  logic [31:0]  in_input_1_shift,
    input  logic [31:0]  in_input_2_offset,
    input  logic [31:0]  in_input_2_mult,
    input  logic [31:0]  in_input_2_shift,
    input  logic [31:0]  in_left_shift,
    input  logic [31:0]  in_out_offset,
    input  logic [31:0]  in_out_mult,
    input  logic [31:0]  in_out_shift,
    input  logic [31:0]  in_out_activation_min,
    input  logic [31:0]  in_out_activation_max,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_output,
    output logic         out_error
);
```

The two data inputs and output are signed int8 bit patterns. All other numeric
inputs are signed int32 bit patterns. `out_error` is a one-bit status.

### Behavior interface

```python
class SignalProcessingCnnElementwiseArmElementwiseAddS8:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_input_1, in_input_2, in_input_1_offset,
                in_input_1_mult, in_input_1_shift, in_input_2_offset,
                in_input_2_mult, in_input_2_shift, in_left_shift,
                in_out_offset, in_out_mult, in_out_shift,
                in_out_activation_min, in_out_activation_max): ...
```

Each `process` call represents one accepted element pair. Pass the two data
inputs as unsigned 8-bit integers and all other arguments as unsigned 32-bit
integers, encoding the signed values specified above. It always returns one
dictionary with exactly `out_output` (an unsigned 8-bit encoding of the signed
int8 result) and `out_error` (0 or 1). Invalid parameters return
`{"out_output": 0, "out_error": 1}`, not an exception, `None`, or a list.
A valid zero result also returns the full dictionary with `out_error=0`.
There is no state between element pairs. Construction and `reset()` have
no numerical effect; `reset()` returns `None`. Physical idle and stall
cycles require no calls.

## Modules

- `signal_processing_cnn_elementwise_arm_elementwise_add_s8`: per-element
  offset, rescale, addition and clamp.

## Functional Behavior

Valid commands have input offsets in [-127,128], output offset in [-128,127],
all three multipliers in [0,2^31-1], all three requantization shifts in [-31,0],
`in_left_shift` in [0,20], and
`-128 <= in_out_activation_min <= in_out_activation_max <= 127`.
These limits retain the non-overflowing int8 quantized-add domain.

For decoded signed inputs `a,b`, first compute
`v1=(a+input_1_offset)*2^left_shift` and
`v2=(b+input_2_offset)*2^left_shift`.
Requantize each using its own multiplier and shift, sum the two results, then
requantize the sum using `out_mult,out_shift`. Add `out_offset` and clamp
to the inclusive activation interval. Encode the result as int8 with
`out_error=0`. Arithmetic before this final encoding is exact; the stated
bounds keep the requantizer's inputs within signed int32.

Invalid commands return `out_output=0,out_error=1`; they are not silently
converted into different scales or clipping intervals.

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

For example, inputs 8 and 4, zero offsets, zero shifts, and multipliers
`2^30` in all three stages produce `(4+2)/2=3`, not a saturated value.

## Reset Behavior

Reset is synchronous active-high. It clears output valid, payload and error,
cancelling any pending response. There is no persistent numerical state.
Inputs and outputs transfer only when valid and ready are both high.
An output waiting for ready keeps its valid flag and all payload bits stable.
Every accepted command, including an invalid command, produces one response.
