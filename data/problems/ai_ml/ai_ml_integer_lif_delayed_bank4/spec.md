# LIFDelayedBank4

Four independent leaky integrate-and-fire neurons with signed INT16 persistent membrane M[i] (units 1/256), initially zero. Every accepted input supplies four signed INT16 currents I, four unsigned beta bytes B representing B/256, and four unsigned 16-bit thresholds T=max(1,threshold_lane). Read old membranes, or zeros if in_clear=1. For each lane, old_spike=1 iff old M>T (strict, using this request's threshold). Let L=round_nearest_ties_away(B*M/256). Compute U=clip(L+I-old_spike*T,-32768,32767). Store U and emit a spike iff U>T. A spike therefore subtracts a threshold on the NEXT accepted step, not this one; at most one threshold is subtracted per step, even for a large membrane. There is no refractory period or clock-driven leak. Output bits 63:0 hold four stored membranes; bits 67:64 hold the four spikes. This preserves snnTorch Leaky's default delayed subtract-reset order. Fixed-point leak rounding, finite saturation, packed four lanes and positive-threshold normalization are declared adaptations, not floating-point bit identity. All parameters are per-request; in_clear discards old membranes before integrating the supplied currents.

## Interface

### RTL interface

```systemverilog
module LIFDelayedBank4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_current,
    input logic [31:0] in_beta,
    input logic [63:0] in_threshold,
    input logic [0:0] in_clear,
    output logic [67:0] out_values
);
endmodule
```

### Behavior interface

```python
class LIFDelayedBank4:
    def reset(self): ...
    def process(self, in_current, in_beta, in_threshold, in_clear): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 68.

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

`LIFDelayedBank4` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Four independent leaky integrate-and-fire neurons with signed INT16 persistent membrane M[i] (units 1/256), initially zero. Every accepted input supplies four signed INT16 currents I, four unsigned beta bytes B representing B/256, and four unsigned 16-bit thresholds T=max(1,threshold_lane). Read old membranes, or zeros if in_clear=1. For each lane, old_spike=1 iff old M>T (strict, using this request's threshold). Let L=round_nearest_ties_away(B*M/256). Compute U=clip(L+I-old_spike*T,-32768,32767). Store U and emit a spike iff U>T. A spike therefore subtracts a threshold on the NEXT accepted step, not this one; at most one threshold is subtracted per step, even for a large membrane. There is no refractory period or clock-driven leak. Output bits 63:0 hold four stored membranes; bits 67:64 hold the four spikes. This preserves snnTorch Leaky's default delayed subtract-reset order. Fixed-point leak rounding, finite saturation, packed four lanes and positive-threshold normalization are declared adaptations, not floating-point bit identity. All parameters are per-request; in_clear discards old membranes before integrating the supplied currents.

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
result may reappear. All four membranes reset to zero. Neuron time advances only with accepted inputs; backpressure and bubbles are not neuron steps.
