# SignalProcessingCnnActivationArmRelu6S8

This block computes the signed 8-bit ReLU6 activation on one input sample per accepted transaction. For each input element, it interprets the value as a two's-complement 8-bit signed integer, clamps negative values to 0, clamps positive values greater than 6 to 6, and returns the clamped result as an 8-bit output. The computation is stateless and equivalent to the scalar operation used in an elementwise ReLU6 pass over an s8 tensor.

## Interface

### RTL interface

```systemverilog
module signal_processing_cnn_activation_arm_relu6_s8 (
    input  logic       clk,
    input  logic       rst,

    input  logic       in_valid,
    output logic       in_ready,
    input  logic [7:0] in_data,

    output logic       out_valid,
    input  logic       out_ready,
    output logic [7:0] out_data
);
```

### Behavior interface

```python
class SignalProcessingCnnActivationArmRelu6S8:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_data): ...
```

Each call represents one accepted sample and returns exactly
`{"out_data": value}`. `in_data` is an integer in 0..255 encoding the signed
8-bit input; `value` is an integer in 0..6 carried in an 8-bit word. Negative
and zero samples still return this dictionary, never `None` or a list. No
handshake signals are returned. Calls retain no sample history. Construction
and `reset()` leave this stateless mapping unchanged; `reset()` returns `None`.

## Modules

- `signal_processing_cnn_activation_arm_relu6_s8`: top-level module implementing a single-sample signed 8-bit ReLU6 clamp.

## Functional Behavior

`in_data` is an 8-bit two's-complement signed integer. Let:

- `x = signed(in_data)` in the range `[-128, 127]`
- `y0 = 0` if `x < 0`, else `x`
- `y1 = 6` if `y0 > 6`, else `y0`

The output transaction is:

- `out_data = unsigned_8bit(y1)`

Equivalently, `y1 = min(max(x, 0), 6)` with both comparisons performed in signed integer arithmetic.

The returned output encoding is the 8-bit two's-complement representation of `y1`. Because `y1` is always in the range `[0, 6]`, the output bit pattern is always one of `8'h00` through `8'h06`.

Examples:

- `in_data = 8'h80` (`x = -128`) -> `out_data = 8'h00`
- `in_data = 8'hFF` (`x = -1`) -> `out_data = 8'h00`
- `in_data = 8'h00` (`x = 0`) -> `out_data = 8'h00`
- `in_data = 8'h05` (`x = 5`) -> `out_data = 8'h05`
- `in_data = 8'h06` (`x = 6`) -> `out_data = 8'h06`
- `in_data = 8'h07` (`x = 7`) -> `out_data = 8'h06`
- `in_data = 8'h7F` (`x = 127`) -> `out_data = 8'h06`

The block has no architectural state other than any handshake buffering required by the implementation. No accumulation, vector length tracking, or frame semantics are present; each accepted input sample is processed independently and produces exactly one output sample.

## Reset Behavior

`rst` is active-high. Reset may be implemented synchronously or asynchronously. After reset, the block has no retained data-state.

Architectural state after reset:

- no internal data state

Output requirements after reset:

- `out_data = 8'h00` whenever `out_valid = 0`
- if the implementation uses no internal buffering, it may deassert `out_valid` during and immediately after reset and assert `in_ready` whenever able to accept a new input
- any internal handshake state used to support stalling shall be initialised empty, such that no pre-reset input can produce a post-reset output
