# NMS4U8

Perform greedy non-maximum suppression on all four boxes of one image/class. Each box occupies four unsigned bytes [y0,x0,y1,x1] in the low-to-high order; scores are four signed INT8 lanes. Normalize reversed endpoints by min/max on each axis. Process boxes by descending score, breaking ties by lower original index. There is no score cutoff. Keep a box unless an already kept box has IoU strictly greater than in_threshold/255; equality does NOT suppress. Areas use continuous corner coordinates (width=xmax-xmin, no +1); zero-area boxes and zero intersection never suppress. Compare positive areas exactly as 255*intersection > in_threshold*union, without floating-point division. Return up to four selected original indices in selection order, two bits each in bits [7:0]; zero unused index slots. Bits [10:8] contain count. Finite coordinates, exact rational IoU and packed indices specialize the source NMS operation; arbitrary batches/classes, center/width boxes and float32 rounding at the threshold are outside this task.

## Interface

### RTL interface

```systemverilog
module NMS4U8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_boxes,
    input logic [31:0] in_scores,
    input logic [7:0] in_threshold,
    output logic [10:0] out_values
);
endmodule
```

### Behavior interface

```python
class NMS4U8:
    def reset(self): ...
    def process(self, in_boxes, in_scores, in_threshold): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 11.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
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

`NMS4U8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Perform greedy non-maximum suppression on all four boxes of one image/class. Each box occupies four unsigned bytes [y0,x0,y1,x1] in the low-to-high order; scores are four signed INT8 lanes. Normalize reversed endpoints by min/max on each axis. Process boxes by descending score, breaking ties by lower original index. There is no score cutoff. Keep a box unless an already kept box has IoU strictly greater than in_threshold/255; equality does NOT suppress. Areas use continuous corner coordinates (width=xmax-xmin, no +1); zero-area boxes and zero intersection never suppress. Compare positive areas exactly as 255*intersection > in_threshold*union, without floating-point division. Return up to four selected original indices in selection order, two bits each in bits [7:0]; zero unused index slots. Bits [10:8] contain count. Finite coordinates, exact rational IoU and packed indices specialize the source NMS operation; arbitrary batches/classes, center/width boxes and float32 rounding at the threshold are outside this task.

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
