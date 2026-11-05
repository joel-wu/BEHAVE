# GridSampleQ4

Bilinear sampling of an NCHW [1,2,3,3] signed INT8 feature map using four independent grid points.

## Interface

### RTL interface

```systemverilog
module GridSampleQ4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [143:0] in_data,
    input logic [63:0] in_grid,
    input logic [0:0] in_align_corners,
    input logic [0:0] in_border,
    output logic [127:0] out_values
);
endmodule
```

### Behavior interface

```python
class GridSampleQ4:
    def reset(self): ...
    def process(self, in_data, in_grid, in_align_corners, in_border): ...
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
`[bits_per_lane*i +: bits_per_lane]`; flat indices are zero-based. Unused
padding bits do not exist. Every input bit pattern has a defined result.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`GridSampleQ4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Bilinear sampling of an NCHW [1,2,3,3] signed INT8 feature map using four independent grid points.

Grid is [1,2,2,2]: each point is (gx,gy), each coordinate a signed INT8 raw value representing raw/16. Input feature values are integers, not normalized bytes. Output is NCHW [1,2,2,2], eight signed INT16 lanes representing sampled values in units 1/256. For a coordinate g=raw/16, if in_align_corners=1 use p=(g+1)*(3-1)/2; otherwise p=((g+1)*3-1)/2. Compute px from gx and py from gy. Set ix=floor(px), iy=floor(py), fx=px-ix, fy=py-iy. The four samples at (iy,ix),(iy,ix+1),(iy+1,ix),(iy+1,ix+1) have weights (1-fy)*(1-fx),(1-fy)*fx,fy*(1-fx),fy*fx. If in_border=0, every individually out-of-range neighbor contributes zero. If in_border=1, clamp each neighbor index independently to [0,2] before reading. Return 256 times the exact weighted sum, rounded once to nearest integer with ties away from zero. Do not round between the horizontal and vertical interpolations; do not truncate negative coordinates toward zero. All output lanes fit signed INT16. This specializes ONNX linear GridSample to two dimensions, zeros/border padding and exact finite fixed-point coordinates; cubic, nearest and reflection modes are outside this task.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked packing example

A byte sequence `[1,2,3,4]` is packed as `0x04030201`; the low byte is element
zero. Signed byte `-1` is `0xff`. This example specifies packing, not a
restriction to small positive inputs.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
