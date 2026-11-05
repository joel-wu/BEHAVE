# HardSwishQ8

Apply hard-swish independently to eight signed INT16 Q8.8 lanes. For a raw lane X set T=min(1536,max(0,X+768)). The output raw integer is nearest(X*T/1536), with exact half values away from zero. Compute the product exactly and round once; do not round the gate first. This is x*clip(x/6+1/2,0,1) in Q8.8. Every output fits signed INT16; there is no wrap or saturation. Values below -3 map to zero, and values above +3 pass through.

## Interface

### RTL interface

```systemverilog
module HardSwishQ8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    output logic [127:0] out_values
);
endmodule
```

### Behavior interface

```python
class HardSwishQ8:
    def reset(self): ...
    def process(self, in_data): ...
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

`HardSwishQ8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Apply hard-swish independently to eight signed INT16 Q8.8 lanes. For a raw lane X set T=min(1536,max(0,X+768)). The output raw integer is nearest(X*T/1536), with exact half values away from zero. Compute the product exactly and round once; do not round the gate first. This is x*clip(x/6+1/2,0,1) in Q8.8. Every output fits signed INT16; there is no wrap or saturation. Values below -3 map to zero, and values above +3 pass through.

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
