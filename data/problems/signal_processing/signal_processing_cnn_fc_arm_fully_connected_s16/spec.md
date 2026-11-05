# SignalProcessingCnnFcArmFullyConnectedS16

This block computes a signed-quantized fully connected layer for up to two
input vectors, eight input channels and four output channels. A command
carries the complete input batch, weight matrix, bias vector and per-tensor
quantization parameters. Every output sums all input channels before
requantization and activation clipping. The same weights and biases apply
to both batch entries.

## Interface

### RTL interface

```systemverilog
module signal_processing_cnn_fc_arm_fully_connected_s16 (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [1:0] in_batches,
    input  logic [3:0] in_depth,
    input  logic [2:0] in_outputs,
    input  logic [255:0] in_input,
    input  logic [255:0] in_kernel,
    input  logic [255:0] in_bias,
    input  logic [30:0] in_multiplier,
    input  logic signed [5:0] in_shift,
    input  logic signed [15:0] in_activation_min,
    input  logic signed [15:0] in_activation_max,
    output logic out_valid,
    input  logic out_ready,
    output logic [127:0] out_output,
    output logic out_error
);
```

All packed arrays place element zero in the least-significant bits:

- `in_input`: up to 16 signed int16 activations, compact batch-major order.
- `in_kernel`: up to 32 signed int8 weights, compact output-major order.
- `in_bias`: four signed int64 biases, one per output channel.
- `in_multiplier`: nonnegative 31-bit integer; `in_shift`: signed 6-bit exponent.
- `in_activation_min/max`: signed int16 clipping bounds.
- `out_output`: up to eight signed int16 results, compact batch-major order.
  All unused output lanes are zero; unused input, weight and bias lanes are ignored.
- `out_error`: one for invalid dimensions, shift or clipping bounds.

An input transfers on a rising edge with `in_valid && in_ready` and produces
exactly one ordered response containing the full result batch. An output
transfers with `out_valid && out_ready`. A stalled valid output and its
payload remain stable until transfer. Latency is not prescribed.

### Behavior interface

```python
class SignalProcessingCnnFcArmFullyConnectedS16:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_batches, in_depth, in_outputs, in_input, in_kernel,
                in_bias, in_multiplier, in_shift, in_activation_min, in_activation_max): ...
```

Call `process(...)` once per accepted command. Arguments use raw unsigned port
encodings, including the 6-bit signed shift and 16-bit signed clipping bounds.
Every call returns exactly `{"out_output": result, "out_error": error}`:
`result` is an unsigned 128-bit packed output and `error` is an unsigned one-bit
integer. Invalid commands return `{"out_output": 0, "out_error": 1}`, not
`None`, an empty list, or a raised exception. All unused output lanes are zero.

There is no retained layer state between commands. Construction and `reset()`
establish the same stateless model; `reset()` returns `None` with no output record.

## Modules

- `signal_processing_cnn_fc_arm_fully_connected_s16`: batched matrix-vector
  multiplication, reduced-multiplier requantization and activation clipping.

## Functional Behavior

Let `B=in_batches`, `K=in_depth`, and `O=in_outputs`.
A command is valid when `1<=B<=2`, `1<=K<=8`, `1<=O<=4`,
`-31<=shift<=7`, and `activation_min<=activation_max`.
Invalid commands return an all-zero result with `out_error=1`.
Valid commands return `out_error=0` and the following calculation.

Decode activation `x[b,c]` from lane `b*K+c`, weight `w[r,c]` from lane
`r*K+c`, and bias `bias[r]` from lane `r`. For every `0<=b<B`, `0<=r<O`:

```text
reduced = min(floor((in_multiplier + 32768) / 65536), 32767)
acc[b,r] = bias[r] + sum(x[b,c] * w[r,c] for c = 0,...,K-1)
scaled[b,r] = floor((acc[b,r] * reduced + 2^(14-shift)) / 2^(15-shift))
y[b,r] = min(activation_max, max(activation_min, scaled[b,r]))
```

Store `y[b,r]` in output lane `b*O+r`. All intermediate operations use
mathematical integers without wrapping, including bias addition and
multiplication. The scaling rounds to nearest with ties toward positive
infinity: at scale one half, 3 becomes 2 and -3 becomes -1.
Activation clipping happens only after scaling. Zero multiplier produces
zero before clipping, which need not be zero after clipping.
There are no input, weight or output zero-point offsets.

For example, `B=1,K=2,O=2`, input [2,-3], weight rows [4,5] and [-1,2],
biases [1,-2], multiplier 0x40000000 and shift 1 produce [-6,-10]
with full-range activation bounds. Changing either input channel or either
weight row can change the corresponding result.

## Reset Behavior

Reset is synchronous active-high `rst`. A reset edge clears output
validity, output bits and error; `in_ready=1` in the reset state.
Any accepted response not yet transferred is cancelled and may not reappear.
Requests otherwise have no retained algorithmic state.
