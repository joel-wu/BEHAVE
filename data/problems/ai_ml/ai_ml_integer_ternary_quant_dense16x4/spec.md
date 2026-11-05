# TernaryQuantDense16x4

Per-row absmax ternary quantization followed by dense integer inference, returning quantized weights too.

## Interface

### RTL interface

```systemverilog
module TernaryQuantDense16x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_x,
    input logic [511:0] in_weights,
    output logic [287:0] out_values
);
endmodule
```

### Behavior interface

```python
class TernaryQuantDense16x4:
    def reset(self): ...
    def process(self, in_x, in_weights): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 288.

Process arguments and returned fields are nonnegative integers of their declared port
widths. Signed quantities use two's-complement bit encodings.
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

`TernaryQuantDense16x4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Per-row absmax ternary quantization followed by dense integer inference, returning quantized weights too.

X has sixteen signed INT8 values. Raw weights are 4x16 signed INT8, row-major. For each row, d=max(abs(w)); q=nearest-away(w/d) in {-1,0,1} if d>0, otherwise q=0. Return y[o]=d*sum(q[o,c]*X[c]) in four signed INT32 lanes at bit 32*o, four unsigned eight-bit scales d at bit 128+8*o, and 64 two-bit codes q+1 at bit 160+2*(16*o+c). Code 3 is never produced. This specializes GGML TQ2 absmax/ternary semantics to blocks of 16 instead of 256, exact integer scales instead of stored FP16, and straightforward row-major packing rather than the GGUF storage ABI. Halfway weights round away from zero.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

With X all 1 and each weight row [2,1,-1,-2,0,...,0], d=2, q=[1,1,-1,-1,0,...,0], y=0. The zero row has d=0, all codes 1, y=0.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
