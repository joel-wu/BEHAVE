# CausalDilatedConvBlock4

Streaming block update for a two-channel, dilation-two causal convolution.

## Interface

### RTL interface

```systemverilog
module CausalDilatedConvBlock4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_history,
    input logic [63:0] in_x,
    input logic [95:0] in_weights,
    output logic [319:0] out_values
);
endmodule
```

### Behavior interface

```python
class CausalDilatedConvBlock4:
    def reset(self): ...
    def process(self, in_history, in_x, in_weights): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 320.

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

`CausalDilatedConvBlock4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Streaming block update for a two-channel, dilation-two causal convolution.

History holds the four preceding time points, oldest first, each two signed INT8 channels. X holds four new time points in the same time-major layout. Concatenate H then X as S[0..7,0..1]. Weights are signed INT8 in OCK order, dimensions 2x2x3. Output Y[t,o]=sum(c,k) W[o,c,k]*S[t+2*k,c], for t=0..3. Thus k=2 addresses the current sample and k=0 the oldest tap (cross-correlation order). Return eight signed INT32 outputs time-major in low 256 bits, and the new four-point history X in bits 256..319. Feed this returned history to the next block; there is no hidden state, bias or saturation.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Zero history, all new samples 1 and all weights 1 give outputs [2,2,2,2,4,4,4,4]; the returned history is all ones.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
