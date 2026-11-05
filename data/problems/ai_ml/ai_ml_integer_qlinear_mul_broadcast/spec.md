# QLinearMulBroadcast

Affine quantized mul with channel-vector broadcasting over a [2,4] tensor.

## Interface

### RTL interface

```systemverilog
module QLinearMulBroadcast (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_a,
    input logic [31:0] in_b,
    input logic [7:0] in_zero_a,
    input logic [7:0] in_zero_b,
    input logic [7:0] in_zero_y,
    input logic [2:0] in_shift_a,
    input logic [2:0] in_shift_b,
    output logic [63:0] out_values
);
endmodule
```

### Behavior interface

```python
class QLinearMulBroadcast:
    def reset(self): ...
    def process(self, in_a, in_b, in_zero_a, in_zero_b, in_zero_y, in_shift_a, in_shift_b): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 64.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Data buses are unsigned
bit patterns; each signed lane is two's complement. Lane i occupies
`[bits_per_lane*i +: bits_per_lane]`; flat indices are zero-based. Any unused output slots are zero as defined below. Every input bit pattern has a defined result.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`QLinearMulBroadcast` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Affine quantized mul with channel-vector broadcasting over a [2,4] tensor.

All values and zero-points are signed INT8. RNE(x/2^e) rounds to the nearest integer, ties to the even integer, before adding the output zero-point; Clip8 saturates to [-128,127]. There is no intermediate saturation or double rounding. A has shape [2,4], B shape [4]. Dequantized A=(a-za)/2^shift_a and B=(b-zb)/2^shift_b. Output scale is one and output shape is [2,4]; its value is Clip8(RNE(A * B)+zy). The three zero points are independently supplied; shifts range 0..7. B[c] is reused by both rows. This is DequantizeLinear → Mul → QuantizeLinear with exact dyadic scales.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

With zero points zero, shifts (1,1), a=3,b=2 give RNE(1.5)=2; a=-3,b=2 give -2. Saturation is applied only after the output zero-point.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
