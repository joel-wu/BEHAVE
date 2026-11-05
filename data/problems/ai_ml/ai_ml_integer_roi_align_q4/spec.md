# RoiAlignQ4

Average RoIAlign on an NCHW [1,2,4,4] signed INT8 feature map, with two regions and 2x2 pooled output bins.

## Interface

### RTL interface

```systemverilog
module RoiAlignQ4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_data,
    input logic [63:0] in_rois,
    output logic [255:0] out_values
);
endmodule
```

### Behavior interface

```python
class RoiAlignQ4:
    def reset(self): ...
    def process(self, in_data, in_rois): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 256.

Process arguments and returned fields are nonnegative integers of their declared port
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

`RoiAlignQ4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Average RoIAlign on an NCHW [1,2,4,4] signed INT8 feature map, with two regions and 2x2 pooled output bins.

Each region has (x1,y1,x2,y2) signed INT8 raw coordinates representing raw/16; regions are packed consecutively. Use ONNX coordinate_transformation_mode=output_half_pixel (no half-pixel subtraction), spatial_scale=1, sampling_ratio=2, mode=avg and batch index zero. Let w=max(x2-x1,1), h=max(y2-y1,1), using real coordinate values. Do not reorder reversed endpoints: malformed or zero-size intervals start at (x1,y1) and have extent at least one. For bin (by,bx) in {0,1}^2 and samples (sy,sx) in {0,1}^2, sample px=x1+(bx+(sx+1/2)/2)*w/2 and py=y1+(by+(sy+1/2)/2)*h/2. If px<-1 or px>4 or py<-1 or py>4, the sample is zero. Otherwise clamp px,py to [0,3] and bilinearly interpolate the four neighboring pixels (the last row/column may be used twice). This [-1,4] boundary rule differs from GridSample zero padding. Average all four exact samples, including zero samples. Output [2,2,2,2] is region/channel/bin_y/bin_x order, sixteen signed INT16 lanes in units 1/256, rounding once at the final bin value to nearest with ties away from zero. No per-sample rounding or integer RoI quantization is allowed. This is an exact rational/fixed-point specialization, not an assertion about source floating-point rounding for arbitrary shapes.

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
