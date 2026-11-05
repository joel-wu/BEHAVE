# Sparsemax8Q8

Project eight Q8 logits onto the probability simplex using Sparsemax, with explicit final Q16 rounding.

## Interface

### RTL interface

```systemverilog
module Sparsemax8Q8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    output logic [143:0] out_values
);
endmodule
```

### Behavior interface

```python
class Sparsemax8Q8:
    def reset(self): ...
    def process(self, in_data): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 144.

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

`Sparsemax8Q8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Project eight Q8 logits onto the probability simplex using Sparsemax, with explicit final Q16 rounding.

Input raw x[i] is signed INT16, representing x[i]/256. Sort raw values descending as z[1..8]. Let k be the largest j with 256+j*z[j]>sum(z[1..j]); k always exists. Let S=sum(z[1..k]). The exact probability is p[i]=max(0,(k*x[i]-S+256)/(256*k)). Output unsigned 17-bit lane i is nearest-even(65536*p[i]), at bit 17*i. Bits 136+i record exact positive support (p[i]>0), before rounding. No softmax/exponential is used. Independent final lane rounding may change the sum of stored probabilities slightly; do not renormalize it. This preserves entmax Sparsemax threshold/support semantics with fixed Q8 inputs and Q16 boundary encoding.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

Eight equal logits return eight raw probabilities 8192 and support mask 0xff. Logits [256,0,0,0,0,0,0,0] return [65536,0,0,0,0,0,0,0] with support mask 1.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
