# ViterbiK3Block8

Exact hard-decision Viterbi decoding of an eight-bit, rate-1/2 convolutional-code block.

## Interface

### RTL interface

```systemverilog
module ViterbiK3Block8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [15:0] in_codeword,
    output logic [12:0] out_values
);
endmodule
```

### Behavior interface

```python
class ViterbiK3Block8:
    def reset(self): ...
    def process(self, in_codeword): ...
```

Each call returns `{"out_values": packed_result}`.

Return exactly one dictionary on every call, never None or a list. All fields are
present even when a valid or status flag is zero.
Every returned dictionary contains exactly these fields (bit widths):

- `out_values`: 13.

Arguments and returned fields are nonnegative integers of their declared port widths.
Signed quantities use two's-complement bit encodings.
Supply all named process arguments on each call. Reuse the instance across
the input sequence. `reset()` establishes the specified initial model state,
returns `None`, and emits no record.

### Data representation

Ports carry unsigned
bit patterns; signed lanes use two's complement. Field positions are
defined below. Every input bit pattern has a defined result.

### Transaction protocol

Inputs transfer only on rising edges with `in_valid && in_ready`; outputs
transfer only with `out_valid && out_ready`. One accepted input produces
exactly one output, in acceptance order. Bubbles do not advance the logical
operator. Backpressure must not drop, duplicate or reorder work. A producer
keeps its valid signal and payload stable while stalled. The implementation
may deassert in_ready and use a serial or pipelined datapath; no fixed latency
or one-result-per-cycle throughput is required.

## Modules

`ViterbiK3Block8` is the only required public module. Its internal implementation
and latency are not prescribed.

## Functional Behavior

Exact hard-decision Viterbi decoding of an eight-bit, rate-1/2 convolutional-code block.

A candidate message is an unsigned byte m. Start the two-bit encoder history h=0. For t=0..7, consume b=(m>>(7-t))&1, set r=(h<<1)|b, emit c0=parity(r&7) and c1=parity(r&5), then set h=r&3. Coded bits c0,c1 occupy positions 2*t and 2*t+1 respectively. Thus message bits are processed MSB-first, while coded pairs are packed earliest-first at the low end. There are no tail bits and no final-state constraint.

Decode the byte whose 16-bit encoding has minimum Hamming distance from in_codeword. If several bytes achieve that distance, choose the numerically smallest byte (lexicographically smallest chronological input sequence). Return that byte in bits 7:0 and its Hamming distance, an unsigned count 0..16, in bits 12:8. Every received word has a result, even when errors cannot be uniquely corrected. Do not force the end history to zero, drop the last decisions, invert polynomial bits or discard ties.

This specializes CommPy hard-decision add/compare/select decoding to a finite four-state trellis with polynomials 7 and 5. Full-block survivor selection and the explicit lexicographic tie rule replace its configurable sliding traceback and implementation-dependent equal-metric survivor choice. Soft metrics and stream continuation are outside this task; each request starts from history zero.

No floating-point operations, approximated transcendental functions or host
language overflow assumptions are required. Any narrowing/modular operation
is stated above; otherwise arithmetic must retain the exact intermediate.

### Worked example

`in_codeword=0x0000` returns message 0, distance 0. The encoding of message 1 is `0xc000`, so that input returns `out_values=1` without forcing a zero end state. The encoding of message 0x80 is 0x0037.

## Reset Behavior

`rst` is synchronous, active high, and overrides transfers on its sampled
edge. It cancels all in-flight work and clears output valid. No pre-reset
result may reappear. The mathematical operator has no state between requests.
