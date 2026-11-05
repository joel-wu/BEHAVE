# RepetitionPenalty16

Sign-aware repetition penalty on a 16-token vocabulary, applied once per distinct seen token.

## Interface

### RTL interface

```systemverilog
module RepetitionPenalty16 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_tokens,
    input logic [3:0] in_length,
    input logic [255:0] in_scores,
    output logic [271:0] out_values
);
endmodule
```

### Behavior interface

```python
class RepetitionPenalty16:
    def reset(self): ...
    def process(self, in_tokens, in_length, in_scores): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 272.

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

`RepetitionPenalty16` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Sign-aware repetition penalty on a 16-token vocabulary, applied once per distinct seen token.

L=min(8,in_length); use only the first L four-bit token slots. For each seen token with signed INT16 score s, output nearest-even(3*s/2) if s<0, otherwise nearest-even(2*s/3), then saturate to INT16. Unseen scores pass through. Return the 16 adjusted lanes and a 16-bit seen mask at bit 256. Repeated occurrences do not compound the penalty. This is Transformers repetition penalty 1.5 specialized to integer score storage; it is not frequency or presence penalty.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Seen scores [-3,-1,0,3] become [-4,-2,0,2] under nearest-even rounding; seeing a token twice gives the same result as once.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
