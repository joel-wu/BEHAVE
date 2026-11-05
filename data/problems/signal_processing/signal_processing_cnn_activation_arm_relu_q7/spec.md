# SignalProcessingCnnActivationArmReluQ7

This module applies the rectified linear unit activation function to one signed 8-bit Q7 input sample per accepted transaction. The computation is pointwise and stateless: negative inputs are replaced with zero, and non-negative inputs pass through unchanged. Arithmetic is integer two's-complement on 8-bit data; no scaling, rounding, saturation beyond the output bit width, or block framing is performed.

## Interface

### RTL interface

```systemverilog
module signal_processing_cnn_activation_arm_relu_q7 (
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
class SignalProcessingCnnActivationArmReluQ7:
    def __init__(self): ...
    def reset(self): ...
    def process(self, in_data): ...
```

Call `process` once per accepted input. It returns one dictionary
`{"out_data": ...}`, whose value is a Python integer holding the specified
output port bit pattern.

## Modules

- `signal_processing_cnn_activation_arm_relu_q7`: top-level module that performs one Q7 ReLU transform per accepted input transaction.

## Functional Behavior

`in_data` is an 8-bit two's-complement signed integer encoded on an unsigned 8-bit port. Let:

- `x = signed(in_data)` interpreted in the range -128 to +127.
- `y = 0` if `x < 0`.
- `y = x` if `x >= 0`.

The module emits one output transaction for every accepted input transaction. `out_data` is the 8-bit two's-complement encoding of `y`.

Equivalent bit-level behavior:

- If `in_data[7] == 1`, then `out_data = 8'h00`.
- If `in_data[7] == 0`, then `out_data = in_data`.

There is no special-case behavior for any individual Q7 value:

- `in_data = 8'h80` (-128) produces `out_data = 8'h00`.
- `in_data = 8'hFF` (-1) produces `out_data = 8'h00`.
- `in_data = 8'h00` (0) produces `out_data = 8'h00`.
- `in_data = 8'h7F` (+127) produces `out_data = 8'h7F`.

The design is stateless at the architectural level. The handshake may introduce latency or backpressure, but the input-to-output mapping is exactly one output sample per input sample with the transform defined above.

## Reset Behavior

Reset is active-high. Reset may be implemented synchronously or asynchronously, provided the externally visible reset state matches this section.

There is no architectural data state.

During reset:
- `in_ready = 0`
- `out_valid = 0`
- `out_data = 8'h00`

After reset is deasserted, the module may begin accepting input transactions and producing outputs according to the handshake protocol and the functional behavior above.
