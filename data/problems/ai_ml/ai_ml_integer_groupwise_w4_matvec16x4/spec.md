# GroupwiseW4MatVec16x4

Blocked-affine INT4 weights times an INT8 activation vector, with four output channels.

## Interface

### RTL interface

```systemverilog
module GroupwiseW4MatVec16x4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    input logic [255:0] in_weights,
    input logic [7:0] in_zero_x,
    input logic [63:0] in_zero_w,
    input logic [63:0] in_scales,
    input logic [63:0] in_bias,
    output logic [127:0] out_values
);
endmodule
```

### Behavior interface

```python
class GroupwiseW4MatVec16x4:
    def reset(self): ...
    def process(self, in_data, in_weights, in_zero_x, in_zero_w, in_scales, in_bias): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 128.

Arguments and returned fields are nonnegative integers of their declared port
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

`GroupwiseW4MatVec16x4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Blocked-affine INT4 weights times an INT8 activation vector, with four output channels.

X contains 16 signed bytes. W contains [4,16] signed four-bit weights, output-channel major. Each row has four consecutive groups of four weights; zero_w[o,g] is signed INT4 and scale[o,g]=1+unsigned(in_scales[o,g]), so scales are integers 1..16. Both metadata arrays flatten as 4*o+g. Activation zero_x is signed INT8; bias has four signed INT16 lanes. Return four exact INT32 values Y[o]=bias[o]+sum(g,scale[o,g]*sum(k=0..3,(X[4*g+k]-zx)*(W[o,4*g+k]-zw[o,g]))). Do not merge group sums before applying their distinct scales, and do not saturate or wrap intermediate terms. This is blocked DequantizeLinear followed by matrix multiplication, specialized to integer scales; it is not the source floating-point dtype implementation.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

For X and W all one, zero zero-points/bias and group scales [1,2,3,4] on every row, each output is 4*(1+2+3+4)=40. Scale bus nibbles encode [0,1,2,3].

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
