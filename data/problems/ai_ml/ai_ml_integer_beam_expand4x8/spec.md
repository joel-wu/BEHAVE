# BeamExpand4x8

One exact beam-expansion selection step, retaining live continuations and a separate terminal candidate.

## Interface

### RTL interface

```systemverilog
module BeamExpand4x8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [63:0] in_beam_scores,
    input logic [511:0] in_token_scores,
    input logic [3:0] in_beam_mask,
    input logic [2:0] in_eos,
    output logic [110:0] out_values
);
endmodule
```

### Behavior interface

```python
class BeamExpand4x8:
    def reset(self): ...
    def process(self, in_beam_scores, in_token_scores, in_beam_mask, in_eos): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 111.

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

`BeamExpand4x8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

One exact beam-expansion selection step, retaining live continuations and a separate terminal candidate.

There are four parent beams and eight tokens. Parent scores and per-parent token increments are signed INT16 in common units. Parent b has score at bits 16*b of in_beam_scores and is valid iff bit b of in_beam_mask is one. Its token t increment occupies bits 16*(8*b+t) of in_token_scores (parent-major order). in_eos is the EOS token index. Valid parents expand to score=parent+increment (signed INT17). Select the best four non-EOS expansions, score descending, ties by parent then token ascending. Each 22-bit slot packs parent[1:0], token[4:2], score[21:5]; count occupies bits 88..90. Separately return the best EOS expansion, ties by lower parent index: parent at 91..92, score at 93..109, valid at 110. No valid parents returns all zero. Unused live slots are zero. This specializes beam candidate ranking; there is no softmax, length penalty, cross-step terminal archive or complete generation loop.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

All scores zero, only parent 0 valid, EOS=0: live choices are (0,1),(0,2),(0,3),(0,4), all score zero. The separate EOS candidate is parent 0, score zero, valid.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
