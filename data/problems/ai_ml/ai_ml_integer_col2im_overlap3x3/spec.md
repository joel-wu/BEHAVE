# Col2ImOverlap3x3

Fold four overlapping 2x2 patches into a 3x3 single-channel image with stride 1, no padding and dilation 1. The sixteen input INT8 cells form an ONNX column matrix [kernel_element, patch_index] of shape [4,4]. Kernel element k=2*i+j and patch p=2*h+w are row-major; cell (k,p) occupies byte 4*k+p. It contributes to output location (h+i,w+j). Sum every overlapping contribution, do not average or overwrite. Output is all nine row-major signed INT16 sums; there is no overflow in this domain.

## Interface

### RTL interface

```systemverilog
module Col2ImOverlap3x3 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_columns,
    output logic [143:0] out_values
);
endmodule
```

### Behavior interface

```python
class Col2ImOverlap3x3:
    def reset(self): ...
    def process(self, in_columns): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 144.

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

`Col2ImOverlap3x3` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Fold four overlapping 2x2 patches into a 3x3 single-channel image with stride 1, no padding and dilation 1. The sixteen input INT8 cells form an ONNX column matrix [kernel_element, patch_index] of shape [4,4]. Kernel element k=2*i+j and patch p=2*h+w are row-major; cell (k,p) occupies byte 4*k+p. It contributes to output location (h+i,w+j). Sum every overlapping contribution, do not average or overwrite. Output is all nine row-major signed INT16 sums; there is no overflow in this domain.

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
