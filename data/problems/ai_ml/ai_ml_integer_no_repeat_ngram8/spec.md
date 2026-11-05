# NoRepeatNGram8

No-repeat n-gram constraint for one bounded decoding history.

## Interface

### RTL interface

```systemverilog
module NoRepeatNGram8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [31:0] in_tokens,
    input logic [3:0] in_length,
    input logic [1:0] in_ngram_minus1,
    input logic [255:0] in_scores,
    output logic [271:0] out_values
);
endmodule
```

### Behavior interface

```python
class NoRepeatNGram8:
    def reset(self): ...
    def process(self, in_tokens, in_length, in_ngram_minus1, in_scores): ...
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

`NoRepeatNGram8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

No-repeat n-gram constraint for one bounded decoding history.

History is eight four-bit token slots; L=min(8,in_length), n=in_ngram_minus1+1 (1..4). Ban token v exactly if appending v repeats an n-gram already present in history[0:L]. For n=1, ban all seen tokens. If L+1<n, ban none. Return the 16 signed INT16 score lanes unchanged for unbanned tokens and zero for banned tokens; bits 256+v hold the ban mask. The mask is authoritative: zero is not a finite substitute for negative infinity in downstream selection. Unused history slots do not participate.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

History [1,2,1] with n=2 bans token 2 only, because the suffix [1] previously led to 2.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
